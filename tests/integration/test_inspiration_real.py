"""Génération réelle (Stable Diffusion 1.5 + ControlNet profondeur) sur la fixture.

Ignoré sans l'extra `neural`, sans les poids ou sans la licence acceptée. Vérifie la chaîne
et mesure temps et VRAM ; la qualité de l'image n'est pas testable automatiquement.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.neural.inspiration import (
    CONTROLNET_MODEL_ID,
    CONTROLNET_SENTINEL,
    SD_MODEL_ID,
    SD_SENTINEL,
)
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, is_installed, license_accepted
from openplan.adapters.protocols import ImageArray, ImageGenerationRequest

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
ROOT = Path(__file__).resolve().parents[2]
WEIGHTS = ROOT / DEFAULT_WEIGHTS_ROOT
READY = (
    is_installed(SD_MODEL_ID, WEIGHTS, SD_SENTINEL)
    and is_installed(CONTROLNET_MODEL_ID, WEIGHTS, CONTROLNET_SENTINEL)
    and license_accepted(SD_MODEL_ID, WEIGHTS)
    and license_accepted(CONTROLNET_MODEL_ID, WEIGHTS)
)
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not (FIXTURE / "image.png").is_file(), reason="fixture absente"),
    pytest.mark.skipif(not READY, reason="poids de diffusion absents ou licence non acceptée"),
]


def test_generate_inspiration_from_fixture_depth() -> None:
    pytest.importorskip("torch")
    pytest.importorskip("diffusers")
    from PIL import Image

    from openplan.adapters.neural.depth_anything import DepthAnythingMetric
    from openplan.adapters.neural.inspiration import DepthGuidedDiffusion
    from openplan.pipeline.depth import depth_to_image

    with Image.open(FIXTURE / "image.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    image = ImageArray(image_id=uuid4(), pixels=pixels)
    depth = DepthAnythingMetric(WEIGHTS, device="cuda").predict(image)
    generator = DepthGuidedDiffusion(WEIGHTS, device="cuda")
    result = generator.generate(
        ImageGenerationRequest(
            prompt="photorealistic empty bedroom, tiled floor, window, soft daylight",
            control=depth_to_image(depth.depth),
            seed=1,
            steps=12,
            max_side=512,
        )
    )
    assert result.pixels.shape == (384, 512, 3) and result.pixels.dtype == np.uint8
    assert result.pixels.std() > 10, "image non uniforme"
    assert result.model_ids == [SD_MODEL_ID, CONTROLNET_MODEL_ID]
    vram = f"{result.peak_vram_mb / 1024:.1f} Go" if result.peak_vram_mb else "CPU"
    print(f"génération 12 étapes 512×384 : {result.duration_s:.1f} s, VRAM {vram}")
