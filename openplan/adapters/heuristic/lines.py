"""Segments de droite par LSD (Line Segment Detector, OpenCV), sans poids à télécharger."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.protocols import AdapterUnavailableError

MIN_LENGTH_RATIO = 0.015
"""Longueur minimale d'un segment conservé, en fraction du grand côté de l'image."""


def detect_segments(
    pixels: NDArray[np.uint8], *, min_length_px: float | None = None
) -> NDArray[np.float64]:
    """Segments (N, 4) en pixels : x0, y0, x1, y1. Vide si aucun."""
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - dépend de l'environnement
        msg = "OpenCV absent : installer l'extra vision (uv sync --extra vision)"
        raise AdapterUnavailableError(msg) from exc
    gray = cv2.cvtColor(pixels, cv2.COLOR_RGB2GRAY)
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    found = detector.detect(gray)[0]
    segments = np.asarray(found if found is not None else [], dtype=np.float64).reshape(-1, 4)
    if min_length_px is None:
        min_length_px = MIN_LENGTH_RATIO * max(gray.shape)
    return segments[segment_lengths(segments) >= min_length_px]


def segment_lengths(segments: NDArray[np.float64]) -> NDArray[np.float64]:
    lengths: NDArray[np.float64] = np.hypot(
        segments[:, 2] - segments[:, 0], segments[:, 3] - segments[:, 1]
    )
    return lengths
