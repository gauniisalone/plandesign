"""« Recalibrer » : une longueur connue met tout le niveau à l'échelle."""

from __future__ import annotations

import math

import pytest

from openplan.geom.scale import CalibrationError
from openplan.pipeline.recalibrate import RecalibrateBody, recalibrate_level
from openplan.scene.ops import UpdateLevel, apply_op
from openplan.schemas import Camera, Source, Wall
from openplan.schemas.common import Point2
from tests.factories import only_level, rect_room_project


def _lengths(project) -> list[float]:  # type: ignore[no-untyped-def]
    level = only_level(project)
    out = []
    for w in level.elements.values():
        if isinstance(w, Wall):
            a = level.vertices[w.start_vertex].position
            b = level.vertices[w.end_vertex].position
            out.append(math.hypot(b.x - a.x, b.y - a.y))
    return sorted(out)


def test_two_points_scale_walls_cameras_and_background() -> None:
    project = rect_room_project()  # 4 × 3 m
    level_id = only_level(project).id
    project = apply_op(
        project,
        level_id,
        UpdateLevel(
            fields={"background_transform": {"scale": 0.01, "tx": 1.0, "ty": 2.0, "rotation": 0.1}}
        ),
    )
    before = _lengths(project)
    # Le côté de 4 m fait en vrai 6 m : tout est multiplié par 1,5.
    after, summary = recalibrate_level(
        project,
        level_id,
        RecalibrateBody(real_m=6.0, a=Point2(x=0.0, y=0.0), b=Point2(x=4.0, y=0.0)),
        actor="user",
    )
    assert summary.factor == pytest.approx(1.5) and summary.what == "la distance cliquée"
    assert _lengths(after) == pytest.approx([v * 1.5 for v in before])
    level = only_level(after)
    assert level.background_transform["scale"] == pytest.approx(0.015)
    assert level.background_transform["tx"] == pytest.approx(1.5)
    assert level.background_transform["rotation"] == pytest.approx(0.1)
    for c in level.elements.values():
        if isinstance(c, Camera):
            assert c.position.z == pytest.approx(
                next(
                    e for e in only_level(project).elements.values() if isinstance(e, Camera)
                ).position.z
            )
    # Les deux points étaient des sommets : la mesure est gardée entre eux, avec sa calibration.
    assert len(after.measurements) == len(project.measurements) + 1
    assert len(after.calibrations) == len(project.calibrations) + 1
    assert after.history[-1].actor == "user" and "Recalibrer" in after.history[-1].label


def test_wall_and_ceiling_modes() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    wall = next(e for e in only_level(project).elements.values() if isinstance(e, Wall))
    after, summary = recalibrate_level(
        project, level_id, RecalibrateBody(real_m=2.0, wall_id=wall.id), actor="user"
    )
    scaled = only_level(after).elements[wall.id]
    assert isinstance(scaled, Wall) and scaled.source is Source.MEASURED
    assert summary.current_m > 0 and summary.factor == pytest.approx(2.0 / summary.current_m)
    # Hauteur sous plafond : le niveau était à 2,50 m estimés, il fait 3,00 m : plan × 1,2.
    before = _lengths(project)
    after, summary = recalibrate_level(
        project, level_id, RecalibrateBody(real_m=3.0, ceiling=True), actor="user"
    )
    level = only_level(after)
    assert summary.factor == pytest.approx(3.0 / 2.5) and summary.what == "la hauteur sous plafond"
    assert level.height == pytest.approx(3.0) and level.height_source is Source.MEASURED
    assert _lengths(after) == pytest.approx([v * 1.2 for v in before])


def test_refusals() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    with pytest.raises(CalibrationError, match="deux points"):
        recalibrate_level(project, level_id, RecalibrateBody(real_m=3.0), actor="user")
    with pytest.raises(CalibrationError, match="trop courte"):
        recalibrate_level(
            project,
            level_id,
            RecalibrateBody(real_m=3.0, a=Point2(x=1.0, y=1.0), b=Point2(x=1.01, y=1.0)),
            actor="user",
        )
    with pytest.raises(CalibrationError, match="invraisemblable"):
        recalibrate_level(
            project,
            level_id,
            RecalibrateBody(real_m=90.0, a=Point2(x=0.0, y=0.0), b=Point2(x=1.0, y=0.0)),
            actor="user",
        )
