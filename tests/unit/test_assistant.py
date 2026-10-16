"""L'assistant agit sur le plan : intentions typées, gestes calculés, commits annulables."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.adapters.remote.assistant import AssistantParseError, parse_reply
from openplan.pipeline.assistant import act, resolve_target
from openplan.scene import history
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas import Furniture, Source
from openplan.schemas.assistant import ASSISTANT_ACTOR, AssistantIntent, AssistantReply
from openplan.schemas.common import Point2
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import check_layout
from tests.factories import only_level, rect_room_project

CATEGORIES = load_catalog().categories


def _furniture(project) -> list[Furniture]:  # type: ignore[no-untyped-def]
    return [e for e in only_level(project).elements.values() if isinstance(e, Furniture)]


def _reply(*intents: AssistantIntent) -> AssistantReply:
    return AssistantReply(intents=list(intents), message="")


def test_parse_reply_is_tolerant() -> None:
    text = """```json
    {"intents": [
      {"kind": "add_furniture", "category": "desk", "near": ["window"], "x": 1.2},
      {"kind": "add_furniture", "category": "piano"},
      {"kind": "teleport", "target": "lit"},
      {"kind": "move_furniture", "target": "le lit", "away_from": ["door_swing"],
       "dimensions_m": {"width": 1.6}},
      {"kind": "ask", "text": "Quel bureau ?"}
    ], "message": "Je pose le bureau près de la fenêtre."}
    ```"""
    reply = parse_reply(text, CATEGORIES, "mets un bureau près de la fenêtre et éloigne le lit")
    kinds = [i.kind for i in reply.intents]
    assert kinds == ["add_furniture", "move_furniture", "ask"]
    assert reply.intents[0].near == ["window"] and reply.intents[1].target == "le lit"
    assert reply.intents[1].dimensions_m is None, "1,6 m n'a pas été écrit par l'utilisateur"
    assert reply.message.startswith("Je pose")
    assert any("position" in d for d in reply.dropped)
    assert any("piano" in d for d in reply.dropped)
    assert any("teleport" in d for d in reply.dropped)
    with pytest.raises(AssistantParseError):
        parse_reply("je ne sais pas", CATEGORIES, "…")
    # Le même repère en near et away (« loin de la fenêtre » mal rendu) : « loin » l'emporte.
    both = parse_reply(
        '{"intents": [{"kind": "move_furniture", "target": "bureau", '
        '"near": ["window"], "away_from": ["window"]}]}',
        CATEGORIES,
        "éloigne le bureau de la fenêtre",
    )
    assert both.intents[0].near == [] and both.intents[0].away_from == ["window"]


def test_add_move_remove_as_assistant_commits(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    cursor = project.history_cursor
    after, turn = act(
        project,
        level_id,
        "mets un bureau près de la fenêtre",
        _reply(AssistantIntent(kind="add_furniture", category="desk", near=["window"])),
        tmp_path,
    )
    desks = [f for f in _furniture(after) if f.category == "desk"]
    assert len(desks) == 1 and desks[0].source is Source.GENERATED
    assert turn.committed == 1 and after.history_cursor == cursor + 1
    assert after.history[-1].actor == ASSISTANT_ACTOR and "ajouter" in after.history[-1].label
    assert turn.highlighted == [desks[0].id] and "posé" in turn.message
    assert not [v for v in check_layout(after) if v.severity == "high"]
    assert validate_invariants(after).valid
    # Ctrl+Z retire le geste de l'assistant.
    assert not [f for f in _furniture(history.undo(after)) if f.category == "desk"]

    # Déplacer « le bureau » loin de la fenêtre : même meuble, autre position.
    before_pos = desks[0].transform.position
    moved, turn = act(
        after,
        level_id,
        "éloigne le bureau de la fenêtre",
        _reply(AssistantIntent(kind="move_furniture", target="le bureau", away_from=["window"])),
        tmp_path,
    )
    desk = next(f for f in _furniture(moved) if f.category == "desk")
    assert desk.id == desks[0].id and desk.transform.position != before_pos
    assert turn.actions[0].status == "done" and moved.history[-1].actor == ASSISTANT_ACTOR

    # Retirer un meuble généré : fait ; retirer un meuble posé par l'utilisateur : refusé, dit.
    removed, turn = act(
        moved,
        level_id,
        "enlève le bureau",
        _reply(AssistantIntent(kind="remove_furniture", target="bureau")),
        tmp_path,
    )
    assert not [f for f in _furniture(removed) if f.category == "desk"]
    assert turn.committed == 1
    mine = apply_op(
        removed, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    kept, turn = act(
        mine,
        level_id,
        "enlève le bureau",
        _reply(AssistantIntent(kind="remove_furniture", target="bureau")),
        tmp_path,
    )
    assert kept is mine and turn.actions[0].status == "refused"
    assert "posé par vous" in turn.actions[0].reason and "Suppr" in turn.actions[0].reason


def test_ambiguous_target_asks_and_unknown_is_said(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    for x in (1.0, 3.0):
        project = apply_op(
            project, level_id, AddFurniture(asset_id="nightstand", position=Point2(x=x, y=0.5))
        )
    resolved = resolve_target(project, level_id, "la table de chevet")
    assert len(resolved.furniture) == 2
    same, turn = act(
        project,
        level_id,
        "déplace la table de chevet",
        _reply(AssistantIntent(kind="move_furniture", target="la table de chevet", near=["door"])),
        tmp_path,
    )
    assert same is project and turn.committed == 0
    assert turn.actions[0].status == "question" and turn.message.startswith("Lequel")
    assert len(turn.actions[0].element_ids) == 2
    assert "(à gauche)" in turn.message and "(à droite)" in turn.message
    # « de gauche » lève l'ambiguïté : celle au plus petit x.
    left = resolve_target(project, level_id, "la table de chevet de gauche")
    assert len(left.furniture) == 1 and left.furniture[0].transform.position.x == pytest.approx(1.0)
    # Cible rendue par le modèle en snake_case : comprise quand même.
    assert len(resolve_target(project, level_id, "table_de_chevet").furniture) == 2
    nothing, turn = act(
        project,
        level_id,
        "enlève le piano",
        _reply(AssistantIntent(kind="remove_furniture", target="le piano")),
        tmp_path,
    )
    assert nothing is project and turn.actions[0].status == "refused"
    assert "je ne trouve pas" in turn.message
    # Déplacer sans dire où : question.
    _, turn = act(
        project,
        level_id,
        "déplace la table de chevet de gauche",
        _reply(AssistantIntent(kind="move_furniture", target="chevet de gauche")),
        tmp_path,
    )
    assert turn.actions[0].status == "question" and "Où mettre" in turn.message


def test_trap_and_no_room_are_refused_not_forced(tmp_path: Path) -> None:
    """« Mets le lit dans le mur » : le modèle ne peut pas le dire (pas de coordonnée) ; un
    lit de plus quand la pièce est pleine est refusé avec le motif."""
    project = rect_room_project()
    level_id = only_level(project).id
    current = project
    refused = None
    for _ in range(8):
        current, turn = act(
            current,
            level_id,
            "ajoute une armoire",
            _reply(AssistantIntent(kind="add_furniture", category="wardrobe")),
            tmp_path,
        )
        if turn.actions[0].status == "refused":
            refused = turn
            break
    assert refused is not None, "la pièce finit par être pleine"
    assert "pas de place" in refused.actions[0].summary
    assert not [v for v in check_layout(current) if v.severity == "high"]


def test_propose_layouts_and_answer(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    same, turn = act(
        project,
        level_id,
        "propose-moi trois aménagements avec un lit et un bureau",
        _reply(
            AssistantIntent(kind="propose_layouts", category="bed_double"),
            AssistantIntent(kind="answer", text="Voici trois idées."),
        ),
        tmp_path,
    )
    assert same is project and turn.committed == 0
    assert 1 <= len(turn.proposals) <= 3 and all(p.valid for p in turn.proposals)
    assert "disposition" in turn.message and "Voici trois idées." in turn.message
    # Sans meuble ni catégorie : question.
    _, turn = act(
        project, level_id, "propose", _reply(AssistantIntent(kind="propose_layouts")), tmp_path
    )
    assert turn.actions[0].status == "question"
