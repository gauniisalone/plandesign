"""Lit encastré sous pente (chantier H, lot H1, note 13) : un meuble paramétrique du catalogue
(`bed_builtin`) dont la géométrie est calculée ici plutôt que lue dans un modèle 3D.

Repère du meuble (même convention que la boîte et les symboles) : `u` le long de la largeur,
`v` le long de la profondeur, face avant en `v = +0,5`, dos en `v = −0,5`. Le dos est la tête,
tournée vers la jambette (« tête sous le rampant »).

- coffre en bois de 40 cm entre les deux joues, deux ou trois tiroirs en façade (au pied) ;
- matelas de 15 cm posé dans le coffre (dessus à 0,55 m) ;
- niche (KAN-25, `niche`) : deux joues le long des grands côtés et un fond à la tête, dont le
  dessus suit la pente sous la charpente, et une étagère en haut, au-dessus du pied, là où la
  hauteur le permet ;
- règles : hauteur libre au-dessus du matelas d'au moins 0,90 m, mesurée à l'oreiller, à 35 cm
  de la tête (`pillow_point`), et 1,80 m pour se tenir debout devant le pied (`access_point`).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from scipy.spatial import HalfspaceIntersection, QhullError  # type: ignore[import-untyped]
from shapely.geometry import MultiPoint, Polygon

from openplan.geom.roof import height_under_slope
from openplan.schemas.derived import DerivedRoof

Float = NDArray[np.float64]

BUILT_IN_BED = "bed_builtin"
BASE_M = 0.40
MATTRESS_M = 0.15
MATTRESS_TOP_M = BASE_M + MATTRESS_M
HEAD_THICKNESS_M = 0.05
"""Épaisseur du fond de la niche, à la tête."""
PILLOW_FROM_HEAD_M = 0.35
MIN_HEADROOM_M = 0.90
"""Hauteur libre au-dessus du matelas, à l'oreiller : on s'y assoit sans se cogner."""
INSET_M = 0.03
"""Retrait du matelas dans le coffre."""
DRAWER_GAP_M = 0.02
DRAWER_FRONT_M = 0.015
WIDTHS = (0.90, 1.40, 1.60)
SIDE_M = 0.025
"""Épaisseur d'une joue de la niche (KAN-25)."""
NICHE_TOP_M = MATTRESS_TOP_M + 1.40
"""Haut de la niche (1,95 m) : l'étagère s'y pose, les joues ne montent pas plus haut."""
SHELF_M = 0.025
MIN_SHELF_DEPTH_M = 0.25
"""En deçà, pas d'étagère : la pente ne laisse pas la place d'y poser quoi que ce soit."""
NICHE_GAP_M = 0.02
"""Jeu entre la niche et la charpente, le caisson d'un velux ou le plafond."""
MIN_PANEL_M = 0.05
STANDING_M = 1.80
"""Hauteur libre pour se tenir debout devant le pied du lit (KAN-25)."""
ACCESS_FROM_FOOT_M = 0.30
"""Où l'on se tient pour entrer dans le lit : à 30 cm du pied, au milieu."""


@dataclass(frozen=True)
class Box:
    """Boîte du lit en coordonnées locales (mètres, centrées) : u, v, z mini et maxi."""

    part: str
    u0: float
    u1: float
    v0: float
    v1: float
    z0: float
    z1: float


def drawer_count(width: float) -> int:
    """Tiroirs en façade (KAN-25) : deux sous un lit d'une place, trois au-delà de 1,20 m."""
    return 3 if width > 1.2 else 2


def parts(width: float, depth: float) -> list[Box]:
    """Coffre et matelas entre les deux joues, façades des tiroirs au pied (KAN-25) ; les joues,
    le fond et l'étagère suivent la pente (`niche`)."""
    hw, hd = width / 2.0, depth / 2.0
    inner = hw - SIDE_M
    head = -hd + HEAD_THICKNESS_M
    front = hd - DRAWER_FRONT_M
    out = [
        Box("base", -inner, inner, head, front, 0.0, BASE_M),
        Box(
            "mattress",
            -inner + INSET_M,
            inner - INSET_M,
            head + INSET_M,
            front - INSET_M,
            BASE_M,
            MATTRESS_TOP_M,
        ),
    ]
    n = drawer_count(width)
    length = (2.0 * inner - DRAWER_GAP_M * (n + 1)) / n
    for i in range(n):
        u0 = -inner + DRAWER_GAP_M + i * (length + DRAWER_GAP_M)
        out.append(Box("drawer", u0, u0 + length, front, hd, 0.06, BASE_M - 0.04))
    return out


def _world(x: float, y: float, yaw: float, u: float, v: float) -> tuple[float, float]:
    """Repère des meubles : point local (u, v) → (x + cos·u − sin·v, y + sin·u + cos·v)."""
    c, s = math.cos(yaw), math.sin(yaw)
    return x + c * u - s * v, y + s * u + c * v


def pillow_point(x: float, y: float, yaw: float, depth: float) -> tuple[float, float]:
    """Où l'on a la tête, assis dans le lit : à 35 cm du fond, au milieu."""
    return _world(x, y, yaw, 0.0, -depth / 2.0 + HEAD_THICKNESS_M + PILLOW_FROM_HEAD_M)


def access_point(x: float, y: float, yaw: float, depth: float) -> tuple[float, float]:
    """Où l'on se tient pour entrer dans le lit : devant le pied, au milieu (KAN-25)."""
    return _world(x, y, yaw, 0.0, depth / 2.0 + ACCESS_FROM_FOOT_M)


def _clearance(roofs: list[DerivedRoof], level_height: float, px: float, py: float) -> float:
    """La plus basse des pentes à l'aplomb du point, plafonnée à la hauteur du niveau."""
    return min(
        (
            height_under_slope(
                (px, py),
                (r.eave[0].x, r.eave[0].y),
                (r.eave[1].x, r.eave[1].y),
                (r.inward.x, r.inward.y),
                r.eave_height,
                r.pitch,
                level_height,
            )
            for r in roofs
        ),
        default=level_height,
    )


def headroom(clearance: float) -> float:
    """Hauteur libre au-dessus du matelas pour une hauteur sous pente donnée."""
    return clearance - MATTRESS_TOP_M


def headroom_at(
    roofs: list[DerivedRoof], level_height: float, x: float, y: float, yaw: float, depth: float
) -> float:
    """Hauteur libre au-dessus du matelas, à l'oreiller."""
    return headroom(_clearance(roofs, level_height, *pillow_point(x, y, yaw, depth)))


def standing_at(
    roofs: list[DerivedRoof], level_height: float, x: float, y: float, yaw: float, depth: float
) -> float:
    """Hauteur libre devant le pied du lit, là où l'on se tient pour y entrer (KAN-25)."""
    return _clearance(roofs, level_height, *access_point(x, y, yaw, depth))


# ------------------------------------------------------------------ niche (KAN-25)
@dataclass(frozen=True)
class Obstacle:
    """Solide convexe que la niche ne doit pas toucher (pièce de charpente, planche du caisson
    d'un velux) : son ombre au sol et son point le plus bas."""

    shadow: Polygon
    bottom: float

    @staticmethod
    def of(points: Float) -> Obstacle | None:
        shadow = MultiPoint([(float(p[0]), float(p[1])) for p in points]).convex_hull
        if not isinstance(shadow, Polygon):
            return None
        return Obstacle(shadow, float(np.min(points[:, 2])))


def capping(planes: Float, solids: Sequence[Float]) -> list[Obstacle]:
    """Les solides qui descendent sous les plans de la niche (pannes, faîtière, caissons) : ils
    plafonnent la niche là où leur ombre la croise. Les autres (chevrons) restent au-dessus de
    l'un des plans, qui suffit à les éviter."""
    out: list[Obstacle] = []
    for points in solids:
        if len(planes) and any(
            float(np.min(points @ row[:3] + row[3])) >= NICHE_GAP_M - 1e-9 for row in planes
        ):
            continue
        obstacle = Obstacle.of(points)
        if obstacle is not None:
            out.append(obstacle)
    return out


def _cap(footprint: Polygon, top: float, obstacles: Sequence[Obstacle]) -> float:
    reach = footprint.buffer(NICHE_GAP_M)
    return min([top, *(o.bottom - NICHE_GAP_M for o in obstacles if o.shadow.intersects(reach))])


def _solid(
    corners: list[tuple[float, float]],
    z0: float,
    top: float,
    planes: Float,
    min_height: float = MIN_PANEL_M,
) -> Float | None:
    """Le prisme `corners` × [z0, top] coupé sous chaque plan (n·p + c ≤ 0) : sommets du solide
    convexe, ou None s'il reste moins de `min_height` de haut au milieu."""
    ring = np.array(corners, dtype=np.float64)
    if Polygon(ring).exterior.is_ccw is False:
        ring = ring[::-1]
    rows = []
    for (x0, y0), (x1, y1) in zip(ring, np.roll(ring, -1, axis=0), strict=True):
        nx, ny = y1 - y0, x0 - x1
        length = math.hypot(nx, ny)
        rows.append((nx / length, ny / length, 0.0, -(nx * x0 + ny * y0) / length))
    rows += [(0.0, 0.0, -1.0, z0), (0.0, 0.0, 1.0, -top)]
    halfspaces = np.vstack([np.array(rows, dtype=np.float64), planes.reshape(-1, 4)])
    cx, cy = (float(c) for c in ring.mean(axis=0))
    high = top
    for n0, n1, n2, c in planes.reshape(-1, 4):
        if n2 > 1e-9:
            high = min(high, -(n0 * cx + n1 * cy + c) / n2)
    if high - z0 < min_height:
        return None
    try:
        hs = HalfspaceIntersection(halfspaces, np.array([cx, cy, (z0 + high) / 2.0]))
    except (QhullError, ValueError):
        return None
    points: Float = np.unique(np.round(np.asarray(hs.intersections, dtype=np.float64), 9), axis=0)
    return points


def _feasible_v(
    x: float, y: float, yaw: float, u_span: float, z: float, planes: Float
) -> tuple[float, float]:
    """L'intervalle de v où le dessus d'une planche horizontale à la hauteur `z`, de −u_span à
    u_span, reste sous chaque plan (les plans sont affines : leurs bornes se calculent)."""
    lo, hi = -math.inf, math.inf
    c, s = math.cos(yaw), math.sin(yaw)
    for n0, n1, n2, k in planes.reshape(-1, 4):
        slope = -s * n0 + c * n1  # dérivée de n·p le long de v
        for u in (-u_span, u_span):
            px, py = x + c * u, y + s * u
            g0 = n0 * px + n1 * py + n2 * z + k
            if abs(slope) < 1e-12:
                if g0 > 0.0:
                    return math.inf, -math.inf
            elif slope > 0.0:
                hi = min(hi, -g0 / slope)
            else:
                lo = max(lo, -g0 / slope)
    return lo, hi


def niche(
    width: float,
    depth: float,
    x: float,
    y: float,
    yaw: float,
    z0: float,
    planes: Float,
    ceiling: float,
    obstacles: Sequence[Obstacle],
) -> list[tuple[str, Float]]:
    """Niche du lit encastré (KAN-25) : deux joues, un fond et une étagère, chacun en sommets d'un
    solide convexe. `planes` : un plan par pente (n·p + c ≤ 0 sous la toiture, abaissé sous les
    chevrons quand la charpente est apparente, `NICHE_GAP_M` compris) ; `ceiling` : hauteur du
    niveau moins le jeu ; `obstacles` : ce qui descend plus bas que ces plans (`capping`). Le
    dessus des joues et du fond suit les plans ; une pièce qui croise une tranche de joue la
    plafonne sur cette tranche seulement (la joue s'entaille sous une panne). L'étagère se pose à
    NICHE_TOP_M ou plus bas sous un obstacle, au-dessus du pied, si elle laisse 0,90 m libres
    au-dessus du matelas et au moins MIN_SHELF_DEPTH_M de profondeur."""
    hw, hd = width / 2.0, depth / 2.0
    inner = hw - SIDE_M
    top = min(z0 + NICHE_TOP_M, ceiling)
    out: list[tuple[str, Float]] = []

    def corners(u0: float, u1: float, v0: float, v1: float) -> list[tuple[float, float]]:
        return [_world(x, y, yaw, u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]

    def add(name: str, u0: float, u1: float, v0: float, v1: float, z: float, cap: float) -> None:
        pts = corners(u0, u1, v0, v1)
        solid = _solid(pts, z, _cap(Polygon(pts), cap, obstacles), planes)
        if solid is not None:
            out.append((name, solid))

    c, s = math.cos(yaw), math.sin(yaw)
    for u0, u1 in ((-hw, -inner), (inner, hw)):
        whole = Polygon(corners(u0, u1, -hd, hd)).buffer(NICHE_GAP_M)
        cuts = {-hd, hd}
        for o in obstacles:
            if o.shadow.intersects(whole):
                vs = [(px - x) * -s + (py - y) * c for px, py in o.shadow.exterior.coords]
                ends = (min(vs) - NICHE_GAP_M, max(vs) + NICHE_GAP_M)
                cuts |= {min(max(v, -hd), hd) for v in ends}
        # Une tranche par plafond : deux tranches voisines au même plafond n'en font qu'une
        # (toujours convexe), une tranche de moins d'un centimètre rejoint la précédente.
        slices: list[list[float]] = []
        for v0, v1 in itertools.pairwise(sorted(cuts)):
            cap = _cap(Polygon(corners(u0, u1, v0, v1)), top, obstacles)
            if slices and (abs(slices[-1][2] - cap) < 1e-6 or v1 - v0 < 0.01):
                slices[-1][1] = v1
                slices[-1][2] = min(slices[-1][2], cap)
            else:
                slices.append([v0, v1, cap])
        for v0, v1, cap in slices:
            add("side", u0, u1, v0, v1, z0, cap)
    add("back", -inner, inner, -hd, -hd + HEAD_THICKNESS_M, z0, top)
    span = _shelf_span(x, y, yaw, inner, hd, top, planes, obstacles)
    if span is not None and span[2] - z0 >= MATTRESS_TOP_M + MIN_HEADROOM_M:
        v0, v1, shelf_top = span
        pts = corners(-inner, inner, v0, v1)
        solid = _solid(pts, shelf_top - SHELF_M, shelf_top, planes, SHELF_M / 2.0)
        if solid is not None:
            out.append(("shelf", solid))
    return out


def _shelf_span(
    x: float,
    y: float,
    yaw: float,
    inner: float,
    hd: float,
    top: float,
    planes: Float,
    obstacles: Sequence[Obstacle],
) -> tuple[float, float, float] | None:
    """Étendue (v0, v1) et dessus de l'étagère : à `top`, là où la pente le permet, abaissée
    sous un obstacle qui croise son emprise (l'emprise s'allonge alors : on recommence)."""
    shelf_top = top
    for _ in range(5):
        lo, hi = _feasible_v(x, y, yaw, inner, shelf_top, planes)
        v0, v1 = max(lo, -hd + HEAD_THICKNESS_M), min(hi, hd)
        if v1 - v0 < MIN_SHELF_DEPTH_M:
            return None
        pts = [
            _world(x, y, yaw, u, v)
            for u, v in ((-inner, v0), (inner, v0), (inner, v1), (-inner, v1))
        ]
        capped = _cap(Polygon(pts), shelf_top, obstacles)
        if capped >= shelf_top - 1e-9:
            return v0, v1, shelf_top
        shelf_top = capped
    return None
