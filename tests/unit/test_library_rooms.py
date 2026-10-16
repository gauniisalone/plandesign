"""Bibliothèque F5 : l'assistant nomme les nouveaux meubles, le générateur respecte l'usage
de la pièce (pas de WC dans une chambre, pas de lit dans une cuisine) et sait « dans le coin »."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.pipeline.assistant import act
from openplan.pipeline.assistant_phrases import SYNONYMS, understand
from openplan.pipeline.layouts import LayoutBody, build_context, generate_layouts, room_use
from openplan.scene.catalog import load_catalog
from openplan.schemas import Furniture
from openplan.schemas.elements import Room
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.schemas.scene import Project
from openplan.validation.layout import check_layout
from tests.factories import only_level, rect_room_project

CATEGORIES = sorted({a.category for a in load_catalog().assets})


def _as(project: Project, room_type: str | None, name: str = "R1") -> Project:
    level = only_level(project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    renamed = room.model_copy(update={"room_type": room_type, "name": name})
    new_level = level.model_copy(update={"elements": {**level.elements, room.id: renamed}})
    return project.model_copy(update={"levels": {level.id: new_level}})


def test_every_catalog_family_has_words() -> None:
    assert set(CATEGORIES) <= set(SYNONYMS), sorted(set(CATEGORIES) - set(SYNONYMS))


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("ajoute un évier", "kitchen_sink"),
        ("mets une cuisinière contre le mur", "stove"),
        ("il me faut un frigo", "fridge"),
        ("ajoute un petit frigo", "fridge_small"),
        ("pose une baignoire", "bathtub"),
        ("ajoute des toilettes", "toilet"),
        ("mets un lavabo", "washbasin"),
        ("ajoute un meuble vasque", "bathroom_cabinet"),
        ("installe une machine à laver", "washer"),
        ("ajoute un sèche-linge", "dryer"),
        ("ajoute une table basse", "coffee_table"),
        ("ajoute une table ronde", "round_table"),
        ("ajoute un fauteuil", "armchair"),
        ("mets un canapé d'angle", "sofa_corner"),
        ("ajoute un meuble tv", "tv_unit"),
        ("ajoute des lits superposés", "bed_bunk"),
        ("ajoute un bureau d'angle", "desk_corner"),
        ("ajoute une chaise de bureau", "chair"),
        ("ajoute deux chaises", "dining_chair"),
        ("ajoute un lampadaire", "floor_lamp"),
        ("ajoute un lit", "bed_double"),
        ("ajoute une table de chevet", "nightstand"),
    ],
)
def test_new_families_are_understood(text: str, category: str) -> None:
    reply = understand(text, CATEGORIES)
    assert reply is not None and reply.intents[0].kind == "add_furniture"
    assert reply.intents[0].category == category


def test_sink_under_the_window_and_shower_in_the_corner() -> None:
    """Les deux phrases de l'essai demandé par le PO."""
    sink = understand("mets un évier sous la fenêtre", CATEGORIES)
    assert sink is not None
    assert sink.intents[0].kind == "add_furniture"
    assert sink.intents[0].category == "kitchen_sink" and sink.intents[0].near == ["window"]
    shower = understand("ajoute une douche dans le coin", CATEGORIES)
    assert shower is not None
    assert shower.intents[0].category == "shower" and shower.intents[0].near == ["corner"]


def test_room_use_from_type_or_name() -> None:
    project = rect_room_project()
    assert room_use(build_context(_as(project, "bedroom"), only_level(project).id, None).room) == (
        "bedroom"
    )
    for name, use in (
        ("Salle de bain", "bathroom"),
        ("Cuisine", "kitchen"),
        ("Séjour", "living_room"),
        ("Chambre d'amis", "bedroom"),
        ("Grenier", None),
    ):
        ctx = build_context(_as(project, None, name), only_level(project).id, None)
        assert room_use(ctx.room) == use, name


def _layout(project: Project, *categories: str):  # type: ignore[no-untyped-def]
    level_id = only_level(project).id
    brief = LayoutBrief(items=[BriefItem(category=c) for c in categories])
    return generate_layouts(project, level_id, LayoutBody(brief=brief, variants=2, seed=1))


def test_generator_keeps_wc_out_of_a_bedroom_and_bed_out_of_a_kitchen() -> None:
    bedroom = _layout(_as(rect_room_project(), "bedroom"), "bed_double", "toilet")
    assert bedroom.proposals
    for proposal in bedroom.proposals:
        assert {f.category for f in proposal.furniture} == {"bed_double"}
        wc = next(u for u in proposal.unplaced if u.category == "toilet")
        assert wc.label == "WC" and wc.reason == "meuble de salle de bain, pas pour une chambre"
    kitchen = _layout(_as(rect_room_project(), None, "Cuisine"), "bed_double", "stove", "washer")
    assert kitchen.proposals
    for proposal in kitchen.proposals:
        assert {f.category for f in proposal.furniture} == {"stove", "washer"}
        bed = next(u for u in proposal.unplaced if u.category == "bed_double")
        assert "pas pour une cuisine" in bed.reason
    bathroom = _layout(_as(rect_room_project(), "bathroom"), "toilet", "shower", "sofa")
    for proposal in bathroom.proposals:
        assert "sofa" not in {f.category for f in proposal.furniture}
    # Pièce sans usage connu (grenier) : rien n'est exclu.
    attic = _layout(_as(rect_room_project(), "attic", "Grenier"), "toilet")
    assert attic.proposals and attic.proposals[0].furniture[0].category == "toilet"


def test_assistant_shower_in_the_corner_and_sink_under_the_window(tmp_path: Path) -> None:
    bathroom = _as(rect_room_project(), "bathroom", "Salle de bain")
    level_id = only_level(bathroom).id
    reply = understand("ajoute une douche dans le coin", CATEGORIES)
    assert reply is not None
    after, turn = act(bathroom, level_id, "ajoute une douche dans le coin", reply, tmp_path)
    assert turn.actions[0].status == "done", turn.actions[0].reason
    assert "n'a pas pu" not in turn.message, turn.message
    shower = next(f for f in only_level(after).elements.values() if isinstance(f, Furniture))
    ctx = build_context(after, level_id, None)
    from shapely.geometry import Polygon

    from openplan.pipeline.layouts import footprint_corners

    t = shower.transform
    footprint = Polygon(
        footprint_corners(t.position.x, t.position.y, t.yaw, shower.bbox.width, shower.bbox.depth)
    )
    assert ctx.landmarks.near("corner", footprint)
    assert not [v for v in check_layout(after) if v.severity == "high"]

    kitchen = _as(rect_room_project(), "kitchen", "Cuisine")
    reply = understand("mets un évier sous la fenêtre", CATEGORIES)
    assert reply is not None
    after, turn = act(
        kitchen, only_level(kitchen).id, "mets un évier sous la fenêtre", reply, tmp_path
    )
    assert turn.actions[0].status == "done", turn.actions[0].reason
    assert "n'a pas pu" not in turn.message, turn.message

    # Un WC dans une chambre : refusé, la raison dite.
    bedroom = rect_room_project()
    reply = understand("ajoute un wc", CATEGORIES)
    assert reply is not None
    same, turn = act(bedroom, only_level(bedroom).id, "ajoute un wc", reply, tmp_path)
    assert same is bedroom and turn.actions[0].status == "refused"
    assert "pas pour une chambre" in (turn.actions[0].reason or "")
    assert turn.actions[0].summary == "Ajouter WC : impossible"


def test_propose_names_every_piece_and_the_model_is_helped() -> None:
    """Essai réel F5 : « propose… avec un évier, une cuisinière et un lit » garde les trois
    meubles ; le modèle qui écrit « wc » est rattaché à `toilet` ; un repère écrit par
    l'utilisateur et oublié par le modèle est complété ; « Douche posée »."""
    from openplan.adapters.remote.assistant import parse_reply
    from openplan.pipeline.assistant import _posed, category_aliases, with_written_places
    from openplan.schemas.assistant import AssistantIntent, AssistantReply

    reply = understand(
        "propose-moi des aménagements avec un évier, une cuisinière et un lit", CATEGORIES
    )
    assert reply is not None
    assert [(i.kind, i.category) for i in reply.intents] == [
        ("propose_layouts", "kitchen_sink"),
        ("propose_layouts", "stove"),
        ("propose_layouts", "bed_double"),
    ]
    parsed = parse_reply(
        '{"intents": [{"kind": "add_furniture", "category": "wc", "count": 1}]}',
        CATEGORIES,
        "ajoute un wc",
        category_aliases(CATEGORIES),
    )
    assert parsed.intents[0].category == "toilet" and not parsed.dropped
    forgot = AssistantReply(
        intents=[AssistantIntent(kind="add_furniture", category="kitchen_sink")]
    )
    helped = with_written_places(forgot, "mets un évier sous la fenêtre", CATEGORIES)
    assert helped.intents[0].near == ["window"]
    other = AssistantReply(intents=[AssistantIntent(kind="add_furniture", category="stove")])
    assert with_written_places(other, "mets un évier sous la fenêtre", CATEGORIES) == other
    assert _posed("Douche", 1) == "posée" and _posed("Évier", 1) == "posé"
    assert _posed("Chaise", 2) == "posées" and _posed("Lits superposés", 1) == "posés"
