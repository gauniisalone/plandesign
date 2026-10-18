"""Cadrage mesuré sans rendu (`geom.framing`) et vues automatiques sous les combles.

Scènes synthétiques seulement : un grenier bas de mêmes cotes que le cas réel (4,00 × 6,69 m,
faîtage 2,32 m, pentes jusqu'au sol, lit encastré contre un pignon) et une pièce droite
témoin. Le cadrage « avant » (vue d'architecte relevée de 6°, à 1,50 m) y est mesuré pour
documenter le défaut corrigé.
"""

from __future__ import annotations

import math

import pytest
from shapely.geometry import Polygon

from openplan.geom.framing import Box, FramingScene, Pose
from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.render import ARCHITECT_FOCAL_PX
from openplan.pipeline.views import (
    ATTIC_EYE_M,
    EYE_M,
    FRAME_EDGE,
    HEADROOM_M,
    HORIZON_MIN,
    ROOF_MAX,
    ROOF_MIN_DETAIL,
    SIZES,
    ViewPlan,
    _box,
    _room,
    _slope_above_head,
    distinct,
    plan_shares,
    plan_views,
)
from openplan.scene.factory import new_project
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Furniture
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project

WIDTH, HEIGHT = SIZES["4:3"]
WIDE = ("overall", "Contrechamp", "Vers la lumière", "Autre angle")


# ------------------------------------------------------------------ la mesure
def _box_room(top: float = 2.5) -> FramingScene:
    """Pièce vide de 4 × 4 m, plafond plat."""
    return FramingScene.build(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]), [], top, [])


def test_a_level_camera_puts_the_horizon_in_the_middle() -> None:
    pose = Pose(2.0, 2.0, 1.5, 0.0, 0.0, 800.0, WIDTH, HEIGHT)
    assert pose.horizon() == pytest.approx(0.5)
    down = pose.with_pitch(math.radians(-10))
    assert down.horizon() == pytest.approx(0.5 - 800.0 * math.tan(math.radians(10)) / HEIGHT)
    s = _box_room().shares(pose)
    assert s.floor + s.roof + s.wall + s.furniture == pytest.approx(1.0)
    assert s.furniture == 0.0


def test_looking_down_sees_the_floor_and_looking_up_the_ceiling() -> None:
    scene = _box_room()
    down = scene.shares(Pose(2.0, 2.0, 1.5, 0.0, math.radians(-89), 1200.0, WIDTH, HEIGHT))
    up = scene.shares(Pose(2.0, 2.0, 1.5, 0.0, math.radians(89), 1200.0, WIDTH, HEIGHT))
    assert down.floor > 0.95 and up.roof > 0.95


def test_a_level_camera_sees_as_much_floor_as_ceiling_at_mid_height() -> None:
    scene = _box_room(top=3.0)
    s = scene.shares(Pose(0.5, 2.0, 1.5, 0.0, 0.0, 600.0, WIDTH, HEIGHT))
    assert s.floor == pytest.approx(s.roof, abs=0.01)


def test_a_box_in_front_of_the_camera_is_furniture_and_hides_the_floor() -> None:
    bed = Box(2.5, 2.0, 0.0, 0.8, 1.0, 0.0, 0.6)
    empty = _box_room().shares(Pose(0.5, 2.0, 1.2, 0.0, math.radians(-20), 600.0, WIDTH, HEIGHT))
    scene = FramingScene.build(Polygon([(0, 0), (4, 0), (4, 4), (0, 4)]), [], 2.5, [bed])
    full = scene.shares(Pose(0.5, 2.0, 1.2, 0.0, math.radians(-20), 600.0, WIDTH, HEIGHT))
    assert full.furniture > 0.1
    assert full.floor < empty.floor


def test_projection_matches_the_pinhole() -> None:
    pose = Pose(0.0, 0.0, 1.0, 0.0, 0.0, 640.0, WIDTH, HEIGHT)
    assert pose.project((5.0, 0.0, 1.0)) == pytest.approx((0.5, 0.5))
    u, v = pose.project((2.0, -1.0, 1.0)) or (0.0, 0.0)
    assert u == pytest.approx(0.5 + 640.0 * 0.5 / WIDTH) and v == pytest.approx(0.5)
    assert pose.project((-1.0, 0.0, 1.0)) is None


# ------------------------------------------------------------------ les deux scènes
def low_attic() -> Project:
    """Grenier bas : 4,00 m entre pignons, 6,69 m entre égouts, faîtage 2,32 m, pentes jusqu'au
    sol, lit encastré de 90 cm contre un pignon (tête sous la pente), deux rangements bas."""

    def est(value: float) -> Estimate:
        return Estimate(value=value, sigma=0.3, source=Source.ESTIMATED)

    project = new_project("Grenier bas")
    geometry = AtticGeometry(
        length=est(4.0), width=est(6.69), ridge_height=est(2.32), eave_height=est(0.0)
    )
    project, _ = apply_attic(project, only_level(project).id, geometry)
    level_id = only_level(project).id
    for op in (
        AddFurniture(asset_id="bed_builtin", position=Point2(x=0.75, y=2.92), yaw=0.0),
        AddFurniture(asset_id="low_storage_eave", position=Point2(x=3.4, y=1.74), yaw=0.0),
        AddFurniture(asset_id="low_storage_eave", position=Point2(x=3.45, y=4.89), yaw=-math.pi),
    ):
        project = apply_op(project, level_id, op)
    return project


def straight_room() -> Project:
    project = rect_room_project(5.0, 4.0, height=2.5)
    level_id = only_level(project).id
    for op in (
        AddFurniture(asset_id="bed_double_160", position=Point2(x=1.4, y=2.9), yaw=math.pi),
        AddFurniture(asset_id="nightstand", position=Point2(x=0.35, y=3.7), yaw=math.pi),
        AddFurniture(asset_id="sofa_2", position=Point2(x=3.8, y=0.6), yaw=0.0),
        AddFurniture(asset_id="coffee_table", position=Point2(x=3.8, y=1.6)),
    ):
        project = apply_op(project, level_id, op)
    return project


def _wide(plan: ViewPlan) -> bool:
    return plan.role == "overall" or plan.name in WIDE


def test_before_the_fix_the_low_attic_overview_was_mostly_roof() -> None:
    """Cadrage d'avant (vue d'architecte à 1,50 m relevée de 6°, au bout du faîtage) : près des
    deux tiers de toit, presque pas de sol, horizon sous le milieu."""
    project = low_attic()
    room = _room(only_level(project))
    before = ViewPlan(
        "overall", "avant", 0.53, 3.35, 1.5, 0.0, math.radians(6.0), ARCHITECT_FOCAL_PX, ""
    )
    s = plan_shares(room, before)
    assert s.roof > 0.55 and s.floor < 0.05 and s.horizon > 0.55


def test_low_attic_wide_views_keep_the_roof_under_40_percent_with_the_horizon_high() -> None:
    project = low_attic()
    level = only_level(project)
    plans, skipped = plan_views(project, level.id)
    room = _room(level)
    assert [p.role for p in plans] == ["overall", "zone", "detail"], skipped
    for plan in plans:
        assert plan.z <= room.clearance(plan.x, plan.y) - HEADROOM_M + 1e-6, plan.name
        if not _wide(plan):
            continue
        s = plan_shares(room, plan)
        assert s.roof <= ROOF_MAX + 0.02, (plan.name, s)
        assert HORIZON_MIN - 0.01 <= s.horizon <= 0.40, (plan.name, s)
        assert s.floor >= 0.15, (plan.name, s)
        assert plan.z == pytest.approx(ATTIC_EYE_M), "à 1,30 m, là où la pente le permet"
    assert all(distinct(a, b) for i, a in enumerate(plans) for b in plans[i + 1 :])


def test_low_attic_detail_frames_the_whole_built_in_bed_under_its_slope() -> None:
    project = low_attic()
    level = only_level(project)
    plans, _ = plan_views(project, level.id)
    detail = next(p for p in plans if p.role == "detail")
    assert detail.name == "Détail : lit encastré"
    room = _room(level)
    bed = next(
        e
        for e in level.elements.values()
        if isinstance(e, Furniture) and e.category == "bed_builtin"
    )
    slope = _slope_above_head(room, bed)
    assert slope is not None and slope[2] < level.height
    pose = Pose(
        detail.x, detail.y, detail.z, detail.yaw, detail.pitch, detail.focal_px, WIDTH, HEIGHT
    )
    for point in [*_box(bed).corners(), slope]:
        uv = pose.project(point)
        assert uv is not None
        assert FRAME_EDGE - 1e-6 <= uv[0] <= 1 - FRAME_EDGE + 1e-6, point
        assert FRAME_EDGE - 1e-6 <= uv[1] <= 1 - FRAME_EDGE + 1e-6, point
    s = plan_shares(room, detail)
    assert ROOF_MIN_DETAIL <= s.roof <= ROOF_MAX, s
    assert s.furniture >= 0.15, s


def test_a_room_without_slopes_keeps_level_eye_height_views() -> None:
    """Témoin : rien ne change sans pente (vues larges de niveau, à 1,50 m)."""
    project = straight_room()
    level = only_level(project)
    plans, _ = plan_views(project, level.id)
    room = _room(level)
    assert not room.roofs
    wide = [p for p in plans if _wide(p)]
    assert wide
    for plan in wide:
        assert plan.pitch == 0.0 and plan.z == pytest.approx(EYE_M), plan.name
        assert plan_shares(room, plan).horizon == pytest.approx(0.5)
    overall = plans[0]
    assert (round(overall.x, 2), round(overall.y, 2)) == (0.92, 0.5)
    assert round(math.degrees(overall.yaw)) == 80
