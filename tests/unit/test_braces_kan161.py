"""KAN-161 : bois inclinés (jambes de force, arbalétriers), côté serveur.

Pose en deux points (pied, tête) en un seul pas d'historique ; tête aimantée sur un autre bois,
la faîtière ou le rampant (le plafond hors des combles), ou à une hauteur donnée ; pied au sol
ou sur un muret ; section 20 cm par défaut, carrée ou ronde ; déplacer, modifier, supprimer ;
volume réel (3D), projection au sol et passage (2D) ; obstacle pour les meubles et la
circulation ; dans le bake (classe « timber ») et nommé par le prompt quand la vue le voit ;
pose à la main réservée à l'offre Pro. Aucun changement de schéma : c'est un `Beam` (0.1.0)."""

from __future__ import annotations

import math
from uuid import UUID

import pytest
from pydantic import ValidationError

from openplan.api.policy.offers import is_pro_op
from openplan.api.policy.registry import PRO_OPS
from openplan.geom.brace import (
    FOOT_OUTSIDE,
    OBSTACLE_HEIGHT_M,
    SECTION_M,
    SNAP_M,
    TOO_FLAT,
    brace_footprint,
    brace_points,
    low_footprint,
    low_reach,
    passage,
    settings,
    underside_at,
)
from openplan.geom.truss import Ridge, ridge_span
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.guides import VISIBLE_MIN_PX, visible_pixels
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import BRACE_WORDS, scene_words
from openplan.scene import history
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import (
    AddBrace,
    AddFurniture,
    OpError,
    RemoveElement,
    UpdateElement,
    apply_op,
)
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import SCHEMA_VERSION, Point2, Point3
from openplan.schemas.elements import Beam, Wall
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import brace_obstacles, check_layout
from tests.factories import attic_project, only_level, rect_room_project

BRACE = f"a {BRACE_WORDS[0]}"


def _brace(level: Level) -> Beam:
    (found,) = [e for e in level.elements.values() if isinstance(e, Beam)]
    return found


def _meta(beam: Beam) -> dict[str, object]:
    return settings(beam)


def _braces(level: Level) -> list[Beam]:
    return sorted(
        (e for e in level.elements.values() if isinstance(e, Beam)), key=lambda b: b.name or ""
    )


def _add(
    project: Project,
    level_id: UUID,
    foot: tuple[float, float],
    head: tuple[float, float],
    **kw: object,
) -> Project:
    op = AddBrace(foot=Point2(x=foot[0], y=foot[1]), head=Point2(x=head[0], y=head[1]), **kw)  # type: ignore[arg-type]
    return apply_op(project, level_id, op)


def _set(project: Project, level_id: UUID, eid: UUID, **fields: object) -> Project:
    return apply_op(project, level_id, UpdateElement(element_id=eid, fields=fields))  # type: ignore[arg-type]


def _attic() -> tuple[Project, UUID, Ridge]:
    project = attic_project(user_defined=True, camera=False)
    level = only_level(project)
    ridge = ridge_span(level, WallGraph.from_level(level))
    assert ridge is not None
    return project, level.id, ridge


def _off(ridge: Ridge, t: float, across: float) -> tuple[float, float]:
    x, y = ridge.at(t)
    nx, ny = ridge.normal
    return x + nx * across, y + ny * across


# ------------------------------------------------------------------ pose, schéma
def test_a_brace_is_two_points_one_undoable_step_and_no_schema_change() -> None:
    project = rect_room_project()
    level = only_level(project)
    after = _add(project, level.id, (1.0, 1.5), (3.0, 1.5))
    beam = _brace(after.levels[level.id])
    assert beam.start.z == 0.0 and beam.end.z == pytest.approx(level.height), "sol → plafond"
    assert (beam.section_width, beam.section_height) == (SECTION_M, SECTION_M) == (0.20, 0.20)
    assert beam.name == "Bois incliné 1"
    assert beam.metadata["brace"] == {"profile": "square", "head_on": "slope", "foot_on": "floor"}
    assert after.history[-1].label == "Poser un bois incliné"
    assert history.undo(after).levels[level.id] == project.levels[level.id]
    # Un Beam existe depuis 0.1.0 : le format ne change pas, le projet relu est le même.
    assert SCHEMA_VERSION == "0.22.0"
    assert Project.model_validate_json(after.model_dump_json()) == after


def test_round_or_square_section_and_limits_said_in_french() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    round_ = _brace(
        _add(project, lid, (1.0, 1.5), (3.0, 1.5), profile="round", width=0.24).levels[lid]
    )
    assert _meta(round_)["profile"] == "round"
    assert round_.section_height == round_.section_width == 0.24, "diamètre"
    with pytest.raises(ValidationError):
        AddBrace(foot=Point2(x=1, y=1), head=Point2(x=2, y=1), width=0.50)  # au plus 40 cm
    with pytest.raises(OpError, match=FOOT_OUTSIDE):
        _add(project, lid, (6.0, 1.5), (3.0, 1.5))
    with pytest.raises(OpError, match=TOO_FLAT):
        _add(project, lid, (1.0, 1.5), (3.0, 1.5), head_on="height", head_height=0.2)
    with pytest.raises(OpError, match="plafond du niveau"):
        _add(project, lid, (1.0, 1.5), (3.0, 1.5), head_on="height", head_height=2.6)


def test_the_foot_stands_on_a_low_wall() -> None:
    """Un muret (mur plus bas que le niveau) porte le pied : il part du dessus du mur ; un mur
    plein ne porte rien."""
    project = rect_room_project()
    level = only_level(project)
    w3 = next(e for e in level.elements.values() if isinstance(e, Wall) and e.name == "W3")
    low = _set(project, level.id, w3.id, height=1.0)
    graph = WallGraph.from_level(low.levels[level.id])
    a, b = graph.wall_axis(w3.id)
    on_wall = ((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
    after = _add(low, level.id, on_wall, (on_wall[0], 1.0))
    beam = _brace(after.levels[level.id])
    assert beam.start.z == pytest.approx(1.0)
    assert _meta(beam)["foot_on"] == "wall"
    with pytest.raises(OpError, match=FOOT_OUTSIDE):
        _add(project, level.id, on_wall, (on_wall[0], 1.0))


# ------------------------------------------------------------------ aimantation
def test_the_head_snaps_to_the_ridge_the_slope_or_another_brace() -> None:
    project, lid, ridge = _attic()
    t = (ridge.t0 + ridge.t1) / 2.0
    near_ridge = _off(ridge, t, SNAP_M * 0.6)
    on_ridge = _add(project, lid, _off(ridge, t, 1.2), near_ridge)
    first = _brace(on_ridge.levels[lid])
    assert _meta(first)["head_on"] == "ridge"
    rx, ry = ridge.at(t)
    assert first.end.x == pytest.approx(rx) and first.end.y == pytest.approx(ry), "sur l'axe"
    level = on_ridge.levels[lid]
    assert 2.5 < first.end.z < level.height, "sous la faîtière"
    # Loin du faîtage : sous le rampant, plus bas.
    slope = _brace(_add(project, lid, _off(ridge, t, 1.5), _off(ridge, t, 0.8)).levels[lid])
    assert _meta(slope)["head_on"] == "slope"
    assert slope.end.z < first.end.z - 0.2
    # Contre le premier bois : la tête prend sa hauteur au point touché.
    mid = ((first.start.x + first.end.x) / 2.0, (first.start.y + first.end.y) / 2.0)
    other = _add(on_ridge, lid, _off(ridge, t + 1.0, 1.2), mid)
    second = next(b for b in _braces(other.levels[lid]) if b.id != first.id)
    assert _meta(second)["head_on"] == "timber"
    assert _meta(second)["head_beam"] == str(first.id)
    assert second.end.z == pytest.approx((first.start.z + first.end.z) / 2.0, abs=0.02)


# ------------------------------------------------------------------ déplacer, modifier
def test_move_modify_and_remove_are_one_step_each() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    after = _add(project, lid, (1.0, 1.5), (3.0, 1.5))
    beam = _brace(after.levels[lid])
    moved = _set(after, lid, beam.id, foot={"x": 1.0, "y": 1.0}, head={"x": 3.0, "y": 1.0})
    m = _brace(moved.levels[lid])
    assert (m.start.y, m.end.y) == (1.0, 1.0) and m.end.z == pytest.approx(2.5)
    assert moved.history[-1].label == "Déplacer le bois incliné"
    assert history.undo(moved).levels[lid] == after.levels[lid]
    changed = _set(moved, lid, beam.id, profile="round", head_height=2.0)
    c = _brace(changed.levels[lid])
    assert c.end.z == pytest.approx(2.0) and _meta(c)["head_on"] == "height"
    assert changed.history[-1].label == "Modifier le bois incliné"
    with pytest.raises(OpError, match="pied et sa tête"):
        _set(changed, lid, beam.id, start={"x": 0, "y": 0, "z": 0})
    with pytest.raises(OpError):
        _set(changed, lid, beam.id, foot={"x": 9.0, "y": 1.0})  # hors de la pièce
    gone = apply_op(changed, lid, RemoveElement(element_id=beam.id))
    assert not _braces(gone.levels[lid])


# ------------------------------------------------------------------ géométrie, 2D
def test_volume_footprint_and_passage_under_the_brace() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    beam = _brace(_add(project, lid, (1.0, 1.5), (3.0, 1.5)).levels[lid])
    points = brace_points(beam)
    assert points[:, 2].min() == pytest.approx(0.0), "pied coupé de niveau, jamais sous le sol"
    assert points[:, 2].max() == pytest.approx(2.5 + 0.1 * math.cos(math.atan2(2.5, 2.0)), abs=0.01)
    shape = brace_footprint(beam)
    assert shape.contains(shape.centroid) and shape.bounds[0] < 1.0 + 1e-6
    # Passage au milieu : axe à 1,25 m, moins la demi-section verticale (0,10 / cos θ).
    cos = 2.0 / math.hypot(2.0, 2.5)
    assert passage(beam) == pytest.approx(1.25 - 0.10 / cos, abs=1e-3)
    assert underside_at(beam, 0.0) == pytest.approx(0.0)
    reach = low_reach(beam)
    assert 0.0 < reach < 2.0 and underside_at(beam, reach / 2.0) == pytest.approx(
        OBSTACLE_HEIGHT_M, abs=0.01
    )
    low = low_footprint(beam)
    assert low is not None and low.area < shape.area
    high = _brace(
        _add(
            project,
            lid,
            (1.0, 1.5),
            (3.0, 1.5),
            foot_height=2.0,
            head_on="height",
            head_height=2.45,
        ).levels[lid]
    )
    assert low_footprint(high) is None, "tout au-dessus de 1,80 m : pas d'obstacle"
    round_ = _brace(_add(project, lid, (1.0, 1.5), (3.0, 1.5), profile="round").levels[lid])
    assert len(brace_points(round_)) > len(points), "section ronde à facettes"


def test_derived_gives_the_plan_drawing_and_the_passage() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    after = _add(project, lid, (1.0, 1.5), (3.0, 1.5))
    beam = _brace(after.levels[lid])
    d = compute_derived(after.levels[lid]).braces[beam.id]
    assert (d.foot.x, d.head.x, d.foot_z) == (1.0, 3.0, 0.0)
    assert d.head_z == pytest.approx(2.5)
    assert d.passage_m == passage(beam) and d.low_reach_m == low_reach(beam)
    assert d.head_on == "slope" and d.profile == "square" and len(d.outline) >= 4


# ------------------------------------------------------------------ contraintes
def _with_furniture(project: Project, lid: UUID, category: str, at: tuple[float, float]) -> Project:
    asset = next(a.id for a in load_catalog().assets if a.category == category)
    return apply_op(project, lid, AddFurniture(asset_id=asset, position=Point2(x=at[0], y=at[1])))


def test_a_piece_of_furniture_under_a_low_brace_is_a_collision_alert() -> None:
    project = rect_room_project()
    lid = only_level(project).id
    braced = _add(project, lid, (2.0, 1.5), (3.8, 1.5))
    beam = _brace(braced.levels[lid])
    hit = _with_furniture(braced, lid, "nightstand", (2.3, 1.5))
    found = [v for v in check_layout(hit) if v.type == "brace_collision"]
    assert len(found) == 1 and beam.id in found[0].element_ids, found
    assert found[0].severity == "medium" and "à décaler" in found[0].message
    clear = _with_furniture(braced, lid, "nightstand", (0.6, 2.5))
    assert not [v for v in check_layout(clear) if v.type == "brace_collision"]


def test_a_low_brace_in_front_of_a_door_is_said_and_blocks_the_passage() -> None:
    project = rect_room_project()  # porte sur W1 (y = 0), de x = 0,50 à 1,33
    lid = only_level(project).id
    braced = _add(project, lid, (0.9, 0.5), (0.9, 2.6))
    found = [v for v in check_layout(braced) if v.type == "brace_blocks_door"]
    assert len(found) == 1 and "porte" in found[0].message
    far = _add(project, lid, (3.2, 2.0), (3.8, 2.0))
    assert not [v for v in check_layout(far) if v.type == "brace_blocks_door"]
    (obstacle,) = brace_obstacles(braced.levels[lid])
    assert obstacle[1].area > 0.0


# ------------------------------------------------------------------ bake, guides, prompt
def _camera(ridge: Ridge, t: float, toward: float) -> RenderCamera:
    x, y = ridge.at(t)
    tx, ty = ridge.at(toward)
    return RenderCamera(
        position=Point3(x=x, y=y, z=1.5),
        yaw=math.atan2(ty - y, tx - x),
        pitch=math.radians(-6.0),
        focal_px=28.8 / 36 * 1280,
        width_px=1280,
        height_px=960,
    )


def test_the_brace_is_baked_as_timber_seen_and_named_only_when_seen() -> None:
    project, lid, ridge = _attic()
    t = ridge.t0 + 0.25 * ridge.length
    after = _add(project, lid, _off(ridge, t, 1.2), _off(ridge, t, 0.05))
    beam = _brace(after.levels[lid])
    bake = bake_level(after, lid, style="realistic")
    timber = [p for p in bake.polyhedra if p.element_id == beam.id]
    assert len(timber) == 1 and timber[0].kind == "timber", "classe « timber » des masques"
    looking = _camera(ridge, ridge.t1 - 0.3, ridge.t0)
    assert visible_pixels(bake, looking).get(str(beam.id), 0) >= VISIBLE_MIN_PX
    assert BRACE in scene_words(bake, after, lid, looking).structure
    away = _camera(ridge, t + 0.6, ridge.t1)
    assert visible_pixels(bake, away).get(str(beam.id), 0) < VISIBLE_MIN_PX
    assert BRACE not in scene_words(bake, after, lid, away).structure
    assert scene_words(bake, after, lid).structure == [], "sans caméra, rien de la structure"


# ------------------------------------------------------------------ offre
def test_placing_a_brace_by_hand_is_pro_moving_it_is_not() -> None:
    assert "add_brace" in PRO_OPS
    project = rect_room_project()
    lid = only_level(project).id
    op = AddBrace(foot=Point2(x=1.0, y=1.5), head=Point2(x=3.0, y=1.5))
    assert is_pro_op(project, lid, op)
    after = apply_op(project, lid, op)
    beam = _brace(after.levels[lid])
    move = UpdateElement(element_id=beam.id, fields={"foot": {"x": 1.0, "y": 1.0}})
    assert not is_pro_op(after, lid, move)
