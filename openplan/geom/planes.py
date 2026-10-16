"""Ajustement de plans dans un nuage de points issu d'une carte de profondeur.

Rétro-projection : pixel (u, v) de profondeur z → point caméra ((u − cx)·z/f, (v − cy)·z/f, z),
repère caméra x à droite, y vers le bas, z vers l'avant. Puis RANSAC séquentiel : le plan le
mieux soutenu est extrait, ses points retirés, et ainsi de suite. Les triplets sont tirés dans
un voisinage image (trois pixels proches ont plus de chances d'appartenir au même plan).

Un plan est écrit n·p + d = 0 avec n unitaire orienté vers la caméra (origine) : d ≥ 0 est la
distance de la caméra au plan. Le seuil d'appartenance croît avec la profondeur, car l'erreur
d'une profondeur apprise est relative, pas absolue.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

Float = NDArray[np.float64]
Index = NDArray[np.intp]

BASE_TOLERANCE_M = 0.03
RELATIVE_TOLERANCE = 0.02
"""Un point est sur le plan si |n·p + d| < BASE + RELATIVE × z."""
NEIGHBOURHOOD = 0.15
"""Fenêtre de tirage des triplets, en fraction du grand côté de l'image."""
MIN_TRIPLET_SPREAD_M = 0.05
CORE_FRACTION = 0.5
MIN_CORE_SHARE = 0.3
"""Affinage final : ACP des points à moins de la moitié du seuil, s'ils sont assez nombreux."""


@dataclass(frozen=True)
class Plane:
    normal: Float
    """Unitaire, orientée vers la caméra."""
    distance: float
    """Distance de la caméra au plan, en mètres (≥ 0)."""

    def signed_distances(self, points: Float) -> Float:
        out: Float = points @ self.normal + self.distance
        return out

    def height_at(self, x: float, y: float) -> float | None:
        """z du plan à l'aplomb de (x, y) ; None pour un plan vertical."""
        nz = float(self.normal[2])
        if abs(nz) < 1e-6:
            return None
        return -(float(self.normal[0]) * x + float(self.normal[1]) * y + self.distance) / nz


@dataclass(frozen=True)
class FittedPlane:
    plane: Plane
    inliers: Index
    """Indices dans le nuage d'origine."""
    rms_m: float

    @property
    def count(self) -> int:
        return len(self.inliers)


def backproject(
    depth: NDArray[np.float32] | Float,
    focal_px: float,
    principal_point: tuple[float, float],
    stride: int = 4,
) -> tuple[Float, Index]:
    """Nuage (N, 3) en mètres, repère caméra, et pixels (N, 2) = (u, v) correspondants.

    Un pixel sur `stride` dans chaque direction ; profondeurs non finies ou nulles ignorées.
    """
    h, w = depth.shape
    vs, us = np.mgrid[0:h:stride, 0:w:stride]
    z = np.asarray(depth[::stride, ::stride], dtype=np.float64)
    keep = np.isfinite(z) & (z > 0)
    z, us, vs = z[keep], us[keep], vs[keep]
    cx, cy = principal_point
    points: Float = np.column_stack([(us - cx) * z / focal_px, (vs - cy) * z / focal_px, z])
    pixels: Index = np.column_stack([us, vs]).astype(np.intp)
    return points, pixels


def plane_from_points(points: Float) -> Plane | None:
    """Plan des moindres carrés (ACP) d'au moins trois points ; None s'ils sont alignés."""
    if len(points) < 3:
        return None
    centroid = points.mean(axis=0)
    _, singular, vt = np.linalg.svd(points - centroid, full_matrices=False)
    if singular[1] < 1e-9:
        return None
    normal = vt[2]
    d = -float(normal @ centroid)
    if d < 0:
        normal, d = -normal, -d
    return Plane(normal=normal / np.linalg.norm(normal), distance=d)


def tolerances(points: Float) -> Float:
    out: Float = BASE_TOLERANCE_M + RELATIVE_TOLERANCE * points[:, 2]
    return out


def ransac_plane(
    points: Float,
    pixels: Index,
    *,
    iterations: int = 300,
    rng: np.random.Generator,
    window_px: float,
) -> FittedPlane | None:
    """Plan le mieux soutenu du nuage ; triplets tirés dans une fenêtre image."""
    n = len(points)
    if n < 3:
        return None
    tol = tolerances(points)
    best: tuple[int, Plane] | None = None
    for _ in range(iterations):
        first = int(rng.integers(n))
        near = np.flatnonzero(np.abs(pixels - pixels[first]).max(axis=1) <= window_px)
        if len(near) < 3:
            continue
        others = rng.choice(near, size=2, replace=False)
        triplet = points[[first, int(others[0]), int(others[1])]]
        if np.linalg.norm(triplet[1] - triplet[0]) < MIN_TRIPLET_SPREAD_M:
            continue
        candidate = plane_from_points(triplet)
        if candidate is None:
            continue
        count = int((np.abs(candidate.signed_distances(points)) < tol).sum())
        if best is None or count > best[0]:
            best = (count, candidate)
    if best is None:
        return None
    plane = best[1]
    inliers = np.flatnonzero(np.abs(plane.signed_distances(points)) < tol)
    # Deux affinages : ACP des inliers, puis nouveaux inliers.
    for _ in range(2):
        refined = plane_from_points(points[inliers])
        if refined is None:
            break
        plane = refined
        inliers = np.flatnonzero(np.abs(plane.signed_distances(points)) < tol)
    if len(inliers) < 3:
        return None
    # Orientation finale sur la moitié centrale de la bande : les points des plans voisins qui
    # frôlent la bande (bas d'un mur, pied d'une pente) ne font plus basculer la normale.
    core = np.flatnonzero(np.abs(plane.signed_distances(points)) < CORE_FRACTION * tol)
    if len(core) >= 3 and len(core) >= MIN_CORE_SHARE * len(inliers):
        tightened = plane_from_points(points[core])
        if tightened is not None:
            plane = tightened
            inliers = np.flatnonzero(np.abs(plane.signed_distances(points)) < tol)
    residuals = plane.signed_distances(points[inliers])
    return FittedPlane(plane=plane, inliers=inliers, rms_m=float(np.sqrt((residuals**2).mean())))


def fit_planes(
    points: Float,
    pixels: Index,
    image_size: tuple[int, int],
    *,
    max_planes: int = 8,
    min_fraction: float = 0.02,
    iterations: int = 300,
    seed: int = 0,
) -> list[FittedPlane]:
    """Plans extraits séquentiellement, du plus soutenu au moins soutenu.

    S'arrête quand un plan rassemble moins de `min_fraction` du nuage initial, ou après
    `max_planes`. Déterministe pour une graine donnée.
    """
    rng = np.random.default_rng(seed)
    window = NEIGHBOURHOOD * max(image_size)
    remaining = np.arange(len(points), dtype=np.intp)
    minimum = max(3, int(min_fraction * len(points)))
    out: list[FittedPlane] = []
    while len(out) < max_planes and len(remaining) >= minimum:
        fitted = ransac_plane(
            points[remaining], pixels[remaining], iterations=iterations, rng=rng, window_px=window
        )
        if fitted is None or fitted.count < minimum:
            break
        global_inliers = remaining[fitted.inliers]
        out.append(FittedPlane(plane=fitted.plane, inliers=global_inliers, rms_m=fitted.rms_m))
        remaining = np.setdiff1d(remaining, global_inliers, assume_unique=True)
    return out


def angle_between(a: Float, b: Float) -> float:
    """Angle (radians) entre deux vecteurs non nuls."""
    cos = float(a @ b) / max(float(np.linalg.norm(a) * np.linalg.norm(b)), 1e-12)
    return float(np.arccos(np.clip(cos, -1.0, 1.0)))


def rotate_plane(plane: Plane, axes: Float) -> Plane:
    """Même plan exprimé dans le repère dont `axes` (lignes) sont les vecteurs de base."""
    normal: Float = axes @ plane.normal
    return Plane(normal=normal, distance=plane.distance)
