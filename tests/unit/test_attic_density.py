"""Chantier H, lot H5 : densité des combles (2 à 4 lits encastrés), composition des vues,
couette et tapis."""

from __future__ import annotations

import math

from shapely.geometry import Polygon

from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.decor import dress_level
from openplan.pipeline.layouts import (
    BED_GAP_M,
    LayoutBody,
    ZoneBriefBody,
    apply_layout,
    brief_from_intent,
    build_context,
    built_in_bed_count,
    classic_beds,
    generate_layouts,
)
from openplan.pipeline.views import _edge_cut, _room
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2
from openplan.validation.layout import (
    bed_headroom_at,
    check_layout,
    clearance_at,
    footprint_corners,
)
from tests.factories import attic_project, only_level, rect_room_project


def test_attic_gets_two_to_four_built_in_beds_heads_under_the_slope() -> None:
    project = attic_project()
    level = only_level(project)
    roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
    assert built_in_bed_count(build_context(project, level.id, None)) == 4, "12 m d'égouts"
    brief = brief_from_intent(project, level.id, ZoneBriefBody())
    run = generate_layouts(project, level.id, LayoutBody(brief=brief, variants=1, budget_s=20))
    beds = [f for f in run.proposals[0].furniture if f.category == "bed_builtin"]
    assert 2 <= len(beds) <= 4
    shapes = []
    for bed in beds:
        t = bed.transform
        fx, fy = -math.sin(t.yaw), math.cos(t.yaw)
        half = bed.bbox.depth / 2 - 0.1
        head = clearance_at((t.position.x - fx * half, t.position.y - fy * half), roofs, level)
        foot = clearance_at((t.position.x + fx * half, t.position.y + fy * half), roofs, level)
        assert foot - head > 0.5, "tête sous le rampant, pied vers le faîtage"
        free = bed_headroom_at(level, roofs, t.position.x, t.position.y, t.yaw, bed.bbox.depth)
        assert free >= 0.9
        corners = footprint_corners(
            t.position.x, t.position.y, t.yaw, bed.bbox.width, bed.bbox.depth
        )
        shapes.append(Polygon(corners))
    gaps = [a.distance(b) for i, a in enumerate(shapes) for b in shapes[i + 1 :]]
    assert min(gaps) >= BED_GAP_M - 1e-6
    furnished = apply_layout(project, level.id, run.proposals[0])
    remarks = [v.type for v in check_layout(furnished)]
    assert "clearance_blocked" not in remarks and "bed_headroom" not in remarks


def test_classic_fallback_for_a_row_is_single_beds_with_one_nightstand_each() -> None:
    project = attic_project()
    level = only_level(project)
    classic = classic_beds(brief_from_intent(project, level.id, ZoneBriefBody()))
    singles = [i for i in classic.items if i.category == "bed_single"]
    stands = [i for i in classic.items if i.category == "nightstand"]
    assert singles and singles[0].count == 4 and stands[0].count == 4


def test_overall_view_counts_furniture_cut_by_the_side_edges() -> None:
    project = rect_room_project(5.0, 4.0)
    level = only_level(project)
    project = apply_op(
        project, level.id, AddFurniture(asset_id="sofa_2", position=Point2(x=2.5, y=2.0))
    )
    room = _room(only_level(project))
    assert _edge_cut(room, 0.6, 2.0, 0.0) == 0, "canapé en plein cadre"
    assert _edge_cut(room, 2.5, 0.4, math.radians(90 + 45)) >= 1, "canapé sur le bord du cadre"


def test_jute_rug_goes_beside_the_bed_when_the_foot_is_taken() -> None:
    project = rect_room_project(5.0, 4.5)
    level = only_level(project)
    project = apply_op(
        project, level.id, AddFurniture(asset_id="bed_double_160", position=Point2(x=2.5, y=1.2))
    )
    project = apply_op(
        project, level.id, AddFurniture(asset_id="chest_of_drawers", position=Point2(x=2.5, y=2.7))
    )
    rug = next(d for d in dress_level(only_level(project)) if d.kind == "jute_rug")
    assert abs(rug.x - 2.5) > 1.0, "sur le côté du lit, pas au pied"
