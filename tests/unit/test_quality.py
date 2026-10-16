"""Contrôle qualité : netteté relative, exposition, résolution."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from openplan.pipeline.quality import (
    BLUR_RATIO_WARN,
    assess_quality,
    downscale,
    laplacian_variance,
    quality_warnings,
    reblur_ratio,
)


def checkerboard(size: int = 256, cell: int = 16) -> NDArray[np.uint8]:
    y, x = np.indices((size, size))
    pattern = ((x // cell + y // cell) % 2) * 255
    return np.stack([pattern] * 3, axis=-1).astype(np.uint8)


def box_blur(pixels: NDArray[np.uint8], radius: int) -> NDArray[np.uint8]:
    out = pixels.astype(np.float32)
    k = 2 * radius + 1
    for axis in (0, 1):
        acc = np.zeros_like(out)
        for shift in range(-radius, radius + 1):
            acc += np.roll(out, shift, axis=axis)
        out = acc / k
    return np.clip(out, 0, 255).astype(np.uint8)


def test_blur_reduces_laplacian_variance() -> None:
    sharp = checkerboard()
    blurred = box_blur(box_blur(sharp, 8), 8)  # deux passes ≈ flou gaussien large
    q_sharp, q_blur = assess_quality(sharp), assess_quality(blurred)
    assert q_sharp["blur_laplacian_var"] > 5 * q_blur["blur_laplacian_var"]
    assert q_sharp["blur_reblur_ratio"] < BLUR_RATIO_WARN < q_blur["blur_reblur_ratio"]
    assert any("floue" in w for w in quality_warnings(q_blur))
    assert not any("floue" in w for w in quality_warnings(q_sharp))


def test_reblur_ratio_is_texture_independent_and_sees_motion_blur() -> None:
    """Un motif fin et un motif large, tous deux nets, donnent le même verdict ; un flou de
    bougé dans une seule direction est vu (pire des deux directions)."""
    rng = np.random.default_rng(0)
    fine = checkerboard(size=512, cell=6)
    coarse = checkerboard(size=512, cell=64)
    noise = rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8)
    for image in (fine, coarse, noise):
        assert assess_quality(image)["blur_reblur_ratio"] < BLUR_RATIO_WARN
    # Bougé horizontal : deux moyennes glissantes de 12 px le long de l'axe 1 seulement.
    moved = coarse.astype(np.float32)
    for _ in range(2):
        acc = np.zeros_like(moved)
        for shift in range(-6, 6):
            acc += np.roll(moved, shift, axis=1)
        moved = acc / 12.0
    moved_u8 = np.clip(moved, 0, 255).astype(np.uint8)
    assert assess_quality(moved_u8)["blur_reblur_ratio"] > BLUR_RATIO_WARN
    assert reblur_ratio(np.zeros((3, 3), dtype=np.float32)) == 0.0
    assert reblur_ratio(np.full((64, 64), 7.0, dtype=np.float32)) == 0.0


def test_exposure_and_resolution_warnings() -> None:
    dark = np.full((100, 120, 3), 5, dtype=np.uint8)
    warnings = quality_warnings(assess_quality(dark))
    assert any("sombre" in w for w in warnings)
    assert any("bouchés" in w for w in warnings)
    assert any("Résolution faible" in w for w in warnings)
    bright = np.full((900, 900, 3), 250, dtype=np.uint8)
    warnings = quality_warnings(assess_quality(bright))
    assert any("claire" in w for w in warnings)
    assert any("brûlés" in w for w in warnings)
    assert not any("Résolution" in w for w in warnings)


def test_downscale_normalises_resolution() -> None:
    big = checkerboard(size=2048, cell=128)
    gray = big[..., 0].astype(np.float32)
    reduced = downscale(gray, 1024)
    assert reduced.shape == (1024, 1024)
    assert downscale(gray[:2, :2], 1024).shape == (2, 2)
    assert laplacian_variance(gray[:2, :2]) == 0.0
    assert quality_warnings({}) == []
