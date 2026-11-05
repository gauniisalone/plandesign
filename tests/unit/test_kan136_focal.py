"""KAN-136 : la caméra « Photo » porte la focale qui a lu la géométrie, et le rendu sans focale
prend le grand côté comme l'estimateur. Scènes synthétiques, photos de bruit fabriquées ici avec
une focale EXIF (aucune photo réelle)."""

from __future__ import annotations

import io
import math
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest

from openplan.adapters.exif_camera import ExifCameraEstimator
from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_fusion import fuse_attic
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.depthplan import observe_room, propose_floorplan_from_depth
from openplan.pipeline.ingestion import import_image, load_image
from openplan.pipeline.reference import room_size, write_depth
from openplan.pipeline.render import camera_view, default_focal_px, photo_camera
from openplan.pipeline.room_fusion import fuse_room
from openplan.scene.factory import new_project
from openplan.schemas import Camera, ImageRef
from openplan.schemas.common import Point3, Source
from openplan.schemas.scene import Project
from tests.attic_scenes import AXIAL_A, AXIAL_B, OBLIQUE, Attic
from tests.factories import only_level
from tests.room_scenes import rectangle
from tests.synthetic_depth import SyntheticCamera, attic_faces, box_faces, render_depth

FOCAL_35MM = 18.0
"""Grand-angle franc, loin du repli de 28,8 mm : une focale perdue se verrait."""
ROOM = rectangle(4.0, 3.5)
OPPOSITE = [(0.5, 0.5, 1.5, 40.0, -12.0), (3.5, 3.0, 1.5, 220.0, -12.0)]
FACING = [(2.0, 0.5, 1.5, 90.0, -5.0), (2.0, 3.0, 1.5, 270.0, -5.0)]


def _jpeg(width: int, height: int, seed: int) -> bytes:
    """Bruit (aucune ligne de fuite) portant FocalLengthIn35mmFilm, comme une photo de téléphone."""
    from PIL import Image

    rng = np.random.default_rng(seed)
    pixels = rng.integers(90, 160, size=(height, width, 3), dtype=np.uint8)
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0xA405] = int(FOCAL_35MM)
    buffer = io.BytesIO()
    Image.fromarray(pixels).save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def _cam(spec: tuple[float, float, float, float, float], size: tuple[int, int]) -> SyntheticCamera:
    x, y, z, yaw, pitch = spec
    return SyntheticCamera(
        position=(x, y, z),
        yaw=math.radians(yaw),
        pitch=math.radians(pitch),
        focal_px=FOCAL_35MM / 36.0 * max(size),
        width_px=size[0],
        height_px=size[1],
    )


def _photos(
    project_dir: Path,
    faces: list,
    specs: list[tuple[float, float, float, float, float]],
    size: tuple[int, int],
) -> tuple[Project, UUID, list[UUID]]:
    project = new_project("KAN-136")
    ids: list[UUID] = []
    for i, spec in enumerate(specs):
        cam = _cam(spec, size)
        project, ref, _ = import_image(
            project, project_dir, _jpeg(*size, seed=10 + i), original_name=f"f{i}.jpg"
        )
        assert ref.focal_35mm == FOCAL_35MM  # l'import la garde (KAN-136, question 1)
        write_depth(
            project_dir,
            ref.id,
            render_depth(faces, cam),
            cam.focal_px,
            cam.principal_point,
            size,
            model_id="synthetic",
        )
        ids.append(ref.id)
    return project, next(iter(project.levels)), ids


def _photo_cameras(project: Project, level: UUID) -> list[Camera]:
    elements = project.levels[level].elements.values()
    return [e for e in elements if isinstance(e, Camera) and e.image_id is not None]


@pytest.mark.parametrize("size", [(320, 240), (240, 320)], ids=["paysage", "portrait"])
@pytest.mark.parametrize("specs", [OPPOSITE, FACING], ids=["murs-communs", "face-a-face"])
def test_room_fusion_cameras_carry_the_focal_that_read_the_room(
    tmp_path: Path, size: tuple[int, int], specs: list
) -> None:
    project, level, ids = _photos(tmp_path, box_faces(4.0, 3.5, 2.5), specs, size)
    run = fuse_room(project, tmp_path, level, ids)
    assert run.applied
    expected = FOCAL_35MM / 36.0 * max(size)
    cameras = _photo_cameras(run.project, level)
    assert {c.image_id for c in cameras} == set(ids)
    assert all(c.focal_px == pytest.approx(expected, rel=1e-6) for c in cameras)
    # La géométrie a été lue à cette focale : la pièce a sa vraie taille.
    room = room_size(run.project, level)
    assert room is not None
    assert sorted((room.length_m, room.width_m)) == pytest.approx(
        sorted((ROOM.length, ROOM.width)), rel=0.05
    )
    # Et le rendu « depuis la photo » la reprend telle quelle.
    _camera, render = photo_camera(run.project, level)
    assert render.focal_px / max(render.width_px, render.height_px) == pytest.approx(
        FOCAL_35MM / 36.0, rel=1e-3
    )


def test_attic_fusion_cameras_and_geometry_carry_the_focal(tmp_path: Path) -> None:
    attic = Attic()
    faces = attic_faces(
        length=attic.length, width=attic.width, ridge_height=attic.ridge, eave_height=attic.eave
    )
    size = (320, 240)
    project, level, ids = _photos(tmp_path, faces, [AXIAL_A, AXIAL_B, OBLIQUE], size)
    run = fuse_attic(project, tmp_path, level, ids)
    assert run.applied, run.notices
    expected = FOCAL_35MM / 36.0 * max(size)
    cameras = _photo_cameras(run.project, level)
    assert {c.image_id for c in cameras} == set(ids)
    assert all(c.focal_px == pytest.approx(expected, rel=1e-6) for c in cameras)


def test_apply_attic_puts_the_geometry_focal_on_the_camera() -> None:
    project = new_project("combles")
    level = only_level(project).id
    base = {
        "length": Estimate(value=8.0, sigma=0.3),
        "width": Estimate(value=6.0, sigma=0.3),
        "ridge_height": Estimate(value=2.5, sigma=0.3),
        "eave_height": Estimate(value=0.0, sigma=0.1),
        "camera_height": Estimate(value=1.5, sigma=0.1),
    }
    with_focal, _ = apply_attic(project, level, AtticGeometry(**base, focal_px=812.5))
    without, _ = apply_attic(project, level, AtticGeometry(**base))
    focal = [c.focal_px for c in only_level(with_focal).elements.values() if isinstance(c, Camera)]
    none = [c.focal_px for c in only_level(without).elements.values() if isinstance(c, Camera)]
    assert focal == [812.5]
    assert none == [None]  # sans focale connue : le repli du rendu, inchangé


def _portrait_project(tmp_path: Path) -> tuple[Project, UUID, UUID, ImageRef]:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (240, 320), (120, 110, 100)).save(buffer, format="PNG")
    project, ref, _ = import_image(new_project("portrait"), tmp_path, buffer.getvalue())
    level = only_level(project)
    camera = Camera(
        name="Photo",
        position=Point3(x=1.0, y=1.0, z=1.5),
        yaw=0.0,
        pitch=0.0,
        image_id=ref.id,
        source=Source.ESTIMATED,
        confidence=0.4,
    )
    lvl = level.model_copy(update={"elements": {**level.elements, camera.id: camera}})
    return project.model_copy(update={"levels": {level.id: lvl}}), level.id, camera.id, ref


def test_portrait_without_focal_renders_at_the_estimator_focal(tmp_path: Path) -> None:
    """Avant KAN-136 : 0,8 × la largeur, soit 21,6 mm équivalent en portrait, alors que la
    géométrie avait été lue à 0,8 × le grand côté (28,8 mm)."""
    project, level, camera_id, ref = _portrait_project(tmp_path)
    estimator = ExifCameraEstimator().estimate(load_image(tmp_path, ref)).focal_px
    assert default_focal_px(240, 320) == pytest.approx(estimator)
    for _camera, render in (photo_camera(project, level), camera_view(project, level, camera_id)):
        assert (render.width_px, render.height_px) == (240, 320)
        assert render.focal_px == pytest.approx(estimator)
        assert render.focal_px / 320 * 36.0 == pytest.approx(28.8)


def test_depthplan_keeps_the_focal_it_was_given() -> None:
    """Non-régression de depthplan.py:655 : une photo seule garde sa focale sur la caméra."""
    cam = SyntheticCamera(
        position=(1.0, 0.5, 1.4),
        yaw=math.radians(45),
        pitch=math.radians(-12),
        focal_px=500.0,
        width_px=960,
        height_px=720,
    )
    obs, _ = observe_room(
        render_depth(box_faces(4.0, 3.0, 2.5), cam), cam.focal_px, cam.principal_point
    )
    assert obs is not None
    ref = ImageRef(
        path="images/p.png", sha256="0" * 64, width_px=960, height_px=720, exif_stripped=True
    )
    project = new_project("profondeur").model_copy(update={"images": {ref.id: ref}})
    level = only_level(project).id
    after, _ = propose_floorplan_from_depth(
        project, level, obs, image_id=ref.id, camera_height_m=1.4, focal_px=cam.focal_px
    )
    assert [c.focal_px for c in _photo_cameras(after, level)] == [500.0]
