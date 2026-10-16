"""Combles estimés sur la fixture rendue : profondeur réelle, géométrie connue.

La fixture est une pièce rectangulaire, pas un grenier : on vérifie que le sol est trouvé et
que la hauteur de caméra retrouvée (1,40 m) tombe dans la marge d'échelle du modèle, et
qu'aucune pente n'est inventée. Ignoré sans l'extra `neural` ou sans les poids.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.neural.depth_anything import MODEL_ID
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, is_installed
from openplan.adapters.protocols import ImageArray
from openplan.pipeline.attic import analyse_attic
from openplan.schemas.bake import RenderCamera

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / DEFAULT_WEIGHTS_ROOT
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (FIXTURE / "image.png").is_file(), reason="fixture absente"),
    pytest.mark.skipif(not is_installed(MODEL_ID, WEIGHTS), reason="poids de profondeur absents"),
]


def test_floor_plane_gives_camera_height() -> None:
    pytest.importorskip("torch")
    from PIL import Image

    from openplan.adapters.neural.depth_anything import DepthAnythingMetric

    cam = RenderCamera.model_validate(json.loads((FIXTURE / "camera.json").read_text()))
    with Image.open(FIXTURE / "image.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    depth = DepthAnythingMetric(WEIGHTS).predict(ImageArray(image_id=uuid4(), pixels=pixels)).depth
    estimate, _planes, cls, _pixels, warnings = analyse_attic(
        depth, cam.focal_px, (cam.width_px / 2, cam.height_px / 2)
    )
    assert cls is not None, warnings
    assert estimate.camera_height is not None
    print(f"hauteur de caméra estimée {estimate.camera_height.text}, vraie {cam.position.z:.2f} m")
    assert estimate.camera_height.value == pytest.approx(cam.position.z, rel=0.30)
    assert estimate.ridge_height is None, "pas de pente dans une pièce droite"
    kinds = [p.kind for p in estimate.planes]
    assert kinds.count("floor") == 1 and "slope" not in kinds
