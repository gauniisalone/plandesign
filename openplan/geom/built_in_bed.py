"""Lit encastré sous pente (chantier H, lot H1, note 13) : un meuble paramétrique du catalogue
(`bed_builtin`) dont la géométrie est calculée ici plutôt que lue dans un modèle 3D.

Repère du meuble (même convention que la boîte et les symboles) : `u` le long de la largeur,
`v` le long de la profondeur, face avant en `v = +0,5`, dos en `v = −0,5`. Le dos est la tête,
tournée vers la jambette (« tête sous le rampant »).

- coffre en bois de 40 cm, deux ou trois tiroirs sur chaque long côté ;
- matelas de 15 cm posé dans le coffre (dessus à 0,55 m) ;
- planche de tête jusqu'à 0,80 m, qui sert de tablette ;
- règle : hauteur libre au-dessus du matelas d'au moins 0,90 m, mesurée à l'oreiller, à 35 cm
  de la tête (`pillow_point`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from openplan.geom.roof import height_under_slope
from openplan.schemas.derived import DerivedRoof

BUILT_IN_BED = "bed_builtin"
BASE_M = 0.40
MATTRESS_M = 0.15
MATTRESS_TOP_M = BASE_M + MATTRESS_M
HEAD_TOP_M = 0.80
HEAD_THICKNESS_M = 0.05
PILLOW_FROM_HEAD_M = 0.35
MIN_HEADROOM_M = 0.90
"""Hauteur libre au-dessus du matelas, à l'oreiller : on s'y assoit sans se cogner."""
INSET_M = 0.03
"""Retrait du matelas dans le coffre."""
DRAWER_GAP_M = 0.02
WIDTHS = (0.90, 1.40, 1.60)


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


def drawer_count(depth: float) -> int:
    """Deux tiroirs jusqu'à 1,90 m de coffre, trois au-delà."""
    return 3 if depth - HEAD_THICKNESS_M > 1.9 else 2


def parts(width: float, depth: float) -> list[Box]:
    """Coffre, façades de tiroirs (deux longs côtés), matelas, planche de tête."""
    hw, hd = width / 2.0, depth / 2.0
    head = -hd + HEAD_THICKNESS_M
    out = [
        Box("head", -hw, hw, -hd, head, 0.0, HEAD_TOP_M),
        Box("base", -hw, hw, head, hd, 0.0, BASE_M),
        Box(
            "mattress",
            -hw + INSET_M,
            hw - INSET_M,
            head + INSET_M,
            hd - INSET_M,
            BASE_M,
            MATTRESS_TOP_M,
        ),
    ]
    n = drawer_count(depth)
    length = (hd - head - DRAWER_GAP_M * (n + 1)) / n
    for side in (-1.0, 1.0):
        for i in range(n):
            v0 = head + DRAWER_GAP_M + i * (length + DRAWER_GAP_M)
            u_in, u_out = side * hw, side * (hw + 0.015)
            out.append(
                Box(
                    "drawer",
                    min(u_in, u_out),
                    max(u_in, u_out),
                    v0,
                    v0 + length,
                    0.06,
                    BASE_M - 0.04,
                )
            )
    return out


def pillow_point(x: float, y: float, yaw: float, depth: float) -> tuple[float, float]:
    """Où l'on a la tête, assis dans le lit : à 35 cm de la planche de tête, au milieu."""
    v = -depth / 2.0 + HEAD_THICKNESS_M + PILLOW_FROM_HEAD_M
    # repère des meubles : point local (u, v) → (x − sin·v, y + cos·v) pour u = 0
    return x - math.sin(yaw) * v, y + math.cos(yaw) * v


def headroom(clearance: float) -> float:
    """Hauteur libre au-dessus du matelas pour une hauteur sous pente donnée."""
    return clearance - MATTRESS_TOP_M


def headroom_at(
    roofs: list[DerivedRoof], level_height: float, x: float, y: float, yaw: float, depth: float
) -> float:
    """Hauteur libre au-dessus du matelas, à l'oreiller : la plus basse des pentes à
    l'aplomb, plafonnée à la hauteur du niveau."""
    px, py = pillow_point(x, y, yaw, depth)
    clearance = min(
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
    return headroom(clearance)
