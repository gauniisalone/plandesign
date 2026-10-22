"""KAN-88 : une vraie génération Z-Image sur la carte, guidée par des arêtes de synthèse.

Seulement sur un créneau GPU accordé par le PO : `OPENPLAN_GPU_TEST=1` et les quatre poids
installés (`OPENPLAN_MODELS_DIR`, défaut models/weights) ; sinon le test est sauté, donc jamais
lancé par `make check`. Aucune photo : un couloir de combles dessiné en traits, et le prompt
que l'app écrit pour les combles de test."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.neural import zimage
from openplan.adapters.protocols import FinishRequest
from openplan.pipeline.finish import finish_prompt
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.scene_prompt import scene_words
from tests.factories import attic_project, only_level

WEIGHTS = Path(os.environ.get("OPENPLAN_MODELS_DIR", "models/weights"))

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        os.environ.get("OPENPLAN_GPU_TEST") != "1", reason="créneau GPU du PO : OPENPLAN_GPU_TEST=1"
    ),
    pytest.mark.skipif(not zimage.installed(WEIGHTS), reason="poids Z-Image absents"),
]


def _attic_edges(w: int = 1024, h: int = 1280) -> np.ndarray:
    """Vue dans l'axe d'un faîtage, en traits : pentes, pignon du fond, plancher."""
    import cv2

    img = np.zeros((h, w), np.uint8)
    cx = w // 2
    lines = [
        ((0, int(h * 0.45)), (cx, int(h * 0.12))),
        ((w, int(h * 0.45)), (cx, int(h * 0.12))),
        ((int(w * 0.3), int(h * 0.62)), (cx, int(h * 0.40))),
        ((int(w * 0.7), int(h * 0.62)), (cx, int(h * 0.40))),
        ((int(w * 0.3), int(h * 0.62)), (int(w * 0.7), int(h * 0.62))),
        ((0, h), (int(w * 0.3), int(h * 0.62))),
        ((w, h), (int(w * 0.7), int(h * 0.62))),
        ((int(w * 0.1), int(h * 0.85)), (int(w * 0.35), int(h * 0.78))),
    ]
    for a, b in lines:
        cv2.line(img, a, b, 255, 2)
    return img


def test_zimage_generates_under_5_gb_without_network() -> None:
    project = attic_project()
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    prompt = finish_prompt(
        project, None, level.id, scene=scene_words(bake, project, level.id), engine="zimage"
    )
    edges = _attic_edges()
    finisher = zimage.ZImageFinisher(WEIGHTS)
    result = finisher.finish(
        FinishRequest(
            prompt=prompt,
            image=np.zeros((*edges.shape, 3), np.uint8),
            edges=edges,
            seed=1234,
        )
    )
    assert result.pixels.shape == (1280, 1024, 3)
    assert float(result.pixels.std()) > 10.0, "une image, pas un aplat"
    assert result.peak_vram_mb is not None and result.peak_vram_mb <= 5000 * 1.048576
    assert result.duration_s < zimage.TIMEOUT_S
    out = os.environ.get("OPENPLAN_GPU_TEST_OUT")
    if out:
        from PIL import Image

        Image.fromarray(result.pixels).save(out)
