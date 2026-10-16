"""Cage d'escalier (S1) : géométrie pure d'un escalier paramétrique et de sa trémie.

L'escalier appartient au niveau où il **arrive** (le grenier au-dessus d'un étage) : sa
trémie perce le plancher de ce niveau et ses volées descendent de `rise` mètres. Repère local :
`d` = direction de montée (lacet `yaw` depuis +X, antihoraire), `u` = sa gauche. Tout ce qui sort
d'ici est en coordonnées du plan (mètres) ; les hauteurs sont comptées depuis le plancher
d'arrivée (marches à z < 0).

Règle de Blondel : n contremarches de hauteur h = rise / n avec h entre 16 et 20 cm, giron
g = 63 cm − 2h borné à 22–30 cm (2h + g ≈ 60–64 cm). Une marche de moins que de
contremarches : la dernière contremarche arrive sur le plancher.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openplan.schemas.elements import Stairs

RISER_RANGE_M = (0.16, 0.20)
BLONDEL_M = 0.63
GOING_RANGE_M = (0.22, 0.30)
CORE_RADIUS_M = 0.10
"""Noyau d'un hélicoïdal (fût central)."""
WALK_FRACTION = 0.65
"""Ligne de foulée d'un hélicoïdal : à 65 % du rayon (le giron s'y mesure)."""
HEADROOM_M = 1.90
"""Échappée minimale au-dessus du nez de chaque marche."""
ARRIVAL_DEPTH_M = 0.90
"""Palier d'arrivée à garder libre devant la dernière marche."""
FLOOR_THICKNESS_M = 0.20
"""Plancher d'arrivée (pour l'échappée sous un palier d'hélicoïdal)."""
WALL_TOUCH_M = 0.15
"""Un côté de trémie à moins de 15 cm d'un mur n'a pas besoin de garde-corps."""


@dataclass(frozen=True)
class Blondel:
    risers: int
    riser_m: float
    going_m: float

    @property
    def treads(self) -> int:
        return self.risers - 1

    @property
    def value_m(self) -> float:
        return 2.0 * self.riser_m + self.going_m


def blondel(rise: float) -> Blondel:
    """Nombre de contremarches et giron pour franchir `rise` mètres."""
    n = max(2, round(rise / 0.175))
    while rise / n > RISER_RANGE_M[1]:
        n += 1
    while n > 2 and rise / n < RISER_RANGE_M[0]:
        n -= 1
    h = rise / n
    g = min(GOING_RANGE_M[1], max(GOING_RANGE_M[0], BLONDEL_M - 2.0 * h))
    return Blondel(risers=n, riser_m=h, going_m=g)


@dataclass
class Tread:
    polygon: Polygon
    z_top: float
    """Dessus de la marche depuis le plancher d'arrivée (négatif)."""


@dataclass
class StairLayout:
    steps: Blondel
    treads: list[Tread]
    opening: Polygon
    """Trémie : ce qui est ouvert dans le plancher d'arrivée."""
    arrival: LineString
    """Bord de trémie par où l'on sort sur le plancher."""
    walk: list[tuple[float, float]]
    """Ligne de foulée, du bas vers le haut."""
    landing: Polygon | None = None
    """Palier de plancher qui avance dans le cercle d'un hélicoïdal."""
    extent: tuple[float, float] = (0.0, 0.0)
    """Encombrement le long de la montée et en travers (mètres), pour la pose."""
    railings: list[list[tuple[float, float]]] = field(default_factory=list)


class _Frame:
    def __init__(self, stairs: Stairs, mirror: bool) -> None:
        self.ox, self.oy = stairs.position.x, stairs.position.y
        self.dx, self.dy = math.cos(stairs.yaw), math.sin(stairs.yaw)
        self.ux, self.uy = -self.dy, self.dx
        self.m = -1.0 if mirror else 1.0
        self.shift = (0.0, 0.0)

    def p(self, a: float, b: float) -> tuple[float, float]:
        a, b = self.m * (a - self.shift[0]), b - self.shift[1]
        return (self.ox + a * self.ux + b * self.dx, self.oy + a * self.uy + b * self.dy)

    def poly(self, pts: list[tuple[float, float]]) -> Polygon:
        return Polygon([self.p(a, b) for a, b in pts])


def _rect(a0: float, a1: float, b0: float, b1: float) -> list[tuple[float, float]]:
    return [(a0, b0), (a1, b0), (a1, b1), (a0, b1)]


Local = list[tuple[list[tuple[float, float]], float]]
Pts = list[tuple[float, float]]


@dataclass
class _Plan:
    treads: Local
    arrival: Pts
    walk: Pts
    landing: Pts | None = None


def _straight(w: float, g: float, n: int, z: Callable[[int], float]) -> _Plan:
    length = n * g
    return _Plan(
        treads=[(_rect(-w / 2, w / 2, i * g, (i + 1) * g), z(i)) for i in range(n)],
        arrival=[(-w / 2, length), (w / 2, length)],
        walk=[(0.0, 0.0), (0.0, length)],
    )


def _quarter(w: float, g: float, n: int, z: Callable[[int], float]) -> _Plan:
    k1 = n // 2
    k2 = n - k1 - 1
    treads: Local = [(_rect(-w / 2, w / 2, i * g, (i + 1) * g), z(i)) for i in range(k1)]
    b0 = k1 * g
    treads.append((_rect(-w / 2, w / 2, b0, b0 + w), z(k1)))
    for j in range(k2):
        a0 = w / 2 + j * g
        treads.append((_rect(a0, a0 + g, b0, b0 + w), z(k1 + 1 + j)))
    end = w / 2 + k2 * g
    return _Plan(
        treads=treads,
        arrival=[(end, b0), (end, b0 + w)],
        walk=[(0.0, 0.0), (0.0, b0 + w / 2), (end, b0 + w / 2)],
    )


def _half(w: float, g: float, n: int, z: Callable[[int], float]) -> _Plan:
    k1 = math.ceil((n - 1) / 2)
    k2 = n - k1 - 1
    treads: Local = [(_rect(-w, 0.0, i * g, (i + 1) * g), z(i)) for i in range(k1)]
    b0 = k1 * g
    treads.append((_rect(-w, w, b0, b0 + w), z(k1)))
    for j in range(k2):
        top = b0 - j * g
        treads.append((_rect(0.0, w, top - g, top), z(k1 + 1 + j)))
    end = b0 - k2 * g
    return _Plan(
        treads=treads,
        arrival=[(0.0, end), (w, end)],
        walk=[(-w / 2, 0.0), (-w / 2, b0 + w / 2), (w / 2, b0 + w / 2), (w / 2, end)],
    )


def _spiral(w: float, g: float, n: int, z: Callable[[int], float]) -> _Plan:
    radius = CORE_RADIUS_M + w
    step = g / (WALK_FRACTION * radius)
    start = -math.pi / 2.0
    treads: Local = [
        (_sector(CORE_RADIUS_M, radius, start + i * step, step), z(i)) for i in range(n)
    ]
    end_angle = start + n * step
    r = WALK_FRACTION * radius
    return _Plan(
        treads=treads,
        arrival=[
            (CORE_RADIUS_M * math.cos(end_angle), CORE_RADIUS_M * math.sin(end_angle)),
            (radius * math.cos(end_angle), radius * math.sin(end_angle)),
        ],
        walk=[
            (r * math.cos(start + t * n * step / 12), r * math.sin(start + t * n * step / 12))
            for t in range(13)
        ],
        landing=_sector(0.0, radius + 0.01, end_angle, 1.5 * step),
    )


_PLANS = {"straight": _straight, "quarter_turn": _quarter, "half_turn": _half, "spiral": _spiral}


def stair_layout(stairs: Stairs) -> StairLayout:
    """Marches, trémie, arrivée et ligne de foulée de l'escalier, dans le plan."""
    steps = blondel(stairs.rise)
    frame = _Frame(stairs, mirror=stairs.turn == "right")
    w = stairs.width

    def z(i: int) -> float:
        return -stairs.rise + (i + 1) * steps.riser_m

    plan = _PLANS[stairs.kind](w, steps.going_m, steps.treads, z)
    if plan.landing is None:
        pts = [p for poly, _ in plan.treads for p in poly]
        a_min, a_max = min(p[0] for p in pts), max(p[0] for p in pts)
        b_min, b_max = min(p[1] for p in pts), max(p[1] for p in pts)
        frame.shift = ((a_min + a_max) / 2.0, (b_min + b_max) / 2.0)
        extent = (b_max - b_min, a_max - a_min)
    else:
        extent = (2.0 * (CORE_RADIUS_M + w), 2.0 * (CORE_RADIUS_M + w))
    treads = [Tread(frame.poly(poly), zt) for poly, zt in plan.treads]
    footprint: BaseGeometry = unary_union([t.polygon for t in treads]).buffer(0.0)
    landing = None
    if plan.landing is not None:
        circle = Point(frame.p(0.0, 0.0)).buffer(CORE_RADIUS_M + w, quad_segs=16)
        landing = frame.poly(plan.landing)
        footprint = circle.difference(landing)
    opening = footprint if isinstance(footprint, Polygon) else footprint.convex_hull
    # La réunion des marches laisse un sommet par marche sur les bords droits : on les retire.
    opening = opening.simplify(1e-4, preserve_topology=True)
    return StairLayout(
        steps=steps,
        treads=treads,
        opening=opening,
        arrival=LineString([frame.p(*plan.arrival[0]), frame.p(*plan.arrival[1])]),
        walk=[frame.p(a, b) for a, b in plan.walk],
        landing=landing,
        extent=extent,
    )


def _sector(r0: float, r1: float, a0: float, da: float) -> list[tuple[float, float]]:
    arc = [a0 + da * t / 4 for t in range(5)]
    outer = [(r1 * math.cos(a), r1 * math.sin(a)) for a in arc]
    if r0 <= 0.0:
        return [(0.0, 0.0), *outer]
    inner = [(r0 * math.cos(a), r0 * math.sin(a)) for a in reversed(arc)]
    return outer + inner


def railing_lines(
    layout: StairLayout, walls: BaseGeometry | None
) -> list[list[tuple[float, float]]]:
    """Côtés ouverts de la trémie : son contour, moins le bord d'arrivée et ce qui longe un
    mur."""
    edge: BaseGeometry = layout.opening.exterior
    edge = edge.difference(layout.arrival.buffer(0.03))
    if walls is not None and not walls.is_empty:
        edge = edge.difference(walls.buffer(WALL_TOUCH_M))
    lines = (
        list(edge.geoms)
        if isinstance(edge, MultiLineString)
        else [edge]
        if isinstance(edge, LineString)
        else []
    )
    return [[(float(x), float(y)) for x, y in line.coords] for line in lines if line.length > 0.1]


def arrival_zone(layout: StairLayout) -> Polygon:
    """Palier d'arrivée à garder libre : bande de 90 cm au-delà du bord d'arrivée, hors
    trémie."""
    (ax, ay), (bx, by) = layout.arrival.coords
    mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
    ex, ey = bx - ax, by - ay
    n = math.hypot(ex, ey) or 1.0
    nx, ny = -ey / n, ex / n
    probe = Point(mx + nx * 0.05, my + ny * 0.05)
    if layout.opening.contains(probe):
        nx, ny = -nx, -ny
    d = ARRIVAL_DEPTH_M
    zone = Polygon([(ax, ay), (bx, by), (bx + nx * d, by + ny * d), (ax + nx * d, ay + ny * d)])
    return zone.difference(layout.opening)


def headroom(layout: StairLayout, ceiling: Callable[[tuple[float, float]], float]) -> float:
    """Échappée la plus faible au-dessus des marches : hauteur libre du niveau d'arrivée à
    l'aplomb de la marche plus la profondeur de la marche ; sous le palier d'un hélicoïdal,
    la sous-face du plancher."""
    worst = math.inf
    for t in layout.treads:
        c = t.polygon.centroid
        if layout.landing is not None and t.polygon.intersects(layout.landing):
            free = -FLOOR_THICKNESS_M - t.z_top
        else:
            free = ceiling((float(c.x), float(c.y))) - t.z_top
        worst = min(worst, free)
    return worst


def cut_line(layout: StairLayout) -> list[tuple[float, float]]:
    """Ligne de coupe en zigzag, en travers de la ligne de foulée au tiers de la montée."""
    walk = LineString(layout.walk)
    at = walk.interpolate(walk.length / 3.0)
    before = walk.interpolate(max(walk.length / 3.0 - 0.05, 0.0))
    dx, dy = at.x - before.x, at.y - before.y
    n = math.hypot(dx, dy) or 1.0
    ux, uy = -dy / n, dx / n
    half = 0.6
    tx, ty = dx / n * 0.08, dy / n * 0.08
    return [
        (at.x - ux * half - tx, at.y - uy * half - ty),
        (at.x - ux * 0.08 + tx, at.y - uy * 0.08 + ty),
        (at.x + ux * 0.08 - tx, at.y + uy * 0.08 - ty),
        (at.x + ux * half + tx, at.y + uy * half + ty),
    ]


def arrow_head(layout: StairLayout) -> list[tuple[float, float]]:
    """Pointe de flèche au bout de la ligne de foulée (sens de montée)."""
    (px, py), (qx, qy) = layout.walk[-2], layout.walk[-1]
    dx, dy = qx - px, qy - py
    n = math.hypot(dx, dy) or 1.0
    dx, dy = dx / n, dy / n
    s = 0.15
    return [
        (qx - dx * s - dy * s * 0.6, qy - dy * s + dx * s * 0.6),
        (qx, qy),
        (qx - dx * s + dy * s * 0.6, qy - dy * s - dx * s * 0.6),
    ]
