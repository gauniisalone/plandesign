"""KAN-92 : zones de sol surélevées (estrade, marche, plancher technique).

Une zone est un quadrilatère simple posé sur le sol d'une pièce, élevé de 0 à 1,20 m. Elle a
ses propres sommets : elle ne touche ni le graphe des murs, ni la détection ni la surface des
pièces. Un meuble dont le centre est sur une zone monte avec elle (calculé ici, jamais stocké).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from shapely.geometry import Point, Polygon

MAX_ELEVATION_M = 1.20
MIN_AREA_M2 = 0.04
"""Plus petite zone acceptée (20 × 20 cm) : en dessous, un clic de travers, pas une estrade."""
FLAT_M = 0.005
"""Épaisseur montrée en 3D d'une zone à 0 m, pour qu'on puisse attraper son dessus."""


class _Point(Protocol):
    @property
    def x(self) -> float: ...
    @property
    def y(self) -> float: ...


class _Zone(Protocol):
    @property
    def polygon(self) -> Sequence[_Point]: ...
    @property
    def elevation(self) -> float: ...
    @property
    def visible(self) -> bool: ...


def quad(points: Iterable[_Point]) -> Polygon:
    return Polygon([(p.x, p.y) for p in points])


def quad_problem(points: Sequence[_Point]) -> str | None:
    """Ce qui empêche ces points d'être une zone de sol (texte pour l'utilisateur), ou None."""
    if len(points) != 4:
        return f"une zone de sol a exactement 4 côtés ({len(points)} points donnés)"
    shape = quad(points)
    if not shape.is_valid:
        return "les côtés de la zone se croisent : cliquez ses 4 coins dans l'ordre"
    if shape.area < MIN_AREA_M2:
        return "zone trop petite (moins de 20 × 20 cm)"
    return None


def lift_at(zones: Iterable[_Zone], x: float, y: float) -> float:
    """La hauteur du sol en (x, y) : la plus haute zone visible qui contient le point, 0 sinon.
    Un meuble posé dessus monte d'autant."""
    point = Point(x, y)
    return max(
        (z.elevation for z in zones if z.visible and quad(z.polygon).covers(point)),
        default=0.0,
    )
