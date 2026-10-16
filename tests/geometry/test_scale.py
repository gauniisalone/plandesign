"""Calibration : facteur d'échelle, résidus, incohérences, application au niveau."""

from __future__ import annotations

import math
from uuid import uuid4

import pytest

from openplan.geom.scale import CalibrationError, apply_scale, observations, solve_scale
from openplan.geom.wallgraph import WallGraph
from openplan.schemas import Measurement, MeasurementKind, Opening, Point2, Project, Wall
from tests.factories import by_name, only_level


def _wall_measure(level_wall: Wall, value: float, tolerance: float = 0.01) -> Measurement:
    return Measurement(
        kind=MeasurementKind.WALL_LENGTH,
        value=value,
        tolerance=tolerance,
        element_id=level_wall.id,
    )


def test_single_measurement_gives_direct_factor(rect_project: Project) -> None:
    level = only_level(rect_project)
    (m,) = rect_project.measurements.values()  # W1 = 4.0 sur un mur de 4.0
    cal = solve_scale(level, [m.model_copy(update={"value": 3.1})])
    assert cal.scale_factor == pytest.approx(3.1 / 4.0)
    assert cal.rms_m == pytest.approx(0.0, abs=1e-12)
    assert cal.inconsistent == []
    assert cal.level_id == level.id


def test_consistent_measurements_have_zero_residuals(rect_project: Project) -> None:
    level = only_level(rect_project)
    w1, w2 = by_name(level, "W1", Wall), by_name(level, "W2", Wall)
    cal = solve_scale(level, [_wall_measure(w1, 8.0), _wall_measure(w2, 6.0)])
    assert cal.scale_factor == pytest.approx(2.0)
    assert all(abs(r) < 1e-9 for r in cal.residuals_m.values())
    assert cal.inconsistent == []


def test_inconsistent_measurements_are_flagged(rect_project: Project) -> None:
    level = only_level(rect_project)
    w1, w2 = by_name(level, "W1", Wall), by_name(level, "W2", Wall)
    m1, m2 = _wall_measure(w1, 4.0), _wall_measure(w2, 6.0)  # 4/4 = 1 mais 6/3 = 2
    cal = solve_scale(level, [m1, m2])
    assert cal.scale_factor == pytest.approx(math.sqrt(2.0))
    assert set(cal.inconsistent) == {m1.id, m2.id}
    assert cal.rms_m > 1.0


def test_opening_width_and_vertex_pair_are_usable(rect_project: Project) -> None:
    level = only_level(rect_project)
    door = by_name(level, "door", Opening)  # largeur 0.83
    w2 = by_name(level, "W2", Wall)  # sommets distants de 3.0
    m_door = Measurement(kind=MeasurementKind.DOOR_WIDTH, value=1.66, element_id=door.id)
    m_pts = Measurement(
        kind=MeasurementKind.POINT_TO_POINT,
        value=6.0,
        vertex_ids=(w2.start_vertex, w2.end_vertex),
    )
    obs = observations(level, [m_door, m_pts])
    assert [(o.current, o.target) for o in obs] == [(0.83, 1.66), (3.0, 6.0)]
    assert solve_scale(level, [m_door, m_pts]).scale_factor == pytest.approx(2.0)


def test_unusable_measurements_are_ignored_or_rejected(rect_project: Project) -> None:
    level = only_level(rect_project)
    ghost = Measurement(kind=MeasurementKind.WALL_LENGTH, value=1.0, element_id=uuid4())
    image = Measurement(
        kind=MeasurementKind.POINT_TO_POINT,
        value=1.0,
        image_points=(uuid4(), Point2(x=0, y=0), Point2(x=100, y=0)),
    )
    # Cible inconnue : ignorée. Points image : hors périmètre sans caméra, ignorée aussi.
    assert observations(level, [ghost, image]) == []
    with pytest.raises(CalibrationError, match="aucune mesure"):
        solve_scale(level, [ghost, image])


def test_apply_scale_scales_plan_but_not_vertical_dimensions(rect_project: Project) -> None:
    level = only_level(rect_project)
    scaled = apply_scale(level, 0.5)
    graph = WallGraph.from_level(scaled)
    w1 = by_name(scaled, "W1", Wall)
    door = by_name(scaled, "door", Opening)
    assert graph.wall_length(w1.id) == pytest.approx(2.0)
    assert w1.thickness == 0.15, "l'épaisseur est une valeur métrique, non mise à l'échelle"
    assert door.offset == pytest.approx(0.25) and door.width == pytest.approx(0.415)
    assert door.height == 2.04, "les hauteurs ne sont pas mises à l'échelle"
    assert scaled.height == level.height


def test_apply_scale_identity_and_validation(rect_project: Project) -> None:
    level = only_level(rect_project)
    assert apply_scale(level, 1.0) is level
    with pytest.raises(CalibrationError, match="positif"):
        apply_scale(level, 0.0)


def test_solve_then_apply_makes_measured_wall_exact(rect_project: Project) -> None:
    level = only_level(rect_project)
    (m,) = rect_project.measurements.values()
    target = m.model_copy(update={"value": 3.1})
    cal = solve_scale(level, [target])
    scaled = apply_scale(level, cal.scale_factor)
    assert WallGraph.from_level(scaled).wall_length(target.element_id) == pytest.approx(3.1)  # type: ignore[arg-type]
    assert solve_scale(scaled, [target]).scale_factor == pytest.approx(1.0)
