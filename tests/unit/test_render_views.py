"""Écran Rendre (R1) : points de vue libres, caméra de rendu, estimation, galerie."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from openplan.pipeline.render import (
    RENDER_SIDECAR,
    NoCameraError,
    RenderEntry,
    camera_view,
    daylight_spec,
    describe_north,
    kelvin_to_rgb,
    list_renders,
    render_expectation,
)
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import AddCamera, UpdateElement, apply_op
from openplan.schemas import Source
from openplan.schemas.common import Point2
from openplan.schemas.elements import Camera
from tests.factories import attic_project, only_level, rect_room_project


def test_add_camera_is_a_user_viewpoint_at_eye_height() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    after = apply_op(project, level_id, AddCamera(position=Point2(x=2.0, y=1.5), yaw=0.5))
    cameras = [e for e in only_level(after).elements.values() if isinstance(e, Camera)]
    assert len(cameras) == 1
    cam = cameras[0]
    assert cam.name == "Point de vue 1" and cam.image_id is None
    assert (cam.position.x, cam.position.y, cam.position.z) == (2.0, 1.5, 1.5)
    assert cam.yaw == 0.5 and cam.source is Source.USER_DEFINED
    assert after.history[-1].actor == USER_ACTOR and "point de vue" in after.history[-1].label
    # Un second point de vue est numéroté ; déplacer et viser passent par update_element.
    again = apply_op(after, level_id, AddCamera(position=Point2(x=1.0, y=1.0)))
    names = sorted(e.name for e in only_level(again).elements.values() if isinstance(e, Camera))
    assert names == ["Point de vue 1", "Point de vue 2"]
    moved = apply_op(
        again,
        level_id,
        UpdateElement(
            element_id=cam.id, fields={"position": {"x": 3.0, "y": 2.0, "z": 1.5}, "yaw": 1.0}
        ),
    )
    cam2 = only_level(moved).elements[cam.id]
    assert isinstance(cam2, Camera) and cam2.position.x == 3.0 and cam2.yaw == 1.0


def test_camera_view_defaults_for_a_free_viewpoint_and_uses_the_photo_size() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(project, level_id, AddCamera(position=Point2(x=2.0, y=1.5)))
    cam = next(e for e in only_level(project).elements.values() if isinstance(e, Camera))
    camera, render = camera_view(project, level_id, cam.id)
    assert camera.id == cam.id
    assert (render.width_px, render.height_px) == (1280, 960)
    assert render.focal_px == pytest.approx(0.8 * 1280)
    with pytest.raises(NoCameraError, match="point de vue inconnu"):
        camera_view(project, level_id, uuid4())
    # Caméra des combles de la fabrique : rendue aussi, grand côté borné à 1600 px.
    attic = attic_project()
    lid = only_level(attic).id
    attic_cam = next(e for e in only_level(attic).elements.values() if isinstance(e, Camera))
    _, render2 = camera_view(attic, lid, attic_cam.id)
    assert max(render2.width_px, render2.height_px) <= 1600


def test_expectation_grows_with_samples_and_says_the_device() -> None:
    fast_gpu, msg_gpu = render_expectation("cuda", 16)
    normal_gpu, _ = render_expectation("cuda", 64)
    fine_cpu, msg_cpu = render_expectation("cpu", 256)
    assert fast_gpu < normal_gpu < fine_cpu
    assert "GPU" in msg_gpu and "processeur" in msg_cpu
    assert normal_gpu == pytest.approx(10.0)
    assert render_expectation("cpu", 64)[0] == pytest.approx(44.0)


def test_list_renders_reads_sidecars_newest_first_and_skips_missing_images(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for index, (name, with_image) in enumerate(
        [("Photo", True), ("Point de vue 1", True), ("Perdu", False)]
    ):
        run_dir = runs / str(uuid4()) / "blender"
        run_dir.mkdir(parents=True)
        entry = RenderEntry(
            run_id=uuid4(),
            camera_id=uuid4(),
            camera_name=name,
            samples=64,
            device="OPTIX",
            render_s=9.0,
            width_px=1280,
            height_px=960,
            created_at=datetime(2026, 9, 28, 1, index, tzinfo=UTC),
        )
        (run_dir / RENDER_SIDECAR).write_text(entry.model_dump_json(), encoding="utf-8")
        if with_image:
            (run_dir / "preview.png").write_bytes(b"\x89PNG")
    (runs / "corrompu" / "blender").mkdir(parents=True)
    (runs / "corrompu" / "blender" / RENDER_SIDECAR).write_text("{", encoding="utf-8")
    entries = list_renders(runs)
    assert [e.camera_name for e in entries] == ["Point de vue 1", "Photo"]
    assert json.loads(entries[0].model_dump_json())["device"] == "OPTIX"
    assert list_renders(tmp_path / "absent") == []


def test_daylight_presets_follow_the_north_of_the_plan() -> None:
    # Nord en haut du plan (90°) : midi au sud = vers le bas, matin à l'est = vers la droite,
    # soir à l'ouest = vers la gauche ; le soleil du soir est bas et chaud.
    midi = daylight_spec("midi", 90.0)
    assert midi["sun"] is not None and midi["north"] == "nord vers le haut du plan"
    dx, dy, dz = midi["sun"]["direction"]
    assert abs(dx) < 1e-9 and dy < 0 and dz > 0.8
    matin = daylight_spec("matin", 90.0)["sun"]
    soir = daylight_spec("soir", 90.0)["sun"]
    assert matin is not None and soir is not None
    assert matin["direction"][0] > 0.8 and soir["direction"][0] < -0.8
    assert soir["elevation_deg"] < matin["elevation_deg"] < midi["sun"]["elevation_deg"]
    r, g, b = soir["color"]
    assert r > g > b, "soleil du soir chaud"
    rn, _gn, bn = midi["sun"]["color"]
    assert abs(rn - bn) < 0.15, "soleil de midi presque blanc"
    # Nord vers la droite (0°) : le midi vient de la gauche.
    tourne = daylight_spec("midi", 0.0)
    assert tourne["sun"] is not None and tourne["sun"]["direction"][0] < -0.5
    assert tourne["north"] == "nord vers la droite du plan"
    nuit = daylight_spec("nuit", 90.0)
    assert nuit["sun"] is None and nuit["sky_strength"] < 0.5 and nuit["interior_key"] > 300
    assert describe_north(45.0) == "nord à 45°"
    assert kelvin_to_rgb(6500.0) == pytest.approx((1.0, 0.976, 0.992), abs=0.03)
