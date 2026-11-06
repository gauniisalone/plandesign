"""KAN-141 : « Modifier » une vue de pièce (maquette 2fb2691) : hauteur d'œil et focale réglées à
la main, la vue devient celle de l'utilisateur et n'est plus recalculée ; « Recalculer » lui
rend la vue calculée. Plans de test seulement."""

from __future__ import annotations

import math
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openplan.pipeline.generated import generate_plan
from openplan.pipeline.programme import read_programme
from openplan.pipeline.render import NoCameraError, camera_view
from openplan.pipeline.room_views import (
    ADDED_KEY,
    ON_KEY,
    ROOM_KEY,
    add_room_view,
    apply_room_views,
    is_room_view,
    reset_room_view,
)
from openplan.pipeline.views import focal_px
from openplan.scene.factory import new_project
from openplan.scene.ops import UpdateElement, apply_op
from openplan.schemas.common import Source
from openplan.schemas.elements import Camera
from openplan.schemas.scene import Project
from tests.factories import only_level

T3 = "Un appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau"


def _with_views() -> Project:
    reading = read_programme(T3, None)
    assert reading.programme is not None
    project = new_project("Essai")
    level_id = only_level(project).id
    project, _ = generate_plan(project, level_id, reading.programme)
    project, _ = apply_room_views(project, level_id, actor="user")
    return project


def _cam(project: Project, camera_id: object) -> Camera:
    cam = only_level(project).elements[camera_id]  # type: ignore[index]
    assert isinstance(cam, Camera)
    return cam


def _first_room_view(project: Project) -> Camera:
    return sorted(
        (e for e in only_level(project).elements.values() if is_room_view(e)),
        key=lambda c: c.name or "",
    )[0]  # type: ignore[return-value]


def _edit(project: Project, cam: Camera, *, eye: float, mm: float) -> Project:
    level_id = only_level(project).id
    position = {"x": cam.position.x, "y": cam.position.y, "z": eye}
    project = apply_op(
        project, level_id, UpdateElement(element_id=cam.id, fields={"position": position})
    )
    return apply_op(
        project, level_id, UpdateElement(element_id=cam.id, fields={"focal_px": focal_px(mm)})
    )


def test_eye_and_focal_set_by_hand_make_the_view_the_users_and_reach_the_render() -> None:
    project = _with_views()
    level_id = only_level(project).id
    cam = _first_room_view(project)
    project = _edit(project, cam, eye=1.30, mm=35.0)
    edited = _cam(project, cam.id)
    assert edited.source is Source.USER_DEFINED
    assert edited.position.z == pytest.approx(1.30)
    _, render = camera_view(project, level_id, cam.id)
    assert render.focal_px / max(render.width_px, render.height_px) * 36.0 == pytest.approx(35.0)
    # Recalculer toutes les vues ne la touche pas.
    again, _ = apply_room_views(project, level_id, actor="user")
    assert _cam(again, cam.id) == edited


def test_recompute_gives_back_the_computed_view_and_keeps_its_tick() -> None:
    project = _with_views()
    level_id = only_level(project).id
    cam = _first_room_view(project)
    project = apply_op(
        project,
        level_id,
        UpdateElement(element_id=cam.id, fields={"metadata": {**cam.metadata, ON_KEY: False}}),
    )
    project = _edit(project, cam, eye=1.25, mm=33.0)
    reset, back = reset_room_view(project, level_id, cam.id, actor="user")
    assert back.id == cam.id and back.name == cam.name
    assert back.source is Source.GENERATED
    for field in ("x", "y", "z"):
        assert getattr(back.position, field) == pytest.approx(getattr(cam.position, field))
    assert back.yaw == pytest.approx(cam.yaw) and back.focal_px == pytest.approx(cam.focal_px)
    assert back.metadata[ON_KEY] is False, "la case reste"
    assert reset.history[-1].label == f"Recalculer la vue : {cam.name}"


def test_recompute_an_added_view_keeps_it_apart_from_the_first() -> None:
    project = _with_views()
    level_id = only_level(project).id
    first = _first_room_view(project)
    room_id = UUID(str(first.metadata[ROOM_KEY]))
    project, extra = add_room_view(project, level_id, room_id, actor="user")
    project = _edit(project, extra, eye=1.4, mm=24.0)
    _, back = reset_room_view(project, level_id, extra.id, actor="user")
    assert back.metadata[ADDED_KEY] is True
    moved = math.hypot(back.position.x - first.position.x, back.position.y - first.position.y)
    turned = abs(
        math.degrees(math.atan2(math.sin(back.yaw - first.yaw), math.cos(back.yaw - first.yaw)))
    )
    assert moved >= 1.0 or turned >= 35.0


def test_only_a_room_view_can_be_recomputed() -> None:
    project = _with_views()
    level_id = only_level(project).id
    with pytest.raises(NoCameraError):
        reset_room_view(project, level_id, uuid4(), actor="user")


def test_the_reset_route(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from openplan.api.app import create_app
    from openplan.api.settings import Settings
    from openplan.scene.store import ProjectStore

    project = _with_views()
    level = only_level(project)
    cam = _first_room_view(project)
    project = _edit(project, cam, eye=1.3, mm=30.0)
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    base = f"/api/projects/{project.id}/levels/{level.id}/views/rooms/camera"
    with TestClient(app) as client:
        r = client.post(f"{base}/{cam.id}/reset")
        assert r.status_code == 200, r.text
        back = r.json()["project"]["levels"][str(level.id)]["elements"][str(cam.id)]
        assert back["source"] == "generated"
        assert client.post(f"{base}/{uuid4()}/reset").status_code in (404, 409)
