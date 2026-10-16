"""Symboles normalisés des ouvertures en plan (E5) : battants, coulissants, pliants.

Une seule définition pour le SVG, le PDF et le DXF ; le plan du navigateur en a la jumelle en
TypeScript (`apps/web/src/editor2d/openingSymbol.ts`, mêmes règles, mêmes constantes). Les
coordonnées sont celles du niveau (mètres). Conventions :

- battant (porte, double porte, porte-fenêtre) : trait du vantail ouvert à 90° et arc du bord
  libre, autour de chaque gond ; `swing.hinge` désigne le vantail principal d'une paire, de
  largeur `split` × largeur ; la porte-fenêtre ajoute le vitrage (deux traits dans le mur) ;
- pliante : zigzag des panneaux repliés côté gonds, tiret jusqu'au bord libre ;
- coulissante en applique : vantail le long de la face du mur (côté `swing.direction`), rail
  sur la baie et sur le tronçon où le vantail s'efface (côté `swing.hinge`), flèche ;
- à galandage : vantail dans la baie, poche en tireté dans l'épaisseur du mur, flèche ;
- baie coulissante : deux vantaux vitrés décalés dans l'épaisseur, qui se recouvrent, flèche.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from openplan.schemas.elements import Opening, OpeningKind

XY = tuple[float, float]

FOLD_PANELS = 4
FOLD_ANGLE = math.radians(60.0)
SLIDE_GAP_M = 0.03
"""Écart entre la face du mur et un vantail coulissant en applique."""
RAIL_GAP_M = 0.06
SLIDE_DEPTH_M = 0.12
"""Profondeur, depuis la face du mur, de la bande que le vantail en applique parcourt."""
WINDOW_OVERLAP = 0.55
"""Part de la largeur couverte par chaque vantail d'une baie coulissante."""

SWING_KINDS = frozenset(
    {
        OpeningKind.DOOR,
        OpeningKind.DOUBLE_DOOR,
        OpeningKind.FOLDING_DOOR,
        OpeningKind.FRENCH_WINDOW,
    }
)
STACK_KINDS = frozenset({OpeningKind.SLIDING_DOOR, OpeningKind.POCKET_DOOR})
"""Le vantail ouvert s'efface le long du mur (applique) ou dedans (galandage)."""


@dataclass(frozen=True)
class SymbolLine:
    points: tuple[XY, ...]
    style: str
    """`leaf` (vantail), `glass` (vitrage), `rail`, `arrow`, `hidden` (tireté)."""


@dataclass(frozen=True)
class SymbolArc:
    center: XY
    radius: float
    start_deg: float
    extent_deg: float
    """Signé, sens direct du repère du niveau (Y vers le haut)."""


@dataclass
class OpeningSymbol:
    lines: list[SymbolLine] = field(default_factory=list)
    arcs: list[SymbolArc] = field(default_factory=list)


@dataclass(frozen=True)
class Leaf:
    hinge: XY
    toward: XY
    """Direction unitaire du gond vers le bord libre, le long du mur."""
    width: float


def _add(p: XY, v: XY, k: float = 1.0) -> XY:
    return (p[0] + v[0] * k, p[1] + v[1] * k)


def _deg(v: XY) -> float:
    return math.degrees(math.atan2(v[1], v[0]))


def swing_side(opening: Opening) -> float:
    """+1 : vers la normale gauche du mur (« inward »), −1 sinon."""
    return -1.0 if opening.swing is not None and opening.swing.direction == "outward" else 1.0


def is_pair(opening: Opening) -> bool:
    return opening.kind is OpeningKind.DOUBLE_DOOR or (
        opening.kind is OpeningKind.FRENCH_WINDOW and opening.leaves == 2
    )


def leaves_of(opening: Opening, p0: XY, p1: XY, u: XY) -> list[Leaf]:
    """Vantaux battants : gond, direction du bord libre, largeur. Vide hors battants."""
    if opening.kind not in SWING_KINDS:
        return []
    left = opening.swing is None or opening.swing.hinge == "left"
    w = opening.width
    back = (-u[0], -u[1])
    if not is_pair(opening):
        return [Leaf(p0, u, w)] if left else [Leaf(p1, back, w)]
    main = w * opening.split
    if left:
        return [Leaf(p0, u, main), Leaf(p1, back, w - main)]
    return [Leaf(p1, back, main), Leaf(p0, u, w - main)]


def folding_depth(width: float) -> float:
    """Saillie des panneaux repliés d'une porte pliante."""
    return width / FOLD_PANELS * math.sin(FOLD_ANGLE)


def stack_span(opening: Opening) -> tuple[float, float] | None:
    """Tronçon du mur (abscisses depuis le départ du mur) où s'efface le vantail ouvert d'une
    coulissante en applique ou à galandage : du côté `swing.hinge`."""
    if opening.kind not in STACK_KINDS:
        return None
    left = opening.swing is None or opening.swing.hinge == "left"
    if left:
        return (opening.offset - opening.width, opening.offset)
    return (opening.offset + opening.width, opening.offset + 2.0 * opening.width)


def _arrow(start: XY, direction: XY, length: float) -> list[SymbolLine]:
    tip = _add(start, direction, length)
    head = min(0.12, length / 3.0)
    lines = [SymbolLine((start, tip), "arrow")]
    for sign in (1.0, -1.0):
        a = math.radians(155.0) * sign
        d = (
            direction[0] * math.cos(a) - direction[1] * math.sin(a),
            direction[0] * math.sin(a) + direction[1] * math.cos(a),
        )
        lines.append(SymbolLine((tip, _add(tip, d, head)), "arrow"))
    return lines


def opening_symbol(opening: Opening, origin: XY, u: XY, half: float) -> OpeningSymbol:
    """Le symbole de l'ouverture : `origin` est le départ de l'axe du mur hôte, `u` sa
    direction unitaire, `half` sa demi-épaisseur."""
    n = (-u[1], u[0])
    p0 = _add(origin, u, opening.offset)
    p1 = _add(p0, u, opening.width)
    side = swing_side(opening)
    out_dir = (n[0] * side, n[1] * side)
    sym = OpeningSymbol()
    kind = opening.kind
    w = opening.width

    def at(s: float, k: float = 0.0) -> XY:
        return _add(_add(origin, u, s), n, k)

    if kind is OpeningKind.FOLDING_DOOR:
        leaf = leaves_of(opening, p0, p1, u)[0]
        panel = w / FOLD_PANELS
        along = panel * math.cos(FOLD_ANGLE)
        out = panel * math.sin(FOLD_ANGLE)
        pts = [leaf.hinge]
        for i in range(1, FOLD_PANELS + 1):
            pts.append(_add(_add(leaf.hinge, leaf.toward, i * along), out_dir, out if i % 2 else 0))
        sym.lines.append(SymbolLine(tuple(pts), "leaf"))
        sym.lines.append(SymbolLine((pts[-1], _add(leaf.hinge, leaf.toward, w)), "hidden"))
    elif kind in SWING_KINDS:
        for leaf in leaves_of(opening, p0, p1, u):
            sym.lines.append(
                SymbolLine((leaf.hinge, _add(leaf.hinge, out_dir, leaf.width)), "leaf")
            )
            start = _deg(leaf.toward)
            extent = (_deg(out_dir) - start + 540.0) % 360.0 - 180.0
            sym.arcs.append(SymbolArc(leaf.hinge, leaf.width, start, extent))
        if kind is OpeningKind.FRENCH_WINDOW:
            for gap in (half / 3.0, -half / 3.0):
                sym.lines.append(
                    SymbolLine((at(opening.offset, gap), at(opening.offset + w, gap)), "glass")
                )
    elif kind in STACK_KINDS:
        span = stack_span(opening)
        assert span is not None
        toward = (-u[0], -u[1]) if span[0] < opening.offset else u
        mid = opening.offset + w / 2.0
        if kind is OpeningKind.SLIDING_DOOR:
            f = side * (half + SLIDE_GAP_M)
            r = side * (half + RAIL_GAP_M)
            sym.lines.append(SymbolLine((at(opening.offset, f), at(opening.offset + w, f)), "leaf"))
            lo, hi = min(span[0], opening.offset), max(span[1], opening.offset + w)
            sym.lines.append(SymbolLine((at(lo, r), at(hi, r)), "rail"))
            sym.lines.extend(_arrow(at(mid, f + side * 0.12), toward, 0.4 * w))
        else:
            sym.lines.append(SymbolLine((p0, p1), "leaf"))
            k = half * 0.6
            sym.lines.append(
                SymbolLine(
                    (
                        at(span[0], k),
                        at(span[1], k),
                        at(span[1], -k),
                        at(span[0], -k),
                        at(span[0], k),
                    ),
                    "hidden",
                )
            )
            sym.lines.extend(_arrow(at(mid, half + 0.12), toward, 0.4 * w))
    elif kind is OpeningKind.SLIDING_WINDOW:
        k = half / 3.0
        cover = WINDOW_OVERLAP * w
        sym.lines.append(
            SymbolLine((at(opening.offset, k), at(opening.offset + cover, k)), "glass")
        )
        sym.lines.append(
            SymbolLine((at(opening.offset + w - cover, -k), at(opening.offset + w, -k)), "glass")
        )
        sym.lines.extend(_arrow(at(opening.offset + w / 2.0, half + 0.12), u, 0.3 * w))
    return sym
