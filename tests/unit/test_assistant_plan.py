"""L'assistant sur le plan lui-même (B4) : murs, dimensions, ouvertures, pièce, mémoire."""

from __future__ import annotations

import math
from itertools import pairwise
from pathlib import Path

import pytest

from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.assistant import act
from openplan.pipeline.assistant_phrases import understand
from openplan.pipeline.assistant_plan import resolve_wall
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas import Opening, OpeningKind, Room, Source, Wall
from openplan.schemas.assistant import ASSISTANT_ACTOR, AssistantIntent, AssistantReply
from openplan.schemas.common import Point2
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level, rect_room_project

CATEGORIES = load_catalog().categories


def _reply(*intents: AssistantIntent) -> AssistantReply:
    return AssistantReply(intents=list(intents))


def _walls(project) -> dict[str, Wall]:  # type: ignore[no-untyped-def]
    level = only_level(project)
    graph = WallGraph.from_level(level)
    out = {}
    for w in graph.walls.values():
        a, b = graph.wall_axis(w.id)
        key = "h" if abs(b.y - a.y) < 1e-6 else "v"
        out[f"{key}{len([k for k in out if k.startswith(key)])}"] = w
    return out


def test_room_dimensions_go_through_wall_lengths(tmp_path: Path) -> None:
    """« La pièce fait 5 m sur 3,5 » : longueurs des murs, jamais une coordonnée ; la pièce
    reste rectangulaire et cohérente ; commits attribués à l'assistant, annulables."""
    project = rect_room_project()  # 4 × 3
    level_id = only_level(project).id
    after, turn = act(
        project,
        level_id,
        "la pièce fait 5 m sur 3,5",
        _reply(
            AssistantIntent(kind="set_dimension", dimension="length", value_m=5.0),
            AssistantIntent(kind="set_dimension", dimension="width", value_m=3.5),
        ),
        tmp_path,
    )
    assert [a.status for a in turn.actions] == ["done", "done"], turn.message
    graph = WallGraph.from_level(only_level(after))
    lengths = sorted(round(graph.wall_length(w), 2) for w in graph.walls)
    assert lengths == pytest.approx([3.5, 3.5, 5.0, 5.0])
    assert validate_invariants(after).valid
    assert all(e.actor == ASSISTANT_ACTOR for e in after.history[-turn.committed :])
    assert turn.committed >= 2


def test_wall_by_position_and_ceiling_height(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    level = only_level(project)
    graph = WallGraph.from_level(level)
    # Nord en haut : « le mur du fond » est celui dont le milieu est le plus haut.
    pick = resolve_wall(project, level_id, "le mur du fond")
    assert len(pick.walls) == 1
    a, b = graph.wall_axis(pick.walls[0].id)
    assert (a.y + b.y) / 2 == pytest.approx(
        max((graph.wall_axis(w)[0].y + graph.wall_axis(w)[1].y) / 2 for w in graph.walls)
    )
    left = resolve_wall(project, level_id, "le mur de gauche").walls[0]
    la, lb = graph.wall_axis(left.id)
    assert (la.x + lb.x) / 2 == pytest.approx(
        min((graph.wall_axis(w)[0].x + graph.wall_axis(w)[1].x) / 2 for w in graph.walls)
    )
    assert len(resolve_wall(project, level_id, "le mur le plus long").walls) == 1
    assert len(resolve_wall(project, level_id, "le mur violet").walls) == 0
    assert len(resolve_wall(project, level_id, None).walls) == 4, "sans indice : ambigu"
    after, turn = act(
        project,
        level_id,
        "le mur du fond fait 4,5 m",
        _reply(
            AssistantIntent(
                kind="set_dimension", dimension="wall", target="le mur du fond", value_m=4.5
            )
        ),
        tmp_path,
    )
    assert turn.actions[0].status == "done"
    assert WallGraph.from_level(only_level(after)).wall_length(pick.walls[0].id) == pytest.approx(
        4.5
    )
    after, turn = act(
        project,
        level_id,
        "hauteur sous plafond 2,60",
        _reply(AssistantIntent(kind="set_dimension", dimension="height", value_m=2.6)),
        tmp_path,
    )
    assert only_level(after).height == pytest.approx(2.6)
    assert only_level(after).height_source is Source.MEASURED
    # Sans indice de mur : question qui nomme les murs.
    _, turn = act(
        project,
        level_id,
        "le mur fait 4 m",
        _reply(AssistantIntent(kind="set_dimension", dimension="wall", target=None, value_m=4.0)),
        tmp_path,
    )
    assert turn.actions[0].status == "question" and "Quel mur" in turn.message


def test_openings_are_placed_in_free_space(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    after, turn = act(
        project,
        level_id,
        "ajoute une porte sur le mur du fond",
        _reply(AssistantIntent(kind="add_opening", opening="door", target="le mur du fond")),
        tmp_path,
    )
    assert turn.actions[0].status == "done", turn.message
    level = only_level(after)
    before_ids = set(only_level(project).elements)
    doors = [
        o
        for o in level.elements.values()
        if isinstance(o, Opening) and o.kind is OpeningKind.DOOR and o.id not in before_ids
    ]
    assert len(doors) == 1 and doors[0].source is Source.ESTIMATED
    assert validate_invariants(after).valid
    wall = resolve_wall(project, level_id, "le mur du fond").walls[0]
    assert doors[0].host_wall == wall.id
    # Une fenêtre de 1 m écrite, sur le même mur : placée à côté, sans chevauchement.
    again, turn = act(
        after,
        level_id,
        "mets une fenêtre de 1 m sur le mur du fond",
        _reply(
            AssistantIntent(
                kind="add_opening", opening="window", target="le mur du fond", value_m=1.0
            )
        ),
        tmp_path,
    )
    assert turn.actions[0].status == "done", turn.message
    openings = [
        o
        for o in only_level(again).elements.values()
        if isinstance(o, Opening) and o.host_wall == wall.id
    ]
    assert len(openings) >= 2 and validate_invariants(again).valid
    ordered = sorted(openings, key=lambda o: o.offset)
    for first, second in pairwise(ordered):
        assert first.offset + first.width <= second.offset + 1e-6
    # Sans mur nommé, plusieurs murs : question.
    _, turn = act(
        project,
        level_id,
        "ajoute une porte",
        _reply(AssistantIntent(kind="add_opening", opening="door")),
        tmp_path,
    )
    assert turn.actions[0].status == "question" and "Sur quel mur" in turn.message


def test_name_room_and_type(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    after, turn = act(
        project,
        level_id,
        "appelle la pièce Chambre d'amis",
        _reply(AssistantIntent(kind="name_room", name="Chambre d'amis")),
        tmp_path,
    )
    room = next(e for e in only_level(after).elements.values() if isinstance(e, Room))
    assert room.name == "Chambre d'amis" and room.room_type == "bedroom"
    assert turn.actions[0].status == "done" and turn.committed == 1


def test_short_memory_resolves_pronouns(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    project = apply_op(
        project, level_id, AddFurniture(asset_id="wardrobe_150", position=Point2(x=0.9, y=2.5))
    )
    desk = next(
        e for e in only_level(project).elements.values() if getattr(e, "category", "") == "desk"
    )
    # « Déplace-le » sans mémoire : question ; avec le dernier meuble cité : le bureau bouge.
    _, turn = act(
        project,
        level_id,
        "déplace-le contre un mur",
        _reply(AssistantIntent(kind="move_furniture", target="le", near=["wall"])),
        tmp_path,
    )
    assert turn.actions[0].status in ("question", "refused")
    _, turn = act(
        project,
        level_id,
        "déplace-le contre un mur",
        _reply(AssistantIntent(kind="move_furniture", target="le", near=["wall"])),
        tmp_path,
        last_element_id=desk.id,
    )
    assert turn.actions[0].status == "done", turn.message
    assert turn.actions[0].element_ids == [desk.id]


@pytest.mark.parametrize(
    ("text", "kind", "check"),
    [
        (
            "la pièce fait 4,20 de large",
            "set_dimension",
            lambda i: i.dimension == "width" and i.value_m == pytest.approx(4.2),
        ),
        (
            "la pièce fait 5 m sur 3,5",
            "set_dimension",
            lambda i: i.dimension == "length" and i.value_m == pytest.approx(5.0),
        ),
        (
            "le mur du fond fait 4,5 m",
            "set_dimension",
            lambda i: (
                i.dimension == "wall"
                and "fond" in (i.target or "")
                and i.value_m == pytest.approx(4.5)
            ),
        ),
        (
            "hauteur sous plafond 2m60",
            "set_dimension",
            lambda i: i.dimension == "height" and i.value_m == pytest.approx(2.6),
        ),
        (
            "ajoute une porte sur le mur de gauche",
            "add_opening",
            lambda i: i.opening == "door" and "gauche" in (i.target or ""),
        ),
        (
            "mets une fenêtre de 1,20 m au fond",
            "add_opening",
            lambda i: i.opening == "window" and i.value_m == pytest.approx(1.2),
        ),
        ("appelle la pièce Bureau", "name_room", lambda i: i.name == "Bureau"),
        ("c'est une chambre d'amis", "name_room", lambda i: (i.name or "").startswith("Chambre")),
    ],
)
def test_plan_phrases(text: str, kind: str, check) -> None:  # type: ignore[no-untyped-def]
    reply = understand(text, CATEGORIES)
    assert reply is not None, text
    assert reply.intents[0].kind == kind, reply.intents
    assert check(reply.intents[0]), reply.intents[0]
    if text.startswith("la pièce fait 5"):
        assert reply.intents[1].dimension == "width" and reply.intents[1].value_m == pytest.approx(
            3.5
        )
    assert math.isfinite(reply.intents[0].value_m or 1.0)
