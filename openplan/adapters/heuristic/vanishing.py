"""Points de fuite par RANSAC sur des segments, puis affinage aux moindres carrés.

Un point de fuite est un vecteur homogène (x, y, w) ; w ≈ 0 signifie « à l'infini »
(lignes parallèles dans l'image). Le résidu d'un segment est l'angle entre sa direction et
la direction de son milieu vers le point de fuite : indépendant de l'échelle de l'image.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.heuristic.lines import segment_lengths

Float = NDArray[np.float64]
IntArray = NDArray[np.intp]
INFINITY_W = 1e-6
"""|w| sous lequel un point de fuite (norme 1) est traité comme à l'infini."""
DEFAULT_THRESHOLD = math.radians(2.0)


@dataclass(frozen=True)
class VanishingPoint:
    homogeneous: tuple[float, float, float]
    """Norme 1."""
    inliers: tuple[int, ...]
    """Indices des segments qui convergent vers ce point."""
    weight: float
    """Somme des longueurs (px) des segments inliers."""

    @property
    def point(self) -> tuple[float, float] | None:
        x, y, w = self.homogeneous
        if abs(w) < INFINITY_W:
            return None
        return (x / w, y / w)

    @property
    def at_infinity(self) -> bool:
        return self.point is None


def segment_lines(segments: Float) -> Float:
    """Droites homogènes (N, 3) des segments, normalisées pour que (a, b) soit unitaire."""
    ones = np.ones((len(segments), 1))
    p0 = np.hstack([segments[:, :2], ones])
    p1 = np.hstack([segments[:, 2:], ones])
    lines = np.cross(p0, p1)
    norm = np.linalg.norm(lines[:, :2], axis=1, keepdims=True)
    normalized: Float = lines / np.maximum(norm, 1e-12)
    return normalized


def angular_residual(segments: Float, vp: Float) -> Float:
    """Angle (rad, dans [0, π/2]) entre chaque segment et la direction milieu → point de fuite."""
    mid = (segments[:, :2] + segments[:, 2:]) / 2.0
    direction = segments[:, 2:] - segments[:, :2]
    direction = direction / np.maximum(np.linalg.norm(direction, axis=1, keepdims=True), 1e-12)
    if abs(vp[2]) < INFINITY_W * np.linalg.norm(vp):
        to_vp = np.broadcast_to(vp[:2] / max(np.linalg.norm(vp[:2]), 1e-12), mid.shape)
    else:
        to_vp = vp[:2] / vp[2] - mid
        to_vp = to_vp / np.maximum(np.linalg.norm(to_vp, axis=1, keepdims=True), 1e-12)
    cos = np.abs(np.sum(direction * to_vp, axis=1))
    residual: Float = np.arccos(np.clip(cos, 0.0, 1.0))
    return residual


def refine_vanishing_point(lines: Float, weights: Float) -> Float:
    """Point minimisant Σ (w_i · l_i · v)² : plus petit vecteur singulier de (w ⊙ L)."""
    weighted = lines * weights[:, None]
    _, _, vt = np.linalg.svd(weighted, full_matrices=False)
    v: Float = vt[-1]
    return v / np.linalg.norm(v)


def find_vanishing_points(
    segments: Float,
    *,
    max_points: int = 3,
    threshold_rad: float = DEFAULT_THRESHOLD,
    iterations: int = 400,
    min_inliers: int = 4,
    seed: int = 0,
) -> list[VanishingPoint]:
    """Jusqu'à `max_points` points de fuite, du plus soutenu au moins soutenu.

    RANSAC séquentiel : après chaque point, ses inliers sont retirés. Les paires sont
    tirées avec une probabilité proportionnelle à la longueur des segments.
    """
    if len(segments) < 2:
        return []
    rng = np.random.default_rng(seed)
    lines = segment_lines(segments)
    lengths = segment_lengths(segments)
    remaining: IntArray = np.arange(len(segments), dtype=np.intp)
    out: list[VanishingPoint] = []
    for _ in range(max_points):
        if len(remaining) < max(2, min_inliers):
            break
        best_ids: IntArray | None = None
        best_weight = 0.0
        probabilities = lengths[remaining] / lengths[remaining].sum()
        subset = segments[remaining]
        for _ in range(iterations):
            i, j = rng.choice(remaining, size=2, replace=False, p=probabilities)
            candidate = np.cross(lines[i], lines[j])
            norm = float(np.linalg.norm(candidate))
            if norm < 1e-12:
                continue
            residual = angular_residual(subset, candidate / norm)
            mask = residual < threshold_rad
            weight = float(lengths[remaining][mask].sum())
            if weight > best_weight:
                best_weight = weight
                best_ids = remaining[mask]
        if best_ids is None or len(best_ids) < min_inliers:
            break
        refined = refine_vanishing_point(lines[best_ids], lengths[best_ids])
        mask = angular_residual(subset, refined) < threshold_rad
        inliers = remaining[mask]
        if len(inliers) < min_inliers:
            inliers = best_ids
            refined = refine_vanishing_point(lines[best_ids], lengths[best_ids])
        out.append(
            VanishingPoint(
                homogeneous=(float(refined[0]), float(refined[1]), float(refined[2])),
                inliers=tuple(int(i) for i in inliers),
                weight=float(lengths[inliers].sum()),
            )
        )
        remaining = np.setdiff1d(remaining, inliers).astype(np.intp)
    return out
