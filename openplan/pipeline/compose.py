"""Gabarits de composition des zones (V1) : une zone est une composition, pas une liste.

Chaque gabarit dit où se tiennent les meubles d'une zone **les uns par rapport aux autres**,
dans un repère local accroché à un mur : `dx` le long du mur, `dy` depuis le mur vers la
pièce, `dyaw` la rotation depuis « face avant tournée vers la pièce ». Le générateur place
ensuite le groupe entier (essais le long des murs de la baie de la zone) ; seuls les meubles
hors gabarit (rangements d'appoint) se placent un par un.

- couchage : lit tête au mur, deux chevets accolés de part et d'autre, tapis au pied ;
- détente : canapé dos au mur, table basse devant sur le tapis, fauteuil en angle tourné vers
  la table, lampadaire derrière le bras du canapé ;
- lecture : fauteuil, lampadaire à côté, petite table de l'autre côté, petit tapis devant ;
- bureau : bureau au mur, chaise devant, bibliothèque à côté ;
- repas : table au mur, chaises en face et en bout de table ; plus de chaises que cela, la
  table s'écarte du mur et en prend aussi côté mur (repli : au mur, chaises en trop omises).

Cotes de passage : 0,45 m entre canapé et table basse, 0,45 m entre table et fauteuil, chevets
à 3 cm du lit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SOFA_TO_TABLE_M = 0.45
TABLE_TO_ARMCHAIR_M = 0.45
SIDE_GAP_M = 0.03
LAMP_INSET_M = 0.25
WALL_CHAIR_BACK_M = 0.15
"""Table écartée du mur : de quoi reculer la chaise côté mur."""
CHAIR_GAP_M = 0.02
"""Une chaise de table à 2 cm du plateau (les emprises ne se chevauchent pas)."""

BEDS = ("bed_double", "bed_single", "bed_bunk", "bed_builtin")
SOFAS = ("sofa", "sofa_lounge", "sofa_corner")
RUGS = ("rug", "rug_round")
DINING_TABLES = ("table", "round_table")
TABLE_CHAIRS = ("dining_chair",)


@dataclass(frozen=True)
class Slot:
    index: int
    """Indice du meuble dans la liste reçue."""
    dx: float
    dy: float
    dyaw: float = 0.0
    optional: bool = False
    """Meuble d'accompagnement (lampadaire, tapis, petite table…) : s'il ne tient pas, le
    groupe se pose sans lui et il est placé ensuite à part."""


Item = tuple[str, float, float]
"""(catégorie, largeur, profondeur) d'un meuble de la zone."""


def compose(zone: str, items: list[Item], *, compact: bool = False) -> list[Slot]:
    """Les meubles de la zone que le gabarit place, et où ; liste vide si la zone n'a pas de
    gabarit ou s'il lui manque son meuble principal (lit, canapé, fauteuil, bureau, table).
    `compact` : la variante la plus serrée (table de repas au mur), essayée en second."""
    used: set[int] = set()

    def pick(*categories: str) -> int | None:
        for i, (category, _, _) in enumerate(items):
            if i not in used and category in categories:
                used.add(i)
                return i
        return None

    def size(i: int) -> tuple[float, float]:
        return items[i][1], items[i][2]

    if zone == "repas":
        return _repas(pick, size, compact)
    builder = _BUILDERS.get(zone)
    return builder(pick, size) if builder else []


def _couchage(pick, size) -> list[Slot]:  # type: ignore[no-untyped-def]
    bed = pick(*BEDS)
    if bed is None:
        return []
    bw, bd = size(bed)
    slots = [Slot(bed, 0.0, bd / 2.0)]
    for side in (-1.0, 1.0):
        stand = pick("nightstand")
        if stand is not None:
            nw, nd = size(stand)
            slots.append(Slot(stand, side * (bw / 2.0 + nw / 2.0 + SIDE_GAP_M), nd / 2.0))
    rug = pick(*RUGS)
    if rug is not None:
        _, rd = size(rug)
        slots.append(Slot(rug, 0.0, bd - 0.3 + rd / 2.0, optional=True))
    return slots


def _detente(pick, size) -> list[Slot]:  # type: ignore[no-untyped-def]
    sofa = pick(*SOFAS)
    if sofa is None:
        return []
    sw, sd = size(sofa)
    slots = [Slot(sofa, 0.0, sd / 2.0)]
    table_y = sd + SOFA_TO_TABLE_M
    table = pick("coffee_table")
    tw = 0.0
    if table is not None:
        tw, td = size(table)
        table_y += td / 2.0
        slots.append(Slot(table, 0.0, table_y))
    rug = pick(*RUGS)
    if rug is not None:
        slots.append(Slot(rug, 0.0, table_y, optional=True))
    chair = pick("armchair")
    if chair is not None:
        _, ad = size(chair)
        # Tourné de 90° vers la table : sa face avant regarde le canapé côté table.
        slots.append(
            Slot(
                chair,
                tw / 2.0 + TABLE_TO_ARMCHAIR_M + ad / 2.0,
                table_y,
                math.pi / 2,
                optional=True,
            )
        )
    lamp = pick("floor_lamp")
    if lamp is not None:
        lw, _ = size(lamp)
        slots.append(Slot(lamp, -(sw / 2.0 + lw / 2.0 + 0.05), LAMP_INSET_M, optional=True))
    return slots


def _lecture(pick, size) -> list[Slot]:  # type: ignore[no-untyped-def]
    chair = pick("armchair")
    if chair is None:
        return []
    aw, ad = size(chair)
    slots = [Slot(chair, 0.0, ad / 2.0 + 0.15)]
    lamp = pick("floor_lamp")
    if lamp is not None:
        lw, _ = size(lamp)
        slots.append(Slot(lamp, aw / 2.0 + lw / 2.0 + 0.1, LAMP_INSET_M, optional=True))
    table = pick("nightstand", "table_2")
    if table is not None:
        nw, nd = size(table)
        slots.append(Slot(table, -(aw / 2.0 + nw / 2.0 + 0.1), nd / 2.0 + 0.15, optional=True))
    rug = pick("rug_round", "rug")
    if rug is not None:
        _, rd = size(rug)
        slots.append(Slot(rug, 0.0, ad / 2.0 + 0.15 + rd / 4.0, optional=True))
    return slots


def _repas(pick, size, compact: bool = False) -> list[Slot]:  # type: ignore[no-untyped-def]
    table = pick(*DINING_TABLES)
    if table is None:
        return []
    tw, td = size(table)
    chairs: list[int] = []
    while (chair := pick(*TABLE_CHAIRS)) is not None:
        chairs.append(chair)
    if not chairs:
        return [Slot(table, 0.0, td / 2.0)]
    cw, cd = size(chairs[0])
    # Par long côté, autant de chaises que la table en laisse (deux au plus). Table au mur :
    # chaises côté pièce puis en bout ; plus de chaises que cela, la table s'écarte du mur et
    # en prend aussi côté mur (sauf `compact`, le repli quand elle ne tient pas écartée).
    per_side = 2 if tw >= 2 * cw + 0.1 else 1
    free = not compact and len(chairs) > per_side + 2
    table_y = (cd + WALL_CHAIR_BACK_M if free else 0.0) + td / 2.0
    slots = [Slot(table, 0.0, table_y)]

    def side(row: list[int], dy: float, dyaw: float, first_required: bool) -> None:
        for k, chair in enumerate(row):
            dx = (k - (len(row) - 1) / 2.0) * (tw / per_side)
            slots.append(Slot(chair, dx, dy, dyaw, optional=not (first_required and k == 0)))

    rest = list(chairs)
    room, rest = rest[:per_side], rest[per_side:]
    side(room, table_y + td / 2.0 + CHAIR_GAP_M + cd / 2.0, math.pi, True)
    if free:
        wall, rest = rest[:per_side], rest[per_side:]
        side(wall, table_y - td / 2.0 - CHAIR_GAP_M - cd / 2.0, 0.0, False)
    for sign, chair in zip((1.0, -1.0), rest, strict=False):
        dx = sign * (tw / 2.0 + CHAIR_GAP_M + cd / 2.0)
        slots.append(Slot(chair, dx, table_y, sign * math.pi / 2, optional=True))
    return slots


def _bureau(pick, size) -> list[Slot]:  # type: ignore[no-untyped-def]
    desk = pick("desk", "desk_corner")
    if desk is None:
        return []
    dw, dd = size(desk)
    slots = [Slot(desk, 0.0, dd / 2.0)]
    chair = pick("chair")
    if chair is not None:
        _, cd = size(chair)
        slots.append(Slot(chair, 0.0, dd + cd / 2.0 + 0.02, math.pi))
    shelf = pick("bookshelf")
    if shelf is not None:
        sw, sd = size(shelf)
        slots.append(Slot(shelf, dw / 2.0 + sw / 2.0 + 0.1, sd / 2.0, optional=True))
    return slots


_BUILDERS = {
    "couchage": _couchage,
    "detente": _detente,
    "lecture": _lecture,
    "bureau": _bureau,
}
