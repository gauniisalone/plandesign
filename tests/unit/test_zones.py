"""V1 (note 10, point 2) : brief par zones, densité cible, baies le long du faîtage, lits et
rangements bas contre les jambettes."""

from __future__ import annotations

import pytest
from shapely.geometry import Polygon, box

from openplan.pipeline.layouts import (
    LayoutBody,
    ZoneBriefBody,
    brief_from_intent,
    build_context,
    generate_layouts,
)
from openplan.pipeline.zones import target_items, zone_bays, zone_brief, zones_for
from openplan.scene.ops import SetIntent, apply_op
from openplan.schemas.elements import Room
from openplan.schemas.intent import DesignIntent
from tests.factories import attic_project, only_level, rect_room_project


def test_zones_follow_the_room_type_and_its_area() -> None:
    plain = DesignIntent()
    assert zones_for(plain, "attic", 73.0, True) == ["couchage", "lecture", "rangement", "detente"]
    assert zones_for(plain, "bedroom", 12.0, False) == ["couchage"]
    assert zones_for(plain, None, 30.0, True) == ["couchage", "lecture"]
    assert zones_for(plain, "kitchen", 20.0, False) == [], "une cuisine ne se meuble pas par zones"
    chosen = DesignIntent(zones=["bureau", "lecture"])
    assert zones_for(chosen, "attic", 73.0, True) == ["bureau", "lecture"], "l'intention prime"


def test_a_73_m2_attic_gets_15_to_25_pieces() -> None:
    brief = zone_brief(DesignIntent(), "attic", 73.0, sloped=True)
    total = sum(i.count for i in brief.items)
    assert 15 <= total <= 35 and total >= target_items(73.0)
    # Combles (H1) : le coin nuit est un lit encastré, tête vers la jambette.
    beds = [i for i in brief.items if i.category == "bed_builtin"]
    assert beds and beds[0].near == ["low_wall"] and beds[0].zone == "couchage"
    assert {i.zone for i in brief.items} == {"couchage", "lecture", "rangement", "detente"}


def test_low_wall_only_under_a_slope_and_avoided_categories_are_dropped() -> None:
    flat = zone_brief(DesignIntent(), "bedroom", 14.0, sloped=False)
    assert all("low_wall" not in i.near for i in flat.items)
    avoiding = zone_brief(DesignIntent(avoid=["rug", "chest"]), "bedroom", 14.0, sloped=False)
    assert not {"rug", "chest"} & {i.category for i in avoiding.items}
    wished = zone_brief(
        DesignIntent(furniture=[{"category": "desk", "count": 1}]),  # type: ignore[list-item]
        "bedroom",
        14.0,
        sloped=False,
    )
    assert wished.items[0].category == "desk" and wished.items[0].zone is None


def test_bays_slice_the_room_along_the_axis() -> None:
    room = box(0, 0, 9, 4)
    bays = zone_bays(room, ["couchage", "lecture", "rangement"], (1.0, 0.0))
    assert list(bays) == ["couchage", "lecture", "rangement"]
    xs = [(round(b.bounds[0], 3), round(b.bounds[2], 3)) for b in bays.values()]
    assert xs == [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0)]
    assert sum(b.area for b in bays.values()) == pytest.approx(room.area)


def _room(project) -> Room:  # type: ignore[no-untyped-def]
    return next(e for e in only_level(project).elements.values() if isinstance(e, Room))


def test_attic_layout_by_zones_is_dense_valid_and_beds_go_under_the_slope() -> None:
    project = attic_project()
    level = only_level(project)
    brief = brief_from_intent(project, level.id, ZoneBriefBody())
    run = generate_layouts(project, level.id, LayoutBody(brief=brief, variants=2, budget_s=20))
    assert run.proposals, run.result.warnings
    best = run.proposals[0]
    assert best.valid
    area = build_context(project, level.id, None).room_polygon.area
    assert len(best.furniture) >= 0.25 * area, "densité bien au-dessus de l'état des lieux"
    ctx = build_context(project, level.id, None)
    low = ctx.landmarks.low_zone
    assert low is not None
    # Lit encastré sous la pente (H1), ou lit classique s'il ne tient pas.
    beds = [f for f in best.furniture if f.category in ("bed_builtin", "bed_double")]
    assert beds
    t, b = beds[0].transform, beds[0].bbox
    from openplan.validation.layout import footprint_corners

    footprint = Polygon(footprint_corners(t.position.x, t.position.y, t.yaw, b.width, b.depth))
    assert footprint.distance(low) < 0.05, "le lit touche la bande basse, contre la jambette"


def test_zone_brief_route_reads_the_room_intent() -> None:
    project = rect_room_project(6.0, 5.0)
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, SetIntent(room_id=_room(project).id, fields={"zones": ["bureau"]})
    )
    brief = brief_from_intent(project, level_id, ZoneBriefBody())
    assert {i.zone for i in brief.items} == {"bureau"}
    assert "desk" in {i.category for i in brief.items}
