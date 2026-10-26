"""Géométrie des pentes de toit (`RoofPlane`) : course, hauteur utile, sens de montée.

Une pente est un plan qui part d'une ligne d'égout horizontale (à `eave_height`) et monte
vers l'intérieur de la pièce avec l'inclinaison `pitch`, jusqu'à la hauteur du niveau (le
faîtage). Tout est en mètres et radians, repère du niveau (X droite, Y haut, Z vers le haut).
"""

from __future__ import annotations

import math
from uuid import UUID

from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.common import Point2
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import RoofPlane, Room
from openplan.schemas.scene import Level

USABLE_HEIGHT_M = 1.80
"""Hauteur sous pente en dessous de laquelle on ne tient plus debout (ligne du plan)."""
MIN_PITCH = math.radians(1.0)
MAX_PITCH = math.radians(89.0)


def horizontal_run(eave_height: float, pitch: float, top_height: float) -> float:
    """Distance horizontale, depuis l'égout, pour atteindre `top_height` ; 0 si déjà atteint."""
    if top_height <= eave_height or not MIN_PITCH <= pitch <= MAX_PITCH:
        return 0.0
    return (top_height - eave_height) / math.tan(pitch)


def usable_offset(eave_height: float, pitch: float, top_height: float) -> float | None:
    """Distance horizontale depuis l'égout où la hauteur sous pente atteint USABLE_HEIGHT_M.

    0 si l'égout est déjà assez haut ; None si le faîtage lui-même est trop bas.
    """
    if eave_height >= USABLE_HEIGHT_M:
        return 0.0
    if top_height <= USABLE_HEIGHT_M:
        return None
    return horizontal_run(eave_height, pitch, USABLE_HEIGHT_M)


def inward_normal(
    eave_start: tuple[float, float],
    eave_end: tuple[float, float],
    toward: tuple[float, float] | None,
) -> tuple[float, float]:
    """Normale unitaire à la ligne d'égout, orientée vers `toward` (centre de la pièce) ;
    sans repère, la normale gauche de start→end."""
    dx, dy = eave_end[0] - eave_start[0], eave_end[1] - eave_start[1]
    length = math.hypot(dx, dy)
    if length < 1e-12:
        return (0.0, 1.0)
    nx, ny = -dy / length, dx / length
    if toward is not None:
        mx, my = (eave_start[0] + eave_end[0]) / 2.0, (eave_start[1] + eave_end[1]) / 2.0
        if (toward[0] - mx) * nx + (toward[1] - my) * ny < 0:
            nx, ny = -nx, -ny
    return (nx, ny)


def height_under_slope(
    point: tuple[float, float],
    eave_start: tuple[float, float],
    eave_end: tuple[float, float],
    inward: tuple[float, float],
    eave_height: float,
    pitch: float,
    top_height: float,
) -> float:
    """Hauteur libre sous une pente à l'aplomb de `point` : monte depuis l'égout avec `pitch`
    le long de `inward`, plafonnée à `top_height` ; du côté extérieur de l'égout, l'égout."""
    mx, my = (eave_start[0] + eave_end[0]) / 2.0, (eave_start[1] + eave_end[1]) / 2.0
    distance = (point[0] - mx) * inward[0] + (point[1] - my) * inward[1]
    if distance <= 0.0 or not MIN_PITCH <= pitch <= MAX_PITCH:
        return min(eave_height, top_height)
    return min(top_height, eave_height + distance * math.tan(pitch))


def derived_roofs(level: Level, graph: WallGraph) -> dict[UUID, DerivedRoof]:
    """Pentes visibles du niveau vues en plan : sens de montée vers le centre de leur pièce
    (sinon vers le centre des sommets), course, hauteur utile."""
    out: dict[UUID, DerivedRoof] = {}
    centre: tuple[float, float] | None = None
    if level.vertices:
        xs = [v.position.x for v in level.vertices.values()]
        ys = [v.position.y for v in level.vertices.values()]
        centre = ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    for rid, roof in level.elements.items():
        if not isinstance(roof, RoofPlane) or not roof.visible:
            continue
        toward = centre
        for room_id in roof.rooms:
            if isinstance(level.elements.get(room_id), Room):
                try:
                    c = graph.room_polygon(room_id).centroid
                except GeometryError:
                    continue
                toward = (float(c.x), float(c.y))
                break
        a = (roof.eave_start.x, roof.eave_start.y)
        b = (roof.eave_end.x, roof.eave_end.y)
        nx, ny = inward_normal(a, b, toward)
        out[rid] = DerivedRoof(
            eave=[roof.eave_start, roof.eave_end],
            inward=Point2(x=nx, y=ny),
            run_m=horizontal_run(roof.eave_height, roof.pitch, level.height),
            usable_offset_m=usable_offset(roof.eave_height, roof.pitch, level.height),
            pitch_deg=math.degrees(roof.pitch),
            eave_height=roof.eave_height,
            pitch=roof.pitch,
        )
    return out


RIDGE_OPPOSITE_COS = -0.95
"""Deux pentes dont les sens de montée sont opposés (à ~18° près) se rejoignent au faîtage."""


def ridge_pair(roofs: list[DerivedRoof]) -> tuple[DerivedRoof, DerivedRoof] | None:
    """Les deux premières pentes opposées (elles se rejoignent au faîtage) ; None sinon
    (appentis, toit plat)."""
    for i, a in enumerate(roofs):
        for b in roofs[i + 1 :]:
            if a.inward.x * b.inward.x + a.inward.y * b.inward.y > RIDGE_OPPOSITE_COS:
                continue
            if math.hypot(a.eave[1].x - a.eave[0].x, a.eave[1].y - a.eave[0].y) < 1e-9:
                continue
            return a, b
    return None


def ridge_line(roofs: list[DerivedRoof]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Un point du faîtage et sa direction (unitaire), en plan, s'il y a deux pentes
    opposées ; None sinon (appentis, toit plat)."""
    pair = ridge_pair(roofs)
    if pair is None:
        return None
    a, b = pair

    def top(r: DerivedRoof) -> tuple[float, float]:
        mx = (r.eave[0].x + r.eave[1].x) / 2.0
        my = (r.eave[0].y + r.eave[1].y) / 2.0
        return mx + r.inward.x * r.run_m, my + r.inward.y * r.run_m

    (ax, ay), (bx, by) = top(a), top(b)
    dx, dy = a.eave[1].x - a.eave[0].x, a.eave[1].y - a.eave[0].y
    n = math.hypot(dx, dy)
    return ((ax + bx) / 2.0, (ay + by) / 2.0), (dx / n, dy / n)


def pitch_from_rise(rise: float, run: float) -> float:
    """Inclinaison d'une pente qui monte de `rise` sur une course horizontale `run`."""
    if run <= 0:
        return MAX_PITCH
    return math.atan2(rise, run)


def roof_window_footprint(
    a: tuple[float, float],
    b: tuple[float, float],
    inward: tuple[float, float],
    pitch: float,
    offset: float,
    distance: float,
    width: float,
    length: float,
) -> list[tuple[float, float]]:
    """Emprise au sol d'un velux (D1) : rectangle le long de l'égout `a → b`, entre `distance`
    et `distance + length` le long de la pente, ramenées au plan par cos(pente)."""
    ux, uy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(ux, uy)
    if n < 1e-9:
        return []
    ux, uy = ux / n, uy / n
    c = math.cos(pitch)
    out = []
    for along, up in (
        (offset, distance),
        (offset + width, distance),
        (offset + width, distance + length),
        (offset, distance + length),
    ):
        out.append((a[0] + ux * along + inward[0] * up * c, a[1] + uy * along + inward[1] * up * c))
    return out


def slope_length(eave_height: float, pitch: float, top_height: float) -> float:
    """Longueur de la pente, de l'égout au faîtage (ou au plafond), mesurée le long de la pente."""
    run = horizontal_run(eave_height, pitch, top_height)
    return run / math.cos(pitch) if run > 0.0 else 0.0
