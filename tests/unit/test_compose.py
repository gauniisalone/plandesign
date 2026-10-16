"""V1 : gabarits de composition — une zone est une composition, pas une liste."""

from __future__ import annotations

import math

import pytest

from openplan.pipeline.compose import compose
from openplan.pipeline.layouts import (
    LayoutBody,
    ZoneBriefBody,
    apply_layout,
    brief_from_intent,
    generate_layouts,
)
from openplan.schemas.elements import Furniture
from openplan.validation.layout import check_layout
from tests.factories import attic_project, only_level, rect_room_project


def test_bedroom_template_flanks_the_bed_and_puts_the_rug_at_its_foot() -> None:
    items = [
        ("bed_double", 1.6, 2.0),
        ("nightstand", 0.45, 0.4),
        ("nightstand", 0.45, 0.4),
        ("rug", 2.0, 1.4),
    ]
    slots = {s.index: s for s in compose("couchage", items)}
    assert set(slots) == {0, 1, 2, 3}
    assert (slots[0].dx, slots[0].dy) == (0.0, 1.0), "tête du lit au mur"
    assert sorted(round(slots[i].dx, 3) for i in (1, 2)) == [-1.055, 1.055]
    assert slots[3].dy == pytest.approx(2.4), "tapis au pied, débordant du lit"


def test_living_template_faces_the_armchair_to_the_coffee_table() -> None:
    items = [
        ("sofa", 1.6, 0.9),
        ("coffee_table", 1.0, 0.6),
        ("armchair", 0.85, 0.85),
        ("rug", 2.0, 1.4),
        ("floor_lamp", 0.35, 0.35),
        ("armchair", 0.85, 0.85),
    ]
    slots = {s.index: s for s in compose("detente", items)}
    assert set(slots) == {0, 1, 2, 3, 4}, "un seul fauteuil dans le gabarit, l'autre reste libre"
    assert slots[1].dy == pytest.approx(0.9 + 0.45 + 0.3), "0,45 m entre canapé et table"
    assert slots[2].dyaw == pytest.approx(math.pi / 2) and slots[2].dy == slots[1].dy
    assert slots[3].dy == slots[1].dy, "la table basse est sur le tapis"
    assert slots[4].dx < -0.8, "lampadaire derrière le bras du canapé"


def test_dining_template_seats_the_chairs_around_the_table() -> None:
    """Quatre chaises sur une table carrée : table écartée du mur, une chaise de chaque côté
    long, deux en bout, toutes tournées vers elle ; en repli serré, table au mur, trois
    chaises, la quatrième hors composition."""
    items = [("table", 0.8, 0.8)] + [("dining_chair", 0.45, 0.5)] * 4
    slots = {s.index: s for s in compose("repas", items)}
    assert set(slots) == {0, 1, 2, 3, 4}
    assert slots[0].dy == pytest.approx(0.5 + 0.15 + 0.4), "table écartée du mur"
    assert slots[1].dyaw == pytest.approx(math.pi) and not slots[1].optional, "côté pièce"
    assert slots[2].dyaw == 0.0 and slots[2].dy < slots[0].dy, "côté mur"
    assert sorted(round(slots[i].dx, 2) for i in (3, 4)) == [-0.67, 0.67], "en bout"
    assert all(slots[i].optional for i in (2, 3, 4))
    compact = {s.index: s for s in compose("repas", items, compact=True)}
    assert set(compact) == {0, 1, 2, 3} and compact[0].dy == pytest.approx(0.4), "au mur"
    long = compose("repas", [("table", 1.6, 0.9)] + [("dining_chair", 0.45, 0.5)] * 2)
    assert sorted(round(s.dx, 2) for s in long[1:]) == [-0.4, 0.4], "deux chaises côte à côte"


def test_no_main_piece_no_template() -> None:
    assert compose("couchage", [("nightstand", 0.45, 0.4)]) == []
    assert compose("rangement", [("low_storage", 1.2, 0.5)]) == []


def _furniture(project, level_id) -> list[Furniture]:  # type: ignore[no-untyped-def]
    return [e for e in project.levels[level_id].elements.values() if isinstance(e, Furniture)]


def test_generated_bedroom_keeps_the_composition_and_raises_no_companion_remark() -> None:
    project = attic_project()
    level_id = only_level(project).id
    # Lit classique et ses chevets (les combles prennent sinon un lit encastré, H1).
    from openplan.pipeline.layouts import classic_beds

    zoned = brief_from_intent(project, level_id, ZoneBriefBody())
    one = [
        i.model_copy(update={"count": 1, "dimensions_m": None})
        if i.category == "bed_builtin"
        else i
        for i in zoned.items
    ]
    brief = classic_beds(zoned.model_copy(update={"items": one}))
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=1, budget_s=20))
    assert run.proposals, run.result.warnings
    furnished = apply_layout(project, level_id, run.proposals[0])
    items = _furniture(furnished, level_id)
    bed = next(f for f in items if f.category == "bed_double")
    stands = [f for f in items if f.category == "nightstand"]
    bx, by, yaw = bed.transform.position.x, bed.transform.position.y, bed.transform.yaw
    ux, uy = math.cos(yaw), math.sin(yaw)
    flanking = [
        s
        for s in stands
        if abs(abs((s.transform.position.x - bx) * ux + (s.transform.position.y - by) * uy) - 1.055)
        < 0.05
        and abs(s.transform.yaw - yaw) < 1e-6
    ]
    assert len(flanking) == 2, "deux chevets accolés au lit, même orientation"
    remarks = [v for v in check_layout(furnished) if v.type == "clearance_blocked"]
    pairs = {frozenset(f.category for f in items if f.id in v.element_ids) for v in remarks}
    assert frozenset({"bed_double", "nightstand"}) not in pairs


def test_living_room_gets_a_sofa_group() -> None:
    project = rect_room_project(6.0, 5.0)
    level_id = only_level(project).id
    from openplan.scene.ops import SetIntent, apply_op
    from openplan.schemas.elements import Room

    room = next(e for e in only_level(project).elements.values() if isinstance(e, Room))
    project = apply_op(project, level_id, SetIntent(room_id=room.id, fields={"zones": ["detente"]}))
    brief = brief_from_intent(project, level_id, ZoneBriefBody())
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=1, budget_s=20))
    furnished = apply_layout(project, level_id, run.proposals[0])
    items = _furniture(furnished, level_id)
    sofa = next(f for f in items if f.category == "sofa")
    table = next(f for f in items if f.category == "coffee_table")
    gap = math.hypot(
        table.transform.position.x - sofa.transform.position.x,
        table.transform.position.y - sofa.transform.position.y,
    )
    assert gap == pytest.approx(0.45 + 0.45 + 0.3, abs=0.02), "table basse devant le canapé"
    assert not [
        v
        for v in check_layout(furnished)
        if v.type == "clearance_blocked" and sofa.id in v.element_ids and table.id in v.element_ids
    ]
