"""KAN-79 : la grille envoyée par le navigateur est celle que garde KAN-66, et la profondeur
relue sans modèle retombe exactement sur ses valeurs aux pixels gardés."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.protocols import AdapterUnavailableError, ImageArray
from openplan.pipeline.client_depth import StoredDepthEstimator, grid_shape, grid_step
from openplan.pipeline.reference import depth_paths, write_depth


@pytest.mark.parametrize(
    ("w", "h"), [(1280, 960), (4032, 3024), (640, 480), (3000, 4000), (641, 3)]
)
def test_grid_matches_the_server_reduction(tmp_path: Path, w: int, h: int) -> None:
    image_id = uuid4()
    depth = np.ones((h, w), dtype=np.float32)
    write_depth(tmp_path, image_id, depth, 100.0, (w / 2, h / 2), (w, h))
    meta = json.loads(depth_paths(tmp_path, image_id)[1].read_text(encoding="utf-8"))
    assert tuple(meta["depth_size"]) == grid_shape(w, h)[::-1]
    assert meta["scale"] == [1 / grid_step(w, h)] * 2


def test_stored_depth_is_read_back_at_photo_size(tmp_path: Path) -> None:
    image_id = uuid4()
    w, h = 1281, 961  # step 3 : grille 427 × 321
    yy, xx = np.mgrid[0:h, 0:w]
    full = (1.0 + xx / w + 2.0 * yy / h).astype(np.float32)
    write_depth(tmp_path, image_id, full, 900.0, (w / 2, h / 2), (w, h))
    pixels = np.zeros((h, w, 3), dtype=np.uint8)
    out = StoredDepthEstimator(tmp_path).predict(ImageArray(image_id=image_id, pixels=pixels))
    assert out.metric and out.depth.shape == (h, w)
    step = grid_step(w, h)
    # Aux pixels gardés : la valeur gardée (float16) ; ailleurs : l'interpolation (plan exact).
    kept = full[::step, ::step].astype(np.float16).astype(np.float32)
    np.testing.assert_allclose(out.depth[::step, ::step], kept, rtol=1e-6)
    inner = out.depth[: (h - 1) // step * step, : (w - 1) // step * step]
    ref = full[: inner.shape[0], : inner.shape[1]]
    assert float(np.max(np.abs(inner - ref) / ref)) < 2e-3


def test_without_stored_depth_the_estimator_says_so(tmp_path: Path) -> None:
    pixels = np.zeros((4, 4, 3), dtype=np.uint8)
    with pytest.raises(AdapterUnavailableError, match="relancez l'analyse"):
        StoredDepthEstimator(tmp_path).predict(ImageArray(image_id=uuid4(), pixels=pixels))
