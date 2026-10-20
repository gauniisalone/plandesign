"""KAN-32 : l'assistant déplace, supprime ou redimensionne une porte ou une fenêtre déjà posée.

Phrases françaises (virgule décimale, cm ou m), désignation par la pièce et l'orientation,
question quand deux ouvertures conviennent, contraintes du mur (angle, autre ouverture,
plafond), refus de supprimer ce que l'utilisateur a posé, historique annulable, et le même
geste avec Gemini texte seul (réponse simulée, aucun appel réseau).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LlmRequest, LlmResponse
from openplan.adapters.remote.assistant import parse_reply
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.assistant import act
from openplan.pipeline.assistant_phrases import understand
from openplan.pipeline.say import Readers, clauses, readers_for, say
from openplan.scene import history
from openplan.scene.catalog import load_catalog
from openplan.schemas import (
    Level,
    Opening,
    OpeningKind,
    Point2,
    Project,
    Room,
    Source,
    Swing,
    Vertex,
    Wall,
)
from openplan.schemas.assistant import ASSISTANT_ACTOR
from openplan.schemas.common import ElementBase
from openplan.schemas.scene import PrivacySettings
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level

CATEGORIES = load_catalog().categories


# ------------------------------------------------------------------------------- le plan
def _plan(*, second_window: str | None = None, user_placard_door: bool = False) -> Project:
    """Chambre [0,4]×[0,3] (fenêtre nord), salon [4,9]×[0,3] (fenêtre sud, porte vers la
    chambre posée par l'utilisateur), placard [0,1,5]×[3,4,2] (porte sur la chambre). Le nord
    est en haut (y croissant). `second_window` : « west » (mur ouest de la chambre) ou
    « north » (deuxième fenêtre au nord, à droite de la première)."""
    vertices: dict[tuple[float, float], Vertex] = {}
    walls: dict[frozenset[tuple[float, float]], Wall] = {}

    def vertex(p: tuple[float, float]) -> Vertex:
        if p not in vertices:
            vertices[p] = Vertex(
                position=Point2(x=p[0], y=p[1]), source=Source.ESTIMATED, confidence=0.6
            )
        return vertices[p]

    def wall(a: tuple[float, float], b: tuple[float, float]) -> Wall:
        key = frozenset({a, b})
        if key not in walls:
            walls[key] = Wall(
                start_vertex=vertex(a).id,
                end_vertex=vertex(b).id,
                thickness=0.15,
                source=Source.ESTIMATED,
                confidence=0.6,
            )
        return walls[key]

    def room(name: str, room_type: str, loop: list[tuple[float, float]]) -> Room:
        ids = [wall(a, b).id for a, b in zip(loop, loop[1:] + loop[:1], strict=True)]
        return Room(
            name=name,
            room_type=room_type,
            boundary_walls=ids,
            source=Source.INFERRED,
            confidence=0.6,
        )

    chambre = room("Chambre", "bedroom", [(0, 0), (4, 0), (4, 3), (1.5, 3), (0, 3)])
    salon = room("Salon", "living_room", [(4, 0), (9, 0), (9, 3), (4, 3)])
    placard = room("Placard", "other", [(0, 3), (1.5, 3), (1.5, 4.2), (0, 4.2)])

    def opening(
        kind: OpeningKind, on: tuple[tuple[float, float], tuple[float, float]], **kw: Any
    ) -> Opening:
        fields: dict[str, Any] = {"source": Source.OBSERVED, "confidence": 0.8, **kw}
        return Opening(kind=kind, host_wall=wall(*on).id, **fields)

    north = opening(
        OpeningKind.WINDOW, ((4, 3), (1.5, 3)), offset=0.9, width=0.8, height=1.25, sill_height=0.9
    )
    salon_window = opening(
        OpeningKind.WINDOW, ((4, 0), (9, 0)), offset=1.5, width=1.0, height=1.25, sill_height=0.9
    )
    salon_door = opening(
        OpeningKind.DOOR,
        ((4, 0), (4, 3)),
        offset=1.0,
        width=0.83,
        height=2.04,
        swing=Swing(hinge="left", direction="inward"),
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    placard_door = opening(
        OpeningKind.DOOR,
        ((0, 3), (1.5, 3)),
        offset=0.4,
        width=0.7,
        height=2.04,
        swing=Swing(hinge="left", direction="inward"),
        **(
            {"source": Source.USER_DEFINED, "confidence": 1.0}
            if user_placard_door
            else {"source": Source.ESTIMATED, "confidence": 0.6}
        ),
    )
    elements: list[ElementBase] = [
        *walls.values(),
        chambre,
        salon,
        placard,
        north,
        salon_window,
        salon_door,
        placard_door,
    ]
    if second_window == "west":
        elements.append(
            opening(
                OpeningKind.WINDOW,
                ((0, 3), (0, 0)),
                offset=1.0,
                width=1.0,
                height=1.25,
                sill_height=0.9,
            )
        )
    elif second_window == "north":
        elements.append(
            opening(
                OpeningKind.WINDOW,
                ((4, 3), (1.5, 3)),
                offset=0.2,
                width=0.5,
                height=1.25,
                sill_height=0.9,
            )
        )
    level = Level(
        name="RDC",
        height=2.5,
        vertices={v.id: v for v in vertices.values()},
        elements={e.id: e for e in elements},
    )
    project = Project(name="Appartement", levels={level.id: level})
    assert validate_invariants(project).valid, validate_invariants(project)
    return project


def _openings(project: Project) -> list[Opening]:
    return [e for e in only_level(project).elements.values() if isinstance(e, Opening)]


def _windows_on(project: Project, a: tuple[float, float], b: tuple[float, float]) -> list[Opening]:
    level = only_level(project)
    graph = WallGraph.from_level(level)
    out = []
    for o in _openings(project):
        p, q = graph.wall_axis(o.host_wall)
        ends = {(round(p.x, 3), round(p.y, 3)), (round(q.x, 3), round(q.y, 3))}
        if ends == {a, b} and o.kind is OpeningKind.WINDOW:
            out.append(o)
    return out


def _center_x(project: Project, o: Opening) -> float:
    graph = WallGraph.from_level(only_level(project))
    a, _ = graph.wall_axis(o.host_wall)
    ux, _uy = graph.wall_direction(o.host_wall)
    return a.x + ux * (o.offset + o.width / 2)


def _say(project: Project, text: str, readers: Readers | None = None, tmp: Path | None = None):  # type: ignore[no-untyped-def]
    return say(
        project,
        only_level(project).id,
        text,
        CATEGORIES,
        tmp or Path("/nonexistent-runs"),
        readers=readers or Readers(None, None),
    )


# -------------------------------------------------------------------- MH1 : les phrases
PHRASES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "déplace la fenêtre de la chambre de 50 cm vers la gauche",
        "move_opening",
        {"target": "la fenetre de la chambre", "delta_m": -0.5},
    ),
    ("supprime la porte du placard", "remove_opening", {"target": "la porte du placard"}),
    (
        "élargis la fenêtre du salon à 1,20 m",
        "resize_opening",
        {"target": "la fenetre du salon", "dimension": "width", "value_m": 1.2},
    ),
    ("décale la porte à droite de 0,3 m", "move_opening", {"target": "la porte", "delta_m": 0.3}),
    ("bouge la fenêtre de 1m20 vers la droite", "move_opening", {"delta_m": 1.2}),
    ("élargis la fenêtre de 20 cm", "resize_opening", {"value_m": None, "delta_m": 0.2}),
    ("rétrécis la porte du placard à 70 cm", "resize_opening", {"value_m": 0.7}),
    (
        "change la hauteur de la fenêtre du salon à 1,40 m",
        "resize_opening",
        {"dimension": "height", "value_m": 1.4},
    ),
    ("rehausse la fenêtre de 10 cm", "resize_opening", {"dimension": "height", "delta_m": 0.1}),
    ("enlève la fenêtre nord", "remove_opening", {"target": "la fenetre nord"}),
    (
        "la porte de la chambre fait 90 cm de large",
        "resize_opening",
        {"target": "la porte de la chambre", "dimension": "width", "value_m": 0.9},
    ),
    ("mets la fenêtre du salon à 1.5 m", "resize_opening", {"value_m": 1.5}),
    ("réduis la porte de 5 cm", "resize_opening", {"delta_m": -0.05}),
    (
        "déplace la porte-fenêtre de 40 cm vers la gauche",
        "move_opening",
        {"target": "la porte-fenetre", "delta_m": -0.4},
    ),
    ("pousse la fenêtre vers la droite", "move_opening", {"delta_m": None}),
]


@pytest.mark.parametrize(("text", "kind", "expected"), PHRASES, ids=[p[0] for p in PHRASES])
def test_french_phrases_become_opening_intents(
    text: str, kind: str, expected: dict[str, Any]
) -> None:
    reply = understand(text, CATEGORIES)
    assert reply is not None, text
    (intent,) = reply.intents
    assert intent.kind == kind
    for key, value in expected.items():
        got = getattr(intent, key)
        if isinstance(value, float):
            assert got == pytest.approx(value), (key, got)
        else:
            assert got == value, (key, got)


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("éloigne le lit de la porte", "move_furniture"),
        ("ajoute une fenêtre sur le mur du fond", "add_opening"),
        ("mets une fenêtre de 1,20 m sur le mur de gauche", "add_opening"),
        ("enlève l'armoire", "remove_furniture"),
        ("mets un bureau sous la fenêtre", "add_furniture"),
    ],
)
def test_furniture_and_new_openings_are_unchanged(text: str, kind: str) -> None:
    reply = understand(text, CATEGORIES)
    assert reply is not None and reply.intents[0].kind == kind


def test_decimal_commas_do_not_cut_the_sentence() -> None:
    assert clauses("élargis la fenêtre du salon à 1,20 m") == [
        "élargis la fenêtre du salon à 1,20 m"
    ]
    assert clauses("plus chaleureux, et élargis la fenêtre à 1.20 m. Merci") == [
        "plus chaleureux",
        "élargis la fenêtre à 1.20 m",
        "Merci",
    ]


def test_the_three_ticket_sentences_do_what_they_say(tmp_path: Path) -> None:
    """MH1 et MH4 : trois phrases, trois gestes, trois pas d'historique `assistant` libellés,
    défaits un par un par Annuler."""
    project = _plan()
    north = _windows_on(project, (4, 3), (1.5, 3))[0]
    x0 = _center_x(project, north)

    after, turn = _say(project, "déplace la fenêtre de la chambre de 50 cm vers la gauche")
    assert [p.status for p in turn.parts] == ["done"], turn.parts
    moved = only_level(after).elements[north.id]
    assert isinstance(moved, Opening)
    # Face au mur nord depuis la chambre, la gauche est l'ouest (x décroissant).
    assert _center_x(after, moved) == pytest.approx(x0 - 0.5)
    assert moved.width == pytest.approx(north.width)
    assert after.history[-1].actor == ASSISTANT_ACTOR
    assert after.history[-1].label.startswith("Assistant : déplacer la fenêtre nord")
    assert turn.committed == 1

    placard_door = next(
        o for o in _openings(after) if o.kind is OpeningKind.DOOR and o.width == 0.7
    )
    after2, turn2 = _say(after, "supprime la porte du placard")
    assert [p.status for p in turn2.parts] == ["done"], turn2.parts
    assert placard_door.id not in only_level(after2).elements
    assert "supprimée" in turn2.parts[0].summary
    assert turn2.committed == 1, "rien d'autre (pas d'envie « placard » dans l'ambiance)"

    salon_window = _windows_on(after2, (4, 0), (9, 0))[0]
    after3, turn3 = _say(after2, "élargis la fenêtre du salon à 1,20 m")
    assert [p.status for p in turn3.parts] == ["done"], turn3.parts
    wider = only_level(after3).elements[salon_window.id]
    assert isinstance(wider, Opening)
    assert wider.width == pytest.approx(1.2)
    # Élargie autour de son milieu.
    assert wider.offset + wider.width / 2 == pytest.approx(
        salon_window.offset + salon_window.width / 2
    )
    assert "1,00 m → 1,20 m" in turn3.parts[0].summary
    assert validate_invariants(after3).valid

    # Annuler : chaque geste se défait, dans l'ordre inverse.
    back = history.undo(after3)
    assert only_level(back).elements[salon_window.id].width == pytest.approx(1.0)  # type: ignore[union-attr]
    back = history.undo(back)
    assert placard_door.id in only_level(back).elements
    back = history.undo(back)
    assert only_level(back).elements[north.id] == north


# ------------------------------------------------------------------ MH2 : l'ambiguïté
def test_two_windows_in_the_room_ask_which_one(tmp_path: Path) -> None:
    project = _plan(second_window="west")
    after, turn = _say(project, "déplace la fenêtre de la chambre de 50 cm vers la gauche")
    assert after is project, "jamais d'action au hasard"
    (part,) = turn.parts
    assert part.status == "question"
    assert part.reason == "Laquelle : la fenêtre nord ou la fenêtre ouest ?"
    assert len(part.element_ids) == 2
    # L'orientation lève l'ambiguïté.
    after, turn = _say(project, "déplace la fenêtre nord de la chambre de 50 cm vers la gauche")
    assert [p.status for p in turn.parts] == ["done"], turn.parts


def test_two_windows_on_the_same_wall_are_told_apart_by_left_and_right() -> None:
    project = _plan(second_window="north")
    after, turn = _say(project, "supprime la fenêtre nord")
    assert after is project
    assert turn.parts[0].reason == (
        "Laquelle : la fenêtre nord de gauche ou la fenêtre nord de droite ?"
    )
    after, turn = _say(project, "supprime la fenêtre nord de droite")
    assert [p.status for p in turn.parts] == ["done"], turn.parts
    (left,) = _windows_on(after, (4, 3), (1.5, 3))
    assert left.offset == pytest.approx(0.9), "celle de droite (vue de la chambre) est partie"


def test_an_unknown_opening_or_room_is_refused_with_the_manual_way() -> None:
    project = _plan()
    _after, turn = _say(project, "supprime la fenêtre de la cuisine")
    assert turn.parts[0].status == "refused"
    assert "pièce" in turn.parts[0].reason and "à la main" in turn.parts[0].reason
    _after, turn = _say(project, "supprime la fenêtre sud de la chambre")
    assert turn.parts[0].status == "refused"


def test_moving_without_an_amount_asks_how_far() -> None:
    project = _plan()
    after, turn = _say(project, "pousse la fenêtre du salon vers la droite")
    assert after is project
    assert turn.parts[0].status == "question" and "De combien" in turn.parts[0].reason


# ------------------------------------------------------------------- MH3 : les contraintes
@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("déplace la fenêtre de la chambre de 1 m vers la gauche", "sortirait de son mur"),
        ("déplace la fenêtre de la chambre de 70 cm vers la gauche", "angle"),
        ("élargis la fenêtre de la chambre à 4,50 m", "au plus 4,00 m"),
        ("change la hauteur de la fenêtre du salon à 1,80 m", "plafond"),
        ("rétrécis la fenêtre du salon à 30 cm", "au moins 0,40 m"),
    ],
)
def test_constraints_of_the_wall_are_kept(text: str, reason: str) -> None:
    project = _plan()
    after, turn = _say(project, text)
    assert after is project, "rien ne change"
    (part,) = turn.parts
    assert part.status == "refused" and reason in part.reason, part.reason


def test_an_opening_never_overlaps_another_one_of_its_wall() -> None:
    project = _plan(second_window="north")
    after, turn = _say(project, "décale la fenêtre nord de gauche de 30 cm vers la droite")
    assert after is project
    assert "chevaucherait la fenêtre" in turn.parts[0].reason
    after, turn = _say(project, "décale la fenêtre nord de gauche de 10 cm vers la droite")
    assert [p.status for p in turn.parts] == ["done"], turn.parts


def test_what_the_user_placed_is_not_removed_but_can_be_resized() -> None:
    project = _plan(user_placard_door=True)
    after, turn = _say(project, "supprime la porte du placard")
    assert after is project
    assert "posée ou mesurée par vous" in turn.parts[0].reason
    assert "Suppr" in turn.parts[0].reason
    after, turn = _say(project, "rétrécis la porte du placard à 65 cm")
    assert [p.status for p in turn.parts] == ["done"], turn.parts
    door = next(
        o
        for o in _openings(after)
        if o.kind is OpeningKind.DOOR and o.name is None and o.width == pytest.approx(0.65)
    )
    assert door.source is Source.USER_DEFINED, "la source ne change pas"


def test_a_locked_opening_is_refused() -> None:
    project = _plan()
    level = only_level(project)
    salon_window = _windows_on(project, (4, 0), (9, 0))[0]
    locked = salon_window.model_copy(update={"locked": True})
    project = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(
                    update={"elements": {**level.elements, locked.id: locked}}
                )
            }
        }
    )
    after, turn = _say(project, "élargis la fenêtre du salon à 1,20 m")
    assert after is project and "verrouillée" in turn.parts[0].reason


# -------------------------------------------------------------- MH6 : Gemini texte seul
class _Gemini:
    """Modèle distant simulé : répond par le JSON donné, aucun appel réseau."""

    model_id = "gemini-simulé"
    remote = True
    supports_images = False
    destination = "example.invalid"

    def __init__(self, intents: list[dict[str, Any]]) -> None:
        self.intents = intents
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if request.purpose == "assistant":
            text = json.dumps({"intents": self.intents, "message": "c'est fait"})
        else:
            text = '{"fields": {}}'
        return LlmResponse(text=text, model_id=self.model_id)


def _consenting(project: Project) -> Project:
    return project.model_copy(
        update={"privacy": PrivacySettings(allow_remote_models=True, remote_consent=True)}
    )


def _gemini_readers(model: _Gemini, tmp_path: Path) -> Readers:
    return readers_for(model, True, ModelGateway(tmp_path / "network_log.jsonl"))  # type: ignore[arg-type]


def test_gemini_reads_what_the_rules_cannot_and_does_the_same_gesture(tmp_path: Path) -> None:
    project = _consenting(_plan())
    rules, _ = _say(project, "déplace la fenêtre de la chambre de 50 cm vers la gauche")
    model = _Gemini(
        [{"kind": "move_opening", "target": "la fenêtre de la chambre", "delta_m": -0.5}]
    )
    text = "la fenêtre de la chambre, pousse-la de 50 cm sur la gauche"
    gemini, turn = _say(project, text, _gemini_readers(model, tmp_path), tmp_path)
    assert any(r.purpose == "assistant" for r in model.requests), "le modèle a lu la phrase"
    assert all(not r.images for r in model.requests), "texte seul"
    actions = [p for p in turn.parts if p.kind == "action"]
    assert [(p.status, p.by) for p in actions] == [("done", "model")], turn.parts
    assert only_level(gemini).elements == only_level(rules).elements


@pytest.mark.parametrize(
    ("text", "intents"),
    [
        (
            "supprime la porte du placard",
            [{"kind": "remove_opening", "target": "la porte du placard"}],
        ),
        (
            "élargis la fenêtre du salon à 1,20 m",
            [
                {
                    "kind": "resize_opening",
                    "target": "la fenêtre du salon",
                    "dimension": "width",
                    "value_m": 1.2,
                }
            ],
        ),
        (
            "déplace la fenêtre de la chambre de 50 cm vers la gauche",
            [{"kind": "move_opening", "target": "la fenêtre de la chambre", "delta_m": -0.5}],
        ),
    ],
)
def test_a_simulated_gemini_reply_gives_the_rules_result(
    text: str, intents: list[dict[str, Any]], tmp_path: Path
) -> None:
    """La réponse JSON simulée de Gemini, validée par `parse_reply`, puis exécutée : même plan
    qu'avec le lecteur à règles."""
    project = _plan()
    level_id = only_level(project).id
    by_rules = understand(text, CATEGORIES)
    assert by_rules is not None
    by_model = parse_reply(json.dumps({"intents": intents}), CATEGORIES, text)
    assert not by_model.dropped
    a, _ = act(project, level_id, text, by_rules, tmp_path)
    b, turn = act(project, level_id, text, by_model, tmp_path)
    assert [x.status for x in turn.actions] == ["done"]
    assert only_level(a).elements == only_level(b).elements


def test_gemini_cannot_invent_a_distance(tmp_path: Path) -> None:
    reply = parse_reply(
        json.dumps({"intents": [{"kind": "move_opening", "target": "la porte", "delta_m": -0.8}]}),
        CATEGORIES,
        "décale un peu la porte vers la gauche",
    )
    assert reply.intents[0].delta_m is None and reply.dropped
    project = _plan()
    after, turn = act(project, only_level(project).id, "", reply, tmp_path)
    assert after is project and turn.actions[0].status == "question"


def test_fixed_forms_skip_the_model(tmp_path: Path) -> None:
    """La phrase-type est lue sur la machine : Gemini n'est pas appelé pour le geste, et le
    résultat est celui des règles."""
    project = _consenting(_plan())
    model = _Gemini([{"kind": "remove_opening", "target": "la fenêtre du salon"}])
    after, turn = _say(
        project, "supprime la porte du placard", _gemini_readers(model, tmp_path), tmp_path
    )
    assert not any(r.purpose == "assistant" for r in model.requests)
    assert [p.status for p in turn.parts if p.kind == "action"] == ["done"]
    assert len(_openings(after)) == len(_openings(project)) - 1
