"""Roulis : la fixture commise est prise appareil droit ; ici Blender rend la même pièce avec
un roulis connu et la détection heuristique doit le retrouver, avec le bon signe (convention
`CameraParameters` : roulis positif = horizon tourné dans le sens antihoraire sur l'image).

Ignoré sans Blender. Rendu Cycles 960 × 720, 32 échantillons débruités (GPU si présent).
"""

from __future__ import annotations

import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.heuristic.box_detector import HeuristicBoxDetector
from openplan.adapters.protocols import ImageArray
from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import find_blender, run_blender
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import only_level, rect_room_project

BLENDER = find_blender(Settings.from_env().blender_bin)
ROLL = math.radians(5.0)
CAMERA = RenderCamera(
    position=Point3(x=0.7, y=0.6, z=1.4),
    yaw=0.8727,
    pitch=-0.2094,
    focal_px=675.0,  # même angle de champ que la fixture (900 px pour 1280 px de large)
    width_px=960,
    height_px=720,
    roll=ROLL,
)


def angle_diff(a: float, b: float) -> float:
    d = (a - b) % (2 * math.pi)
    return min(d, 2 * math.pi - d)


@pytest.mark.blender
@pytest.mark.slow
@pytest.mark.skipif(BLENDER is None, reason="Blender absent")
def test_roll_is_recovered_with_its_sign(tmp_path: Path) -> None:
    from PIL import Image

    project = rect_room_project(name="Fixture roulis")
    result = run_blender(
        project,
        only_level(project).id,
        tmp_path,
        blender_bin=str(BLENDER),
        camera=CAMERA,
        samples=32,
        denoise=True,
        gpu=True,
    )
    preview = next(a for a in result.artifacts if a.name == "preview.png")
    with Image.open(preview.path) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    analysis = HeuristicBoxDetector().analyse(ImageArray(image_id=uuid4(), pixels=pixels))
    assert analysis.frame is not None, analysis.warnings
    cam = analysis.camera
    print(
        f"roulis {math.degrees(cam.roll):.2f}° (vrai {math.degrees(ROLL):.1f}°), "
        f"tangage {math.degrees(cam.pitch):.2f}°, focale {cam.focal_px:.1f} px, "
        f"{preview.description}"
    )
    assert angle_diff(cam.roll, ROLL) < math.radians(2.0), f"roulis {math.degrees(cam.roll):.1f}°"
    assert angle_diff(cam.pitch, CAMERA.pitch) < math.radians(2.0)
    assert cam.focal_px == pytest.approx(CAMERA.focal_px, rel=0.10)
