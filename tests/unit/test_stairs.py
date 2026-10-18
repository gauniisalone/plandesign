"""S1 : cage d'escalier paramétrique (trémie, escalier, garde-corps), règles et plans."""

from __future__ import annotations

import itertools
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import Point, Polygon

from openplan.geom.stairs import (
    HEADROOM_M,
    arrival_zone,
    blondel,
    railing_lines,
    stair_layout,
)
from openplan.pipeline.assistant import act
from openplan.pipeline.assistant_phrases import understand
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.export import render_dxf, render_svg
from openplan.pipeline.layouts import LayoutBody, generate_layouts
from openplan.pipeline.scene3d import bake_level
from openplan.scene.migrate import load_project
from openplan.scene.ops import AddFurniture, AddStairs, apply_op, stair_rise
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Stairs
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.validation.layout import check_layout
from tests.factories import attic_project, only_level, rect_room_project

KINDS = ("straight", "quarter_turn", "half_turn", "spiral")


def _stairs(kind: str = "straight", **kw: object) -> Stairs:
    return Stairs(
        kind=kind,  # type: ignore[arg-type]
        position=Point2(x=0.0, y=0.0),
        source=Source.USER_DEFINED,
        confidence=1.0,
        **kw,  # type: ignore[arg-type]
    )


def test_blondel_for_a_storey() -> None:
    steps = blondel(2.70)
    assert steps.risers == 15 and steps.riser_m == pytest.approx(0.18)
    assert steps.going_m == pytest.approx(0.27) and 0.60 <= steps.value_m <= 0.64
    for rise in (2.2, 2.5, 2.9, 3.3):
        s = blondel(rise)
        assert 0.16 <= s.riser_m <= 0.20 and 0.60 <= s.value_m <= 0.64 + 0.02


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_climbs_to_the_floor_and_arrives_on_the_opening_edge(kind: str) -> None:
    lay = stair_layout(_stairs(kind))
    assert len(lay.treads) == lay.steps.treads == 14
    zs = [t.z_top for t in lay.treads]
    assert zs == sorted(zs), "on monte marche après marche"
    assert zs[-1] == pytest.approx(-lay.steps.riser_m), "dernière contremarche sur le plancher"
    assert lay.opening.is_valid and lay.opening.area > 1.0
    edge = lay.opening.exterior
    assert edge.distance(Point(lay.arrival.coords[0])) < 0.02
    assert edge.distance(Point(lay.arrival.coords[1])) < 0.02
    assert arrival_zone(lay).area > 0.3, "palier d'arrivée hors trémie"


def test_straight_flight_dimensions_and_mirror() -> None:
    lay = stair_layout(_stairs("straight", yaw=0.0))
    minx, miny, maxx, maxy = lay.opening.bounds
    assert maxx - minx == pytest.approx(14 * 0.27, abs=1e-6), "montée le long de +X"
    assert maxy - miny == pytest.approx(0.9, abs=1e-6)
    assert lay.arrival.coords[0][0] == pytest.approx(maxx), "on arrive au bout de la montée"
    left = stair_layout(_stairs("quarter_turn"))
    right = stair_layout(_stairs("quarter_turn", turn="right"))
    assert left.arrival.centroid.y == pytest.approx(-right.arrival.centroid.y, abs=1e-6)


def test_railings_only_on_open_sides() -> None:
    lay = stair_layout(_stairs("straight"))
    free = railing_lines(lay, None)
    assert sum(Polygon(lay.opening).exterior.length for _ in [0]) > sum(
        math.dist(a, b) for line in free for a, b in itertools.pairwise(line)
    ), "pas de garde-corps du côté de l'arrivée"
    minx, _miny, maxx, maxy = lay.opening.bounds
    wall = Polygon(
        [(minx - 1, maxy), (maxx + 1, maxy), (maxx + 1, maxy + 0.2), (minx - 1, maxy + 0.2)]
    )
    along_wall = railing_lines(lay, wall)
    assert all(min(p[1] for p in line) < maxy - 0.1 for line in along_wall), "rien contre le mur"


def _with_stairs(project, kind: str = "straight", x: float = 2.0, y: float = 1.5, yaw: float = 0.0):  # type: ignore[no-untyped-def]
    lid = only_level(project).id
    return apply_op(project, lid, AddStairs(kind=kind, position=Point2(x=x, y=y), yaw=yaw))  # type: ignore[arg-type]


def test_add_stairs_opens_the_floor_and_bakes_steps_and_railing() -> None:
    project = _with_stairs(rect_room_project(5.0, 4.0), x=2.5, y=2.0)
    level = only_level(project)
    stairs = next(e for e in level.elements.values() if isinstance(e, Stairs))
    assert stairs.rise == pytest.approx(2.70) and stairs.name == "Escalier"
    bake = bake_level(project, level.id, style="realistic")
    steps = [p for p in bake.prisms if p.kind == "stairs" and p.finish == "wood_dark"]
    shaft = [p for p in bake.prisms if p.kind == "stairs" and p.finish == "plaster"]
    assert len(shaft) == 4, "cage fermée sous la trémie (quatre murs)"
    rails = [p for p in bake.prisms if p.kind == "stairs" and p.finish in ("timber", "metal")]
    assert len(steps) >= 14 and rails, "marches et garde-corps"
    assert min(p.z0 for p in steps) == pytest.approx(-2.70, abs=1e-6)
    floors = [p for p in bake.polyhedra if p.kind == "slab"] + [
        p for p in bake.prisms if p.kind == "floor" and p.holes
    ]
    assert floors, "le plancher est percé (trémie) : polyèdre à trou"
    derived = compute_derived(level)
    d = derived.stairs[stairs.id]
    assert d.risers == 15 and len(d.treads) == 14 and d.railings and len(d.arrow) == 3


def test_rules_block_furniture_in_the_stairwell_and_on_the_arrival() -> None:
    project = _with_stairs(rect_room_project(5.0, 4.0), x=2.5, y=2.0)
    lid = only_level(project).id
    on_hole = apply_op(
        project, lid, AddFurniture(asset_id="chest_of_drawers", position=Point2(x=2.5, y=2.0))
    )
    types = {v.type: v.severity for v in check_layout(on_hole)}
    assert types.get("furniture_in_stairwell") == "high"
    lay = stair_layout(
        next(e for e in only_level(project).elements.values() if isinstance(e, Stairs))
    )
    c = arrival_zone(lay).centroid
    on_landing = apply_op(
        project,
        lid,
        AddFurniture(asset_id="chest_of_drawers", position=Point2(x=c.x, y=c.y), yaw=math.pi / 2),
    )
    assert {v.type for v in check_layout(on_landing)} >= {"stairs_arrival_blocked"}


def test_headroom_is_flagged_under_a_low_slope() -> None:
    project = attic_project()  # égouts à 1 m le long de y = 0 et y = 5, faîtage à 3,2 m
    near_eave = _with_stairs(project, x=3.0, y=0.8, yaw=0.0)
    flagged = [v for v in check_layout(near_eave) if v.type == "stairs_headroom"]
    assert flagged and flagged[0].severity == "medium"
    under_ridge = _with_stairs(project, x=3.0, y=2.5, yaw=0.0)
    assert not [v for v in check_layout(under_ridge) if v.type == "stairs_headroom"]
    d = next(iter(compute_derived(only_level(under_ridge)).stairs.values()))
    assert d.headroom_m is not None and d.headroom_m >= HEADROOM_M


def test_layout_generator_keeps_off_the_stairwell() -> None:
    project = _with_stairs(rect_room_project(5.0, 4.0), x=2.5, y=2.0)
    lid = only_level(project).id
    brief = LayoutBrief(items=[BriefItem(category="bed_double"), BriefItem(category="desk")])
    run = generate_layouts(project, lid, LayoutBody(brief=brief, variants=2, seed=3))
    hole = next(iter(compute_derived(only_level(project)).stairs.values()))
    opening = Polygon([(p.x, p.y) for p in hole.outline])
    for proposal in run.proposals:
        for f in proposal.furniture:
            assert Point(f.transform.position.x, f.transform.position.y).distance(opening) > 0.0


def test_rise_comes_from_the_level_below() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    assert stair_rise(project, lid) == pytest.approx(2.70)
    upper = only_level(project).model_copy(update={"elevation": 2.9})
    lower = upper.model_copy(
        update={"id": __import__("uuid").uuid4(), "elevation": 0.0, "elements": {}, "vertices": {}}
    )
    two = project.model_copy(update={"levels": {upper.id: upper, lower.id: lower}})
    assert stair_rise(two, upper.id) == pytest.approx(2.9)


def test_old_reserved_stairs_migrate_without_loss() -> None:
    project = rect_room_project()
    data = json.loads(project.model_dump_json())
    data["schema_version"] = "0.2.0"
    level = next(iter(data["levels"].values()))
    level["elements"]["00000000-0000-0000-0000-0000000000aa"] = {
        "id": "00000000-0000-0000-0000-0000000000aa",
        "type": "stairs",
        "source": "user_defined",
        "confidence": 1.0,
        "footprint": [{"x": 1, "y": 1}, {"x": 4, "y": 1}, {"x": 4, "y": 2}, {"x": 1, "y": 2}],
    }
    loaded = load_project(data)
    stairs = next(e for e in only_level(loaded).elements.values() if isinstance(e, Stairs))
    assert loaded.schema_version == "0.15.0"
    assert (stairs.position.x, stairs.position.y) == pytest.approx((2.5, 1.5))
    assert stairs.width == pytest.approx(1.0) and abs(math.sin(stairs.yaw)) < 1e-9


def test_plans_draw_the_stairs_symbol() -> None:
    project = _with_stairs(rect_room_project(5.0, 4.0), x=2.5, y=2.0)
    svg = render_svg(project, only_level(project).id)
    assert 'class="stairs"' in svg and "stairs-railing" in svg and "15 × 18,0 / 27 cm" in svg
    dxf = render_dxf(project, only_level(project).id)
    assert "ESCALIERS" in dxf


def test_assistant_understands_and_places_stairs(tmp_path: Path) -> None:
    reply = understand("Ajoute un escalier droit contre le mur de gauche", [])
    assert reply is not None and reply.intents[0].kind == "add_stairs"
    assert reply.intents[0].stairs == "straight" and reply.intents[0].target.startswith("mur")
    centre = understand("Mets une trémie avec garde-corps au centre", [])
    assert centre is not None and centre.intents[0].target == "centre"
    helix = understand("Pose un escalier en colimaçon", [])
    assert helix is not None and helix.intents[0].stairs == "spiral"
    project = rect_room_project(5.0, 4.0)
    lid = only_level(project).id
    after, turn = act(project, lid, "Mets une trémie au centre", centre, tmp_path)
    stairs = [e for e in only_level(after).elements.values() if isinstance(e, Stairs)]
    assert len(stairs) == 1 and turn.actions[0].status == "done", turn.actions
    assert stairs[0].source == "estimated"
    _, wall = act(project, lid, "Ajoute un escalier contre le mur de gauche", reply, tmp_path)
    assert wall.actions[0].status in ("done", "question"), wall.actions
