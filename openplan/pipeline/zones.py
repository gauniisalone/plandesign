"""Brief par zones (V1, note 10, point 2) : de l'intention de la pièce à un brief dense.

Une pièce se meuble par **zones** (couchage, lecture, bureau, rangement, détente), chacune avec
son kit de meubles du catalogue. Le nombre de zones vient de l'intention, sinon de la surface ;
la **densité cible** est de 0,3 à 0,5 meuble par m² (un grenier de 70 m² : 3 à 4 zones, 15 à 25
meubles). Les zones sont placées par **baies** le long du faîtage (sinon du grand axe de la
pièce) : chaque meuble d'une zone cherche sa place dans sa baie. Sous pente, lits, chevets,
commodes et rangements bas vont contre les jambettes (`low_wall`), pas seulement au pignon.

Rien ici ne place : le brief dit *quoi* et *près de quoi*, le générateur décide *où*.
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

from openplan.schemas.intent import DesignIntent, Zone
from openplan.schemas.layout import BriefItem, LayoutBrief, Place, Priority

TARGET_DENSITY = 0.35
"""Meubles par m² de sol visés (milieu de la fourchette 0,3–0,5 de la note 10)."""
MIN_ITEMS = 5
MAX_ITEMS = 35
USABLE_HEIGHT_M = 1.40
"""La densité se compte sur la surface où l'on tient debout ou presque (plus de 1,40 m)."""
AREA_PER_ZONE_M2 = 18.0
MAX_ZONES = 4
TIGHT_M2_PER_ZONE = 10.0

Kit = list[tuple[str, int, Priority, list[Place]]]

LOW: Place = "low_wall"
KITS: dict[str, Kit] = {
    "couchage": [
        ("bed_double", 1, "required", [LOW]),
        ("nightstand", 2, "required", [LOW]),
        ("rug", 1, "nice_to_have", []),
    ],
    "couchage_pente": [
        ("bed_builtin", 1, "required", [LOW]),
        ("rug", 1, "nice_to_have", []),
    ],
    "lecture": [
        ("armchair", 1, "required", ["window"]),
        ("floor_lamp", 1, "required", []),
        ("nightstand", 1, "nice_to_have", []),
        ("rug_round", 1, "nice_to_have", []),
    ],
    "bureau": [
        ("desk", 1, "required", ["window"]),
        ("chair", 1, "required", []),
        ("bookshelf", 1, "nice_to_have", ["wall"]),
    ],
    "rangement": [
        ("low_storage", 2, "required", [LOW]),
        ("chest", 1, "nice_to_have", [LOW]),
    ],
    "cuisine": [
        ("kitchen_sink", 1, "required", ["wall"]),
        ("stove", 1, "required", ["wall"]),
        ("kitchen_base", 1, "required", ["wall"]),
        ("fridge", 1, "required", ["wall"]),
    ],
    "repas": [
        ("table", 1, "required", []),
        ("dining_chair", 4, "required", []),
    ],
    "detente": [
        ("sofa", 1, "required", []),
        ("coffee_table", 1, "required", []),
        ("armchair", 1, "nice_to_have", []),
        ("rug", 1, "nice_to_have", []),
        ("floor_lamp", 1, "nice_to_have", []),
    ],
}
"""Kit de chaque zone : catégorie, nombre, priorité, repères voulus (`low_wall` seulement
sous pente)."""
FILLERS_SLOPED: Kit = [("low_storage", 1, "nice_to_have", [LOW])]
FILLERS_FLAT: Kit = [
    ("chest", 1, "nice_to_have", ["wall"]),
    ("bookshelf", 1, "nice_to_have", ["wall"]),
]
"""De quoi compléter tant que la densité cible n'est pas atteinte : des rangements, jamais un
deuxième meuble principal (un seul canapé, lit, fauteuil… par zone, sauf demande)."""
ORDER: dict[str, list[Zone]] = {
    "bedroom": ["couchage", "rangement", "lecture", "bureau"],
    "attic": ["couchage", "lecture", "rangement", "detente"],
    "living_room": ["detente", "lecture", "rangement", "bureau"],
    "office": ["bureau", "lecture", "rangement", "detente"],
    "other": ["detente", "lecture", "rangement", "bureau"],
}
"""Zones par défaut, dans l'ordre, selon le type de pièce ; cuisine, salle de bain et couloir
ne se meublent pas par zones. Les zones « cuisine » et « repas » ne viennent que d'une
intention (séjour avec cuisine ouverte d'un plan généré, L3)."""


def zones_for(intent: DesignIntent, room_type: str | None, area: float, sloped: bool) -> list[Zone]:
    """Les zones voulues (intention), sinon autant que la surface en permet (une par 18 m²,
    quatre au plus) dans l'ordre du type de pièce."""
    if intent.zones:
        return list(intent.zones)
    kind = room_type or ("attic" if sloped else "other")
    order = ORDER.get(kind)
    if order is None:
        return []
    count = max(1, min(MAX_ZONES, round(area / AREA_PER_ZONE_M2)))
    return order[:count]


def target_items(area: float) -> int:
    return max(MIN_ITEMS, min(MAX_ITEMS, round(TARGET_DENSITY * area)))


def zone_brief(
    intent: DesignIntent,
    room_type: str | None,
    area: float,
    *,
    sloped: bool,
    usable_area: float | None = None,
) -> LayoutBrief:
    """Le brief dense d'une pièce d'après son intention (V1). Les meubles souhaités de
    l'intention passent en premier ; les catégories à éviter sont retirées des kits. Le
    nombre de zones suit la surface au sol, la densité la surface à plus de 1,40 m."""
    usable = area if usable_area is None else usable_area
    zones = zones_for(intent, room_type, area, sloped)
    avoid = set(intent.avoid)
    items: list[BriefItem] = []

    def add(
        zone: Zone | None, category: str, count: int, priority: Priority, near: list[Place]
    ) -> None:
        if category in avoid:
            return
        wanted = [p for p in near if sloped or p != LOW]
        items.append(
            BriefItem(category=category, count=count, priority=priority, near=wanted, zone=zone)
        )

    for wish in intent.furniture:
        add(None, wish.category, wish.count, "required", [])
    # Pièce serrée (au moins deux zones et moins de 10 m² par zone : séjour avec cuisine et
    # repas, chambre avec bureau) : les « en plus » des kits (tapis, deuxième fauteuil…) ne
    # passent pas. Une pièce à une seule zone garde ses « en plus ».
    tight = len(zones) >= 2 and usable / len(zones) < TIGHT_M2_PER_ZONE
    for zone in zones:
        # Combles (H1) : le coin nuit est un lit encastré, tête vers la jambette (le générateur
        # revient au lit classique s'il ne tient pas sous la pente).
        kit = KITS["couchage_pente"] if sloped and zone == "couchage" else KITS[zone]
        for category, count, priority, near in kit:
            if tight and priority == "nice_to_have":
                continue
            add(zone, category, count, priority, near)
    target = target_items(usable)
    total = sum(i.count for i in items)
    fillers = FILLERS_SLOPED if sloped else FILLERS_FLAT
    k = 0
    while zones and not tight and total < target and k < 4 * len(zones) * len(fillers):
        zone = zones[k % len(zones)]
        category, count, priority, near = fillers[(k // len(zones)) % len(fillers)]
        k += 1
        if category in avoid:
            continue
        add(zone, category, count, priority, near)
        total += count
    use = room_type or ("attic" if sloped else "")
    return LayoutBrief(
        room_use=use,
        items=items,
        notes=(
            f"Brief par zones : {', '.join(zones) or 'aucune'} ; {total} meubles visés pour "
            f"{usable:.0f} m² à plus de 1,40 m (densité cible {TARGET_DENSITY:.2f} meuble/m²)."
        ),
    )


def zone_bays(room: Polygon, zones: list[Zone], axis: tuple[float, float]) -> dict[str, Polygon]:
    """Une baie par zone : la pièce découpée en tranches égales le long de `axis` (le faîtage,
    sinon le grand axe de la pièce), dans l'ordre des zones."""
    if not zones or room.is_empty:
        return {}
    ux, uy = axis
    norm = math.hypot(ux, uy) or 1.0
    ux, uy = ux / norm, uy / norm
    coords = list(room.exterior.coords)
    along = [x * ux + y * uy for x, y in coords]
    across = [-x * uy + y * ux for x, y in coords]
    lo, hi = min(along), max(along)
    a0, a1 = min(across) - 1.0, max(across) + 1.0
    step = (hi - lo) / len(zones)
    out: dict[str, Polygon] = {}
    for i, zone in enumerate(zones):
        s0, s1 = lo + i * step, lo + (i + 1) * step
        strip = Polygon(
            [
                (s * ux - t * uy, s * uy + t * ux)
                for s, t in ((s0, a0), (s1, a0), (s1, a1), (s0, a1))
            ]
        )
        part = room.intersection(strip)
        if not part.is_empty:
            merged = unary_union([part])
            out.setdefault(zone, merged if isinstance(merged, Polygon) else merged.convex_hull)
    return out


def long_axis(room: Polygon) -> tuple[float, float]:
    """Direction du grand côté du rectangle minimal qui enveloppe la pièce."""
    rect = room.minimum_rotated_rectangle
    if not isinstance(rect, Polygon):
        return (1.0, 0.0)
    pts = list(rect.exterior.coords)[:3]
    (x0, y0), (x1, y1), (x2, y2) = pts
    d1 = (x1 - x0, y1 - y0)
    d2 = (x2 - x1, y2 - y1)
    return d1 if math.hypot(*d1) >= math.hypot(*d2) else d2
