"""Polygones 2D : construction, orientation, aires. Shapely fait le travail lourd."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient

from openplan.schemas.common import Point2


def signed_area(points: Sequence[Point2]) -> float:
    """Aire signée (formule du lacet) : positive si le contour est antihoraire."""
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        p, q = points[i], points[(i + 1) % n]
        total += p.x * q.y - q.x * p.y
    return total / 2.0


def is_ccw(points: Sequence[Point2]) -> bool:
    return signed_area(points) > 0.0


def ensure_ccw(points: Sequence[Point2]) -> list[Point2]:
    return list(points) if is_ccw(points) else list(reversed(points))


def to_polygon(points: Sequence[Point2]) -> Polygon:
    """Polygone Shapely orienté antihoraire, sans point de fermeture dupliqué."""
    if len(points) < 3:
        msg = "un polygone demande au moins trois points"
        raise ValueError(msg)
    return orient(Polygon([(p.x, p.y) for p in points]), sign=1.0)


def to_points(polygon: Polygon) -> list[Point2]:
    """Sommets du contour extérieur, sans le point de fermeture."""
    coords = list(polygon.exterior.coords)[:-1]
    return [Point2(x=float(x), y=float(y)) for x, y in coords]


def largest_polygon(geometry: Any) -> Polygon:
    """Le plus grand polygone d'une géométrie (Polygon, MultiPolygon, ou vide)."""
    if isinstance(geometry, Polygon):
        return geometry
    if isinstance(geometry, MultiPolygon):
        return max(geometry.geoms, key=lambda g: g.area)
    parts = [g for g in getattr(geometry, "geoms", []) if isinstance(g, Polygon)]
    return max(parts, key=lambda g: g.area) if parts else Polygon()
