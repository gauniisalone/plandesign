"""Profondeur métrique réelle sur la fixture rendue : distance au mur de face connue.

Ignoré sans l'extra `neural` ou sans les poids (`openplan models pull ...`). Seuil
volontairement large (30 %) pour un premier chiffre ; la valeur mesurée est reportée dans
docs/evaluation/protocol.md.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.neural.depth_anything import MODEL_ID
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, is_installed
from openplan.adapters.protocols import ImageArray
from openplan.schemas.bake import RenderCamera

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / DEFAULT_WEIGHTS_ROOT
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (FIXTURE / "image.png").is_file(), reason="fixture absente"),
    pytest.mark.skipif(not is_installed(MODEL_ID, WEIGHTS), reason="poids de profondeur absents"),
]


def ray_distance_to_wall(cam: RenderCamera, u: float, v: float, wall_y: float) -> float:
    """Distance le long du rayon du pixel (u, v) jusqu'au plan y = wall_y (mur de face)."""
    cx, cy = cam.width_px / 2, cam.height_px / 2
    # Rayon caméra (x droite, y bas, z avant) → monde : lacet autour de Z, tangage autour de X.
    d = np.array([(u - cx) / cam.focal_px, (v - cy) / cam.focal_px, 1.0])
    d /= np.linalg.norm(d)
    # Caméra à tangage p, lacet yaw : avant = (cos yaw cos p, sin yaw cos p, sin p),
    # droite = (sin yaw, -cos yaw, 0), haut = (-cos yaw sin p, -sin yaw sin p, cos p),
    # et l'axe image y (vers le bas) vaut -haut.
    yaw, p = cam.yaw, cam.pitch
    forward = np.array([math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p)])
    right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
    up = np.array([-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p)])
    world = d[0] * right - d[1] * up + d[2] * forward
    t = (wall_y - cam.position.y) / world[1]
    return float(t)


def test_metric_depth_matches_far_wall_distance() -> None:
    pytest.importorskip("torch")
    from PIL import Image

    from openplan.adapters.neural.depth_anything import DepthAnythingMetric

    cam = RenderCamera.model_validate(json.loads((FIXTURE / "camera.json").read_text()))
    with Image.open(FIXTURE / "image.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    depth = DepthAnythingMetric(WEIGHTS).predict(ImageArray(image_id=uuid4(), pixels=pixels)).depth
    assert depth.shape == pixels.shape[:2]
    # Point sur le mur de face, à gauche de la fenêtre, à mi-hauteur : (u, v) = (150, 500).
    u, v = 150, 500
    expected = ray_distance_to_wall(cam, u, v, wall_y=2.925)
    patch = depth[v - 5 : v + 5, u - 5 : u + 5]
    measured = float(np.median(patch))
    # Le modèle rend une profondeur « z » (distance au plan image), pas la distance le long
    # du rayon : on compare à la composante avant.
    dz = expected * (
        1.0
        / math.sqrt(
            1
            + ((u - cam.width_px / 2) / cam.focal_px) ** 2
            + ((v - cam.height_px / 2) / cam.focal_px) ** 2
        )
    )
    print(f"profondeur estimée {measured:.2f} m, attendue {dz:.2f} m (rayon {expected:.2f} m)")
    assert measured == pytest.approx(dz, rel=0.30)
