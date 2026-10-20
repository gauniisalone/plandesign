"""Intersection de solides convexes (KAN-67) : jusqu'où un meuble entre dans le caisson d'un
velux. Chaque solide est un système de demi-espaces n·x + c ≤ 0 ; la mesure est le rayon de la
plus grande boule tenant dans les deux à la fois (programme linéaire), négatif s'ils ne se
touchent pas. Même mesure que les tests de KAN-28 et KAN-63."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import linprog  # type: ignore[import-untyped]
from scipy.spatial import ConvexHull, QhullError  # type: ignore[import-untyped]
from shapely.geometry import MultiPoint, Polygon

Float = NDArray[np.float64]
SEEN_MAX = 20_000
"""Positions gardées par solide avant de vider sa mémoire de contacts."""


def hull_halfspaces(points: Float) -> Float | None:
    """Faces de l'enveloppe convexe : lignes (n, c) avec n·x + c ≤ 0 à l'intérieur, n unitaire ;
    None pour un solide plat."""
    try:
        equations: Float = np.asarray(ConvexHull(points).equations, dtype=np.float64)
    except (QhullError, ValueError):
        return None
    return equations


def prism_halfspaces(polygon: Polygon, z0: float, z1: float) -> Float:
    """Demi-espaces d'un prisme droit convexe (emprise d'un meuble, du sol `z0` au dessus `z1`),
    sans enveloppe à calculer : une face par côté de l'emprise, plus le dessous et le dessus."""
    ring = np.asarray(polygon.exterior.coords, dtype=np.float64)[:-1, :2]
    if Polygon(ring).exterior.is_ccw is False:
        ring = ring[::-1]
    rows = []
    for (x0, y0), (x1, y1) in zip(ring, np.roll(ring, -1, axis=0), strict=True):
        nx, ny = y1 - y0, x0 - x1  # normale sortante d'un contour antihoraire
        length = float(np.hypot(nx, ny))
        if length < 1e-12:
            continue
        nx, ny = nx / length, ny / length
        rows.append((nx, ny, 0.0, -(nx * x0 + ny * y0)))
    rows += [(0.0, 0.0, -1.0, z0), (0.0, 0.0, 1.0, -z1)]
    return np.array(rows, dtype=np.float64)


def overlap_radius(a: Float, b: Float) -> float:
    """Rayon de la plus grande boule dans l'intersection de deux systèmes de demi-espaces."""
    eq = np.vstack([a, b])
    a_ub = np.hstack([eq[:, :3], np.linalg.norm(eq[:, :3], axis=1)[:, None]])
    res = linprog(
        [0.0, 0.0, 0.0, -1.0],
        A_ub=a_ub,
        b_ub=-eq[:, 3],
        bounds=[(None, None)] * 3 + [(None, 1.0)],
    )
    return float(res.x[3]) if res.success else -1.0


@dataclass(frozen=True)
class Solid:
    """Solide convexe préparé une fois : demi-espaces, boîte englobante, plus bas point, ombre
    au sol et directions d'arêtes ; le contact avec une position déjà essayée est gardé (le
    générateur et les règles reposent souvent les mêmes emprises)."""

    points: Float
    halfspaces: Float
    bottom: float
    shadow: Polygon
    lo: Float
    hi: Float
    directions: Float
    seen: dict[tuple[float, ...], float] = field(default_factory=dict, compare=False, hash=False)

    @staticmethod
    def of(points: Float) -> Solid | None:
        halfspaces = hull_halfspaces(points)
        shadow = MultiPoint([(float(x), float(y)) for x, y, _z in points]).convex_hull
        if halfspaces is None or not isinstance(shadow, Polygon):
            return None
        return Solid(
            points,
            halfspaces,
            float(np.min(points[:, 2])),
            shadow,
            points.min(axis=0),
            points.max(axis=0),
            _directions(points),
        )

    def depth_in(self, polygon: Polygon, z0: float, top: float) -> float:
        """De combien le prisme `polygon` × [z0, top] entre dans ce solide : le rayon de la plus
        grande boule tenant dans les deux (m), ou une borne de ce rayon du bon côté quand la
        pénétration suffit à trancher. ≤ 0 s'ils ne se touchent pas."""
        x0, y0, x1, y1 = polygon.bounds
        if (
            top <= self.bottom
            or z0 >= self.hi[2]
            or x1 <= self.lo[0]
            or x0 >= self.hi[0]
            or y1 <= self.lo[1]
            or y0 >= self.hi[1]
        ):
            return -1.0
        ring = np.asarray(polygon.exterior.coords, dtype=np.float64)[:-1, :2]
        key = (*np.round(ring, 6).ravel().tolist(), round(z0, 6), round(top, 6))
        if key not in self.seen:
            if len(self.seen) >= SEEN_MAX:
                self.seen.clear()  # mémoire bornée (serveur de longue durée)
            self.seen[key] = self._depth(polygon, ring, z0, top)
        return self.seen[key]

    def _depth(self, polygon: Polygon, ring: Float, z0: float, top: float) -> float:
        if not polygon.intersects(self.shadow):
            return -1.0
        box = np.array([[x, y, z] for x, y in ring for z in (z0, top)], dtype=np.float64)
        sides = np.diff(np.vstack([ring, ring[:1]]), axis=0)
        normals = np.vstack(
            [
                self.halfspaces[:, :3],
                np.column_stack([sides[:, 1], -sides[:, 0], np.zeros(len(sides))]),
                [[0.0, 0.0, 1.0]],
            ]
        )
        depth = penetration(self.points, box, normals, self.directions)
        # Rayon inscrit r et épaisseur minimale w d'un convexe : w / (2√3) ≤ r ≤ w / 2.
        if depth <= 0.0:
            return -1.0
        if depth <= 2.0 * SURE_TOUCH_M or depth >= 2.0 * math.sqrt(3.0) * SURE_TOUCH_M:
            return depth / (2.0 if depth <= 2.0 * SURE_TOUCH_M else 2.0 * math.sqrt(3.0))
        return overlap_radius(self.halfspaces, prism_halfspaces(polygon, z0, top))


SURE_TOUCH_M = 0.005
"""Seuil des règles (rayon) : la pénétration tranche sans programme linéaire hors de
[2 × 5 mm, 2√3 × 5 mm] ; entre les deux, le rayon exact est calculé."""


def _directions(points: Float) -> Float:
    """Directions de toutes les paires de sommets (les arêtes en font partie), unitaires."""
    i, j = np.triu_indices(len(points), k=1)
    d = points[j] - points[i]
    n = np.linalg.norm(d, axis=1)
    keep = n > 1e-9
    out: Float = d[keep] / n[keep][:, None]
    return out


def penetration(
    a: Float, b: Float, face_normals: Float, directions_a: Float | None = None
) -> float:
    """Pénétration de deux enveloppes convexes (théorème des axes séparateurs) : le plus petit
    recouvrement de leurs projections sur les axes candidats (normales des faces des deux, et
    produits croisés des directions d'arêtes de l'une et de l'autre) ; ≤ 0 s'il existe un axe
    séparateur. Les directions de toutes les paires de sommets contiennent celles des arêtes :
    avec les normales des faces, la liste d'axes est complète et la valeur exacte."""
    da = _directions(a) if directions_a is None else directions_a
    db = _directions(b)
    cross = np.cross(da[:, None, :], db[None, :, :]).reshape(-1, 3)
    norms = np.linalg.norm(cross, axis=1)
    faces = face_normals / np.maximum(np.linalg.norm(face_normals, axis=1), 1e-12)[:, None]
    axes = np.vstack([faces, cross[norms > 1e-9] / norms[norms > 1e-9][:, None]])
    pa, pb = a @ axes.T, b @ axes.T
    overlap = np.minimum(pa.max(axis=0), pb.max(axis=0)) - np.maximum(
        pa.min(axis=0), pb.min(axis=0)
    )
    return float(np.min(overlap))
