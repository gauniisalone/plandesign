"""KAN-130 : le cône d'une caméra de photo dans le SVG du plan (survol dans l'espace de travail)."""

from __future__ import annotations

import math
from uuid import uuid4

from openplan.pipeline.export import FOV_LENGTH_M, camera_cone, render_svg
from openplan.schemas.common import Point3, Source
from openplan.schemas.elements import Camera
from openplan.schemas.scene import Level, Project
from tests.factories import cut_corner_project, only_level


def _with_camera(focal_px: float | None = None) -> tuple[Project, Level, Camera]:
    project = cut_corner_project()
    level = only_level(project)
    camera = Camera(
        position=Point3(x=1.0, y=1.0, z=1.5),
        yaw=0.0,
        pitch=0.0,
        focal_px=focal_px,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    level.elements[camera.id] = camera
    return project, level, camera


def test_the_cone_follows_the_camera_and_the_plan_canvas_rule() -> None:
    project, level, camera = _with_camera()
    cone = camera_cone(project, level, camera.id)
    assert cone is not None
    tip, left, right = cone
    assert (tip.x, tip.y) == (1.0, 1.0)
    # Sans focale ni photo : 36° de chaque côté, sur 1,5 m.
    assert math.isclose(math.hypot(left.x - 1.0, left.y - 1.0), FOV_LENGTH_M)
    assert math.isclose(math.atan2(right.y - 1.0, right.x - 1.0), math.pi / 5)
    assert math.isclose(math.atan2(left.y - 1.0, left.x - 1.0), -math.pi / 5)


def test_the_svg_draws_the_cone_only_when_asked() -> None:
    project, level, camera = _with_camera()
    assert "camera-cone" not in render_svg(project, level.id).split("</style>")[1]
    drawn = render_svg(project, level.id, camera_id=camera.id).split("</style>")[1]
    assert drawn.count('class="camera-cone"') == 1 and drawn.count('class="camera-dot"') == 1


def test_an_unknown_camera_draws_nothing() -> None:
    project, level, _ = _with_camera()
    other = uuid4()
    assert camera_cone(project, level, other) is None
    assert "camera-cone" not in render_svg(project, level.id, camera_id=other).split("</style>")[1]
