"""Symboles 2D des meubles (bibliothèque, F4) : contour + symbole reconnaissable par famille.

Un symbole est décrit une fois, ici, dans le repère unité du meuble : `u` le long de la largeur
et `v` le long de la profondeur, tous deux dans [-0,5 ; 0,5], la face avant en `v = +0,5` (même
convention que la boîte et l'accrochage au mur : le dos est en `v = -0,5`). Le plan du
navigateur (servi par `GET /api/catalog/symbols`), l'export SVG et le PDF le mettent à la
taille et à la place de la boîte ; les règles, elles, restent sur la boîte.
"""

from __future__ import annotations

import math
from typing import Literal

from openplan.schemas.common import Point2, StrictModel

UV = tuple[float, float]


class SymbolPath(StrictModel):
    """Trait du symbole, en coordonnées unité ; `closed` ferme le tracé (contour)."""

    points: list[UV]
    closed: bool = False


class FurnitureSymbol(StrictModel):
    """`outline` : contour du meuble, rectangle (la boîte) ou ellipse inscrite (table ronde,
    lampadaire) ; `paths` : les traits du symbole par-dessus le contour."""

    outline: Literal["rect", "ellipse"] = "rect"
    paths: list[SymbolPath]


def _rect(u0: float, v0: float, u1: float, v1: float) -> SymbolPath:
    return SymbolPath(points=[(u0, v0), (u1, v0), (u1, v1), (u0, v1)], closed=True)


def _line(*points: UV) -> SymbolPath:
    return SymbolPath(points=list(points))


def _ellipse(cu: float, cv: float, ru: float, rv: float, n: int = 24) -> SymbolPath:
    return SymbolPath(
        points=[
            (
                round(cu + ru * math.cos(2 * math.pi * i / n), 4),
                round(cv + rv * math.sin(2 * math.pi * i / n), 4),
            )
            for i in range(n)
        ],
        closed=True,
    )


def _cross() -> list[SymbolPath]:
    return [_line((-0.5, -0.5), (0.5, 0.5)), _line((-0.5, 0.5), (0.5, -0.5))]


def _front(v: float = 0.4) -> SymbolPath:
    return _line((-0.5, v), (0.5, v))


def _sym(*paths: SymbolPath, outline: Literal["rect", "ellipse"] = "rect") -> FurnitureSymbol:
    return FurnitureSymbol(outline=outline, paths=list(paths))


SYMBOLS: dict[str, FurnitureSymbol] = {
    # Séjour
    "sofa": _sym(
        _line((-0.5, -0.2), (0.5, -0.2)),
        _line((-0.36, -0.2), (-0.36, 0.5)),
        _line((0.36, -0.2), (0.36, 0.5)),
        _line((0.0, -0.2), (0.0, 0.5)),
    ),
    "sofa_lounge": _sym(
        _line((-0.5, -0.35), (0.5, -0.35)),
        _line((-0.42, -0.35), (-0.42, 0.5)),
        _line((0.42, -0.35), (0.42, 0.05)),
        _line((-0.1, -0.35), (-0.1, 0.5)),
        _line((-0.1, 0.05), (0.5, 0.05)),
    ),
    "sofa_corner": _sym(
        _line((0.5, -0.32), (-0.32, -0.32), (-0.32, 0.5)),
        _line((-0.32, 0.0), (0.5, 0.0)),
        _line((0.0, -0.32), (0.0, 0.0)),
    ),
    "armchair": _sym(
        _line((-0.5, -0.25), (0.5, -0.25)),
        _line((-0.32, -0.25), (-0.32, 0.5)),
        _line((0.32, -0.25), (0.32, 0.5)),
    ),
    "coffee_table": _sym(_rect(-0.42, -0.38, 0.42, 0.38)),
    "tv_unit": _sym(_rect(-0.4, -0.42, 0.4, -0.3)),
    "bookshelf": _sym(
        _line((-0.25, -0.5), (-0.25, 0.5)),
        _line((0.0, -0.5), (0.0, 0.5)),
        _line((0.25, -0.5), (0.25, 0.5)),
    ),
    "table": _sym(
        _rect(-0.3, -0.42, 0.3, 0.42),
        _rect(-0.48, -0.2, -0.36, 0.2),
        _rect(0.36, -0.2, 0.48, 0.2),
    ),
    "dining_chair": _sym(_line((-0.5, -0.3), (0.5, -0.3))),
    "round_table": _sym(_ellipse(0.0, 0.0, 0.4, 0.4), outline="ellipse"),
    "floor_lamp": _sym(
        _line((-0.35, -0.35), (0.35, 0.35)),
        _line((-0.35, 0.35), (0.35, -0.35)),
        outline="ellipse",
    ),
    # Chambre
    "bed_double": _sym(
        _rect(-0.42, -0.46, -0.04, -0.3),
        _rect(0.04, -0.46, 0.42, -0.3),
        _line((-0.5, -0.2), (0.5, -0.2)),
        _line((0.5, 0.1), (0.2, -0.2)),
    ),
    "bed_single": _sym(
        _rect(-0.36, -0.46, 0.36, -0.32),
        _line((-0.5, -0.22), (0.5, -0.22)),
        _line((0.5, 0.05), (0.2, -0.22)),
    ),
    "bed_builtin": _sym(
        # Lit encastré (H1) : planche de tête au dos, oreiller, pli de couette, tiroirs du coffre.
        _rect(-0.5, -0.5, 0.5, -0.475),
        _rect(-0.36, -0.45, 0.36, -0.32),
        _line((-0.5, -0.2), (0.5, -0.2)),
        *(_line((-0.5, v), (-0.44, v)) for v in (-0.1, 0.2)),
        *(_line((0.44, v), (0.5, v)) for v in (-0.1, 0.2)),
    ),
    "bed_bunk": _sym(
        _rect(-0.36, -0.46, 0.36, -0.32),
        _line((-0.5, -0.22), (0.5, -0.22)),
        *_cross(),
    ),
    "nightstand": _sym(_front(0.35), _ellipse(0.0, -0.05, 0.22, 0.22)),
    "wardrobe": _sym(
        _line((-0.5, 0.0), (0.5, 0.0)),
        *(_line((u - 0.06, -0.25), (u + 0.06, 0.25)) for u in (-0.34, -0.17, 0.0, 0.17, 0.34)),
    ),
    "chest": _sym(_front(0.38), _line((0.0, 0.38), (0.0, 0.5))),
    "low_storage": _sym(
        _front(0.3),
        _line((-0.17, -0.5), (-0.17, 0.3)),
        _line((0.17, -0.5), (0.17, 0.3)),
    ),
    # Bureau
    "desk": _sym(_rect(-0.22, -0.42, 0.22, -0.32)),
    "desk_corner": _sym(
        _rect(-0.22, -0.42, 0.22, -0.32), _line((0.5, 0.0), (0.0, 0.0), (0.0, 0.5))
    ),
    "chair": _sym(_line((-0.5, -0.3), (0.5, -0.3)), _ellipse(0.0, 0.1, 0.3, 0.3)),
    # Cuisine
    "kitchen_base": _sym(_front()),
    "kitchen_drawers": _sym(
        _front(),
        _line((-0.15, 0.4), (-0.15, 0.5)),
        _line((0.15, 0.4), (0.15, 0.5)),
    ),
    "kitchen_sink": _sym(
        _front(),
        _rect(-0.32, -0.28, 0.32, 0.28),
        _ellipse(0.0, 0.0, 0.06, 0.06, n=12),
        _line((0.0, -0.44), (0.0, -0.28)),
    ),
    "stove": _sym(
        *(_ellipse(u, v, 0.15, 0.15, n=16) for u in (-0.22, 0.22) for v in (-0.2, 0.2)),
    ),
    "fridge": _sym(*_cross()),
    "fridge_small": _sym(*_cross(), _front(0.4)),
    # Décor (D4)
    "rug": _sym(
        _rect(-0.42, -0.38, 0.42, 0.38),
        *(_line((-0.5, v), (-0.46, v)) for v in (-0.3, -0.1, 0.1, 0.3)),
        *(_line((0.46, v), (0.5, v)) for v in (-0.3, -0.1, 0.1, 0.3)),
    ),
    "rug_round": _sym(_ellipse(0.0, 0.0, 0.38, 0.38), outline="ellipse"),
    # Salle de bain
    "toilet": _sym(_rect(-0.4, -0.5, 0.4, -0.22), _ellipse(0.0, 0.12, 0.3, 0.36)),
    "shower": _sym(*_cross(), _ellipse(0.0, 0.0, 0.06, 0.06, n=12)),
    "bathtub": _sym(_rect(-0.45, -0.38, 0.45, 0.38), _ellipse(-0.36, 0.0, 0.04, 0.07, n=12)),
    "washbasin": _sym(_ellipse(0.0, 0.05, 0.38, 0.32), _ellipse(0.0, -0.36, 0.05, 0.05, n=12)),
    "bathroom_cabinet": _sym(_front(0.35), _line((0.0, 0.35), (0.0, 0.5))),
    "washer": _sym(_ellipse(0.0, 0.05, 0.36, 0.36), _ellipse(0.0, 0.05, 0.2, 0.2)),
    "dryer": _sym(_ellipse(0.0, 0.05, 0.36, 0.36), _line((-0.5, -0.38), (0.5, -0.38))),
}

GENERIC = FurnitureSymbol(paths=[_front(0.4)])
"""Meuble d'une famille sans symbole (projet ancien, catégorie inconnue) : contour + face avant."""


def symbol_for(category: str) -> FurnitureSymbol:
    return SYMBOLS.get(category, GENERIC)


def place_symbol(
    symbol: FurnitureSymbol, cx: float, cy: float, yaw: float, width: float, depth: float
) -> tuple[list[Point2], list[tuple[list[Point2], bool]]]:
    """Le contour et les traits du symbole en mètres, à la place et au lacet du meuble."""
    c, s = math.cos(yaw), math.sin(yaw)

    def world(uv: UV) -> Point2:
        dx, dy = uv[0] * width, uv[1] * depth
        return Point2(x=cx + c * dx - s * dy, y=cy + s * dx + c * dy)

    if symbol.outline == "ellipse":
        outline = [world(p) for p in _ellipse(0.0, 0.0, 0.5, 0.5, n=32).points]
    else:
        outline = [world(p) for p in [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]]
    paths = [([world(p) for p in path.points], path.closed) for path in symbol.paths]
    return outline, paths


class SymbolSet(StrictModel):
    """Ce que sert l'API au plan du navigateur : un symbole par famille, et celui par défaut."""

    symbols: dict[str, FurnitureSymbol]
    generic: FurnitureSymbol


def symbol_set() -> SymbolSet:
    return SymbolSet(symbols=dict(SYMBOLS), generic=GENERIC)
