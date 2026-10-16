"""Habillage du rendu (D4, enrichi en C3) : ce qui rend une pièce habitée sans rien changer à la
scène ni aux règles d'aménagement.

Par meuble : lampe de chevet allumée, petite plante sur commode, coussins et plaid sur les lits,
livres sur les étagères ouvertes, tirage encadré au-dessus d'un lit ou d'un canapé adossé. Par
pièce : suspension sous le faîtage (ou au plafond), une ou deux grandes plantes dans des coins
libres où la pente laisse la place. Par fenêtre : deux rideaux légers. Tout suit l'**ambiance**
de la pièce (`metadata["ambiance"]` d'une `Room` : naturel, chalet, scandinave, industriel,
contemporain ; naturel par défaut) : couleurs du linge, des tirages et des tapis, suspension,
nombre de plantes, livres. Les modèles viennent du kit CC0 de Kenney ; plaid, rideaux,
suspension et tirages (aplats abstraits) sont construits par Blender, sans asset tiers.
"""

from __future__ import annotations

import math
from typing import Literal, cast

from shapely.affinity import rotate, translate
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from openplan.geom.built_in_bed import BUILT_IN_BED, HEAD_THICKNESS_M, MATTRESS_TOP_M
from openplan.geom.roof import derived_roofs
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.scene.catalog import CatalogError, asset
from openplan.schemas.bake import (
    RGB,
    BakedOpening,
    Decor,
    DecorPalette,
    DecorStyle,
)
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import Furniture, Room
from openplan.schemas.scene import Level
from openplan.validation.layout import WALKABLE_CATEGORIES, clearance_at, stair_zones

DECOR_LAMP = "models/lampRoundTable.glb"
DECOR_PLANT = "models/plantSmall1.glb"
DECOR_FLOOR_PLANT = "models/pottedPlant.glb"
DECOR_PILLOW = "models/pillow.glb"
DECOR_BOOKS = "models/books.glb"
DECOR_STYLES: tuple[DecorStyle, ...] = (
    "naturel",
    "chalet",
    "scandinave",
    "industriel",
    "contemporain",
)
PALETTES: dict[DecorStyle, DecorPalette] = {
    "naturel": DecorPalette(
        style="naturel",
        textile=(0.86, 0.82, 0.74),
        accent=(0.62, 0.36, 0.24),
        rug=(0.56, 0.44, 0.29),
        rug_stripe=(0.22, 0.16, 0.10),
        prints=[(0.74, 0.55, 0.38), (0.42, 0.47, 0.36), (0.88, 0.80, 0.68)],
        curtain=(0.93, 0.90, 0.84),
        pendant="rattan",
        floor_plants=2,
        books=True,
    ),
    "chalet": DecorPalette(
        style="chalet",
        textile=(0.90, 0.86, 0.78),
        accent=(0.55, 0.14, 0.12),
        rug=(0.30, 0.22, 0.17),
        rug_stripe=(0.86, 0.80, 0.70),
        prints=[(0.55, 0.14, 0.12), (0.25, 0.32, 0.24), (0.86, 0.80, 0.70)],
        curtain=(0.88, 0.82, 0.72),
        pendant="metal_black",
        floor_plants=1,
        books=True,
    ),
    "scandinave": DecorPalette(
        style="scandinave",
        textile=(0.93, 0.93, 0.91),
        accent=(0.80, 0.62, 0.25),
        rug=(0.88, 0.86, 0.82),
        rug_stripe=(0.20, 0.20, 0.21),
        prints=[(0.55, 0.62, 0.68), (0.80, 0.62, 0.25), (0.93, 0.92, 0.90)],
        curtain=(0.97, 0.97, 0.96),
        pendant="white",
        floor_plants=2,
        books=False,
    ),
    "industriel": DecorPalette(
        style="industriel",
        textile=(0.36, 0.36, 0.38),
        accent=(0.55, 0.33, 0.18),
        rug=(0.26, 0.26, 0.27),
        rug_stripe=(0.78, 0.46, 0.24),
        prints=[(0.15, 0.15, 0.16), (0.55, 0.33, 0.18), (0.75, 0.74, 0.72)],
        curtain=(0.80, 0.79, 0.77),
        pendant="metal_black",
        floor_plants=1,
        books=True,
    ),
    "contemporain": DecorPalette(
        style="contemporain",
        textile=(0.82, 0.76, 0.68),
        accent=(0.45, 0.47, 0.30),
        rug=(0.80, 0.74, 0.65),
        rug_stripe=(0.10, 0.10, 0.10),
        prints=[(0.10, 0.10, 0.10), (0.45, 0.47, 0.30), (0.82, 0.76, 0.68)],
        curtain=(0.92, 0.89, 0.85),
        pendant="brass",
        floor_plants=2,
        books=False,
    ),
}
_FRAMED = {
    "bed_double": (0.9, 0.6),
    "bed_builtin": (0.6, 0.45),
    "bed_single": (0.6, 0.45),
    "sofa": (0.9, 0.6),
    "sofa_lounge": (1.0, 0.65),
}
_PLANT_ON = {"chest", "tv_unit", "low_storage", "bookshelf"}
_PATTERNS: tuple[Literal["blocks", "arch", "bands"], ...] = ("blocks", "arch", "bands")
MATTRESS_TOP = 0.62
"""Dessus de la couette des lits Kenney, en fraction de la hauteur de la boîte (mesuré sur
bedSingle et bedDouble : 0,60 à 0,67)."""
SHELVES = (0.15, 0.42, 0.69)
"""Tablettes de bookcaseOpen, en fraction de la hauteur (mesurées sur le modèle)."""
PENDANT_DROP_M = 0.8
PENDANT_MIN_BOTTOM_M = 2.1
"""Le bas de la suspension reste au-dessus de la tête."""
FLOOR_PLANT = (0.45, 1.1)
"""Grande plante en pot : diamètre, hauteur."""
PLANT_INSET_M = 0.35
PLANT_FREE_M = 0.25
"""Écart minimal entre une grande plante et un meuble."""
DRAPE_DROP_M = 0.22
"""Retombée d'un plaid ou d'une couette sur chaque côté du lit (H3)."""
THROW_BIAS_DEG = 7.0
"""Plaid posé de biais, pas au cordeau (références 13 et 14)."""
JUTE_DEPTH_M = 0.75
JUTE_STYLES = ("naturel", "chalet", "scandinave")
"""Tapis de jute devant les lits (H3) ; les autres ambiances gardent un tapis de laine uni."""
CURTAIN_WIDTH_M = 0.4
CURTAIN_OFFSET_M = 0.08


def ambiance(level: Level) -> DecorStyle:
    """Style de l'intention de la première pièce qui en a un valide (V1) ; à défaut l'ancienne
    `metadata["ambiance"]` (projet pas encore migré) ; sinon « naturel »."""
    for e in level.elements.values():
        if isinstance(e, Room):
            wanted = e.intent.style if e.intent is not None else e.metadata.get("ambiance")
            if isinstance(wanted, str) and wanted in PALETTES:
                return wanted
    return "naturel"


def palette_of(level: Level) -> DecorPalette:
    return PALETTES[ambiance(level)]


def _rooms(level: Level, graph: WallGraph) -> list[Polygon]:
    out: list[Polygon] = []
    for e in level.elements.values():
        if isinstance(e, Room):
            try:
                out.append(graph.room_polygon(e.id))
            except GeometryError:
                continue
    return out


def _footprint(f: Furniture) -> Polygon:
    t, b = f.transform, f.bbox
    w, d = b.width * t.scale, b.depth * t.scale
    shape = box(-w / 2, -d / 2, w / 2, d / 2)
    return cast(
        Polygon, translate(rotate(shape, t.yaw, use_radians=True), t.position.x, t.position.y)
    )


def dress_level(level: Level, openings: list[BakedOpening] | None = None) -> list[Decor]:
    """Tout l'habillage du niveau, dans l'ambiance de sa pièce."""
    pal = palette_of(level)
    graph = WallGraph.from_level(level)
    walls = graph.walls_union() if graph.walls else None
    roofs = list(derived_roofs(level, graph).values())
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture) and e.visible]
    out: list[Decor] = []
    for k, f in enumerate(furniture):
        out.extend(_on_furniture(f, k, pal, walls, roofs, level))
    rooms = _rooms(level, graph)
    out.extend(_bedside_rugs(furniture, pal, walls))
    out.extend(_pendants(rooms, roofs, level, pal))
    doors = [o for o in openings or [] if o.kind == "door"]
    out.extend(_floor_plants(rooms, furniture, doors, roofs, level, pal))
    out.extend(_curtains(openings or [], rooms, roofs, level, pal))
    return out


def _on_furniture(
    f: Furniture,
    index: int,
    pal: DecorPalette,
    walls: BaseGeometry | None,
    roofs: list[DerivedRoof],
    level: Level,
) -> list[Decor]:
    t, b = f.transform, f.bbox
    x, y, yaw = t.position.x, t.position.y, t.yaw
    w, d, h = b.width * t.scale, b.depth * t.scale, b.height * t.scale
    z0, top = t.position.z, t.position.z + h
    ux, uy = math.cos(yaw), math.sin(yaw)
    out: list[Decor] = []
    if f.category == "nightstand":
        out.append(
            Decor(
                kind="table_lamp",
                model=DECOR_LAMP,
                x=x,
                y=y,
                z=top,
                yaw=yaw,
                width=0.26,
                depth=0.26,
                height=0.42,
                light_w=25.0,
            )
        )
    elif f.category in _PLANT_ON and w > 0.5 and f.category != "bookshelf":
        px, py = x + ux * (w / 2 - 0.2), y + uy * (w / 2 - 0.2)
        out.append(
            Decor(
                kind="plant",
                model=DECOR_PLANT,
                x=px,
                y=py,
                z=top,
                yaw=yaw,
                width=0.24,
                depth=0.24,
                height=0.32,
            )
        )
    if f.category in ("bed_single", "bed_double"):
        out.extend(_bed_linen(x, y, yaw, w, d, h, z0, pal, f.category == "bed_double"))
    elif f.category == BUILT_IN_BED:
        # Lit encastré (H1) : matelas à 0,55 m, oreillers contre la planche de tête.
        out.extend(
            _bed_linen(
                x,
                y,
                yaw,
                w,
                d,
                h,
                z0,
                pal,
                w >= 1.3,
                z_top=z0 + MATTRESS_TOP_M,
                pillow_at=(HEAD_THICKNESS_M + 0.2) / d,
                duvet=True,
            )
        )
    if f.category == "bookshelf" and pal.books and _model(f).endswith("bookcaseOpen.glb"):
        out.extend(_books(x, y, yaw, w, d, h, z0, index))
    # Ce qui habille le meuble lui-même le désigne dans un rendu (P4) ; le cadre au mur et la
    # plante posée dessus restent du décor sans porteur.
    out = [d.model_copy(update={"host": f.id}) if d.kind in HOSTED_KINDS else d for d in out]
    if f.category in _FRAMED and walls is not None:
        frame = _frame(f, walls=walls, roofs=roofs, level=level, pal=pal, index=index)
        if frame is not None:
            out.append(frame)
    return out


HOSTED_KINDS = frozenset({"table_lamp", "pillow", "duvet", "throw", "books"})
"""Décor rattaché au meuble qu'il habille (P4) : un clic dessus désigne ce meuble."""


BED_CATEGORIES = ("bed_double", "bed_single", BUILT_IN_BED)


def _bedside_rugs(
    furniture: list[Furniture], pal: DecorPalette, walls: BaseGeometry | None = None
) -> list[Decor]:
    """Un tapis de jute (ou de laine) au pied de chaque lit (H3), s'il n'y a pas déjà un tapis
    et si rien d'autre n'occupe la place."""
    out: list[Decor] = []
    solids = [f for f in furniture if f.category not in WALKABLE_CATEGORIES]
    rugs = [_footprint(f) for f in furniture if f.category in WALKABLE_CATEGORIES]
    for bed in (f for f in furniture if f.category in BED_CATEGORIES):
        t, b = bed.transform, bed.bbox
        w, d = b.width * t.scale, b.depth * t.scale
        fx, fy = -math.sin(t.yaw), math.cos(t.yaw)
        ux, uy = math.cos(t.yaw), math.sin(t.yaw)
        # Au pied du lit d'abord ; sinon le long d'un côté (H5), plus court que le lit.
        spots = [
            (
                fx * (d / 2 + JUTE_DEPTH_M / 2 + 0.05),
                fy * (d / 2 + JUTE_DEPTH_M / 2 + 0.05),
                w + 0.4,
                t.yaw,
            )
        ]
        for side in (1.0, -1.0):
            reach = w / 2 + JUTE_DEPTH_M / 2 + 0.05
            spots.append((ux * side * reach, uy * side * reach, 0.75 * d, t.yaw + math.pi / 2))
        chosen = None
        for dx, dy, width, yaw in spots:
            cx, cy = t.position.x + dx, t.position.y + dy
            shape = cast(
                Polygon,
                translate(
                    rotate(
                        box(-width / 2, -JUTE_DEPTH_M / 2, width / 2, JUTE_DEPTH_M / 2),
                        yaw,
                        use_radians=True,
                    ),
                    cx,
                    cy,
                ),
            )
            if any(shape.intersects(r) for r in rugs):
                continue
            if any(f.id != bed.id and _footprint(f).intersects(shape) for f in solids):
                continue
            if walls is not None and shape.intersects(walls):
                continue
            chosen = (cx, cy, width, yaw)
            rugs.append(shape)
            break
        if chosen is None:
            continue
        cx, cy, width, yaw = chosen
        out.append(
            Decor(
                kind="jute_rug",
                x=cx,
                y=cy,
                z=t.position.z,
                yaw=yaw,
                width=width,
                depth=JUTE_DEPTH_M,
                height=0.012,
                color=(0.62, 0.50, 0.33) if pal.style in JUTE_STYLES else pal.rug,
                pattern=None,
                material=None,
                accent=None if pal.style in JUTE_STYLES else pal.rug_stripe,
            )
        )
    return out


def _bed_linen(  # noqa: PLR0913 - boîte du lit et réglages du linge
    x: float,
    y: float,
    yaw: float,
    w: float,
    d: float,
    h: float,
    z0: float,
    pal: DecorPalette,
    double: bool,
    *,
    z_top: float | None = None,
    pillow_at: float = 0.42,
    duvet: bool = False,
) -> list[Decor]:
    """Coussins devant l'oreiller du modèle et plaid replié au pied de la couette. La tête du
    lit est à l'arrière (face avant = pied)."""
    fx, fy = -math.sin(yaw), math.cos(yaw)
    ux, uy = math.cos(yaw), math.sin(yaw)
    bx, by = x - fx * d / 2, y - fy * d / 2  # milieu de la tête de lit
    z_top = z0 + MATTRESS_TOP * h if z_top is None else z_top
    out: list[Decor] = []
    across = (-0.24 * w, 0.24 * w) if double else (0.0,)
    for i, a in enumerate(across):
        out.append(
            Decor(
                kind="pillow",
                model=DECOR_PILLOW,
                x=bx + fx * pillow_at * d + ux * a,
                y=by + fy * pillow_at * d + uy * a,
                z=z_top,
                yaw=yaw,
                width=min(0.48, 0.42 * w if double else 0.6 * w),
                depth=0.32,
                height=0.13,
                color=pal.accent if i % 2 == 0 else pal.textile,
            )
        )
    if duvet:
        # Lit encastré (H3) : couette de lin souple et froissée, rabattue avant les oreillers.
        out.append(
            Decor(
                kind="duvet",
                x=bx + fx * (0.58 * d),
                y=by + fy * (0.58 * d),
                z=z_top,
                yaw=yaw,
                width=w + 2 * DRAPE_DROP_M,
                depth=0.78 * d,
                height=0.04,
                color=pal.textile,
                drape=w / 2,
            )
        )
    # Plaid tissé à franges (H3), posé de biais au pied, qui retombe sur les côtés du lit.
    out.append(
        Decor(
            kind="throw",
            x=bx + fx * 0.7 * d,
            y=by + fy * 0.7 * d,
            z=z_top + (0.04 if duvet else 0.0),
            yaw=yaw + math.radians(THROW_BIAS_DEG),
            width=w + 2 * DRAPE_DROP_M,
            depth=0.5,
            height=0.02,
            color=pal.accent,
            accent=pal.textile,
            drape=w / 2,
        )
    )
    return out


def _books(
    x: float, y: float, yaw: float, w: float, d: float, h: float, z0: float, index: int
) -> list[Decor]:
    """Une rangée de livres sur chaque tablette, de longueur et de côté variables."""
    ux, uy = math.cos(yaw), math.sin(yaw)
    gap = (SHELVES[1] - SHELVES[0]) * h
    out: list[Decor] = []
    for s, rel in enumerate(SHELVES):
        share = (0.45, 0.65, 0.35)[(index + s) % 3]
        side = (-1.0, 1.0)[(index + s) % 2]
        row = share * (w - 0.06)
        along = side * (w - 0.06 - row) / 2
        out.append(
            Decor(
                kind="books",
                model=DECOR_BOOKS,
                x=x + ux * along,
                y=y + uy * along,
                z=z0 + rel * h,
                yaw=yaw,
                width=row,
                depth=0.75 * d,
                height=min(0.24, gap - 0.04),
            )
        )
    return out


def _frame(
    f: Furniture,
    *,
    walls: BaseGeometry | None,
    roofs: list[DerivedRoof],
    level: Level,
    pal: DecorPalette,
    index: int,
) -> Decor | None:
    t = f.transform
    x, y, yaw = t.position.x, t.position.y, t.yaw
    d, top = f.bbox.depth * t.scale, t.position.z + f.bbox.height * t.scale
    fx, fy = -math.sin(yaw), math.cos(yaw)
    bx, by = x - fx * d / 2, y - fy * d / 2
    if walls is None or Point(bx, by).distance(walls) > 0.12:
        return None  # pas adossé à un mur
    fw, fh = _FRAMED[f.category]
    z_bottom = max(top + 0.35, 1.2 - fh / 2)
    if roofs:
        room = clearance_at((bx, by), roofs, level) - 0.1
        z_bottom = min(z_bottom, room - fh)
        if z_bottom < top + 0.15:
            return None  # la pente descend trop bas derrière ce meuble
    colors = pal.prints
    return Decor(
        kind="frame",
        x=bx - fx * 0.015,
        y=by - fy * 0.015,
        z=z_bottom,
        yaw=yaw,
        width=fw,
        depth=0.03,
        height=fh,
        color=colors[index % len(colors)],
        accent=colors[(index + 1) % len(colors)],
        pattern=_PATTERNS[index % len(_PATTERNS)],
    )


def _ceiling_at(point: tuple[float, float], roofs: list[DerivedRoof], level: Level) -> float:
    if roofs:
        return float(clearance_at(point, roofs, level))
    return level.height


def _pendants(
    rooms: list[Polygon], roofs: list[DerivedRoof], level: Level, pal: DecorPalette
) -> list[Decor]:
    """Une suspension au centre de chaque pièce, sous le faîtage quand il y a des pentes."""
    out: list[Decor] = []
    for poly in rooms:
        c = poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid
        ceiling = _ceiling_at((c.x, c.y), roofs, level)
        drop = min(PENDANT_DROP_M, ceiling - PENDANT_MIN_BOTTOM_M)
        if drop < 0.3:
            continue
        out.append(
            Decor(
                kind="pendant",
                x=float(c.x),
                y=float(c.y),
                z=level.elevation + ceiling - drop,
                width=0.42 if pal.pendant == "rattan" else 0.34,
                depth=0.42 if pal.pendant == "rattan" else 0.34,
                height=drop,
                light_w=60.0,
                material=pal.pendant,
            )
        )
    return out


def _candidates(poly: Polygon) -> list[tuple[float, float]]:
    """Coins de la pièce et milieux de ses murs, rentrés vers l'intérieur."""
    c = poly.centroid
    coords = list(poly.exterior.coords)[:-1]
    points = list(coords) + [
        ((ax + bx) / 2.0, (ay + by) / 2.0)
        for (ax, ay), (bx, by) in zip(coords, coords[1:] + coords[:1], strict=True)
    ]
    out: list[tuple[float, float]] = []
    for px, py in points:
        vx, vy = c.x - px, c.y - py
        n = math.hypot(vx, vy)
        if n > 1e-6:
            inset = PLANT_INSET_M * (1.4 if (px, py) in coords else 1.3)
            out.append((px + vx / n * inset, py + vy / n * inset))
    return out


def _headroom(x: float, y: float, half: float, roofs: list[DerivedRoof], level: Level) -> float:
    """Hauteur libre la plus basse sur l'emprise carrée d'un objet (centre et quatre coins)."""
    points = [(x, y)] + [(x + a, y + b) for a in (-half, half) for b in (-half, half)]
    return min(_ceiling_at(p, roofs, level) for p in points)


def _floor_plants(
    rooms: list[Polygon],
    furniture: list[Furniture],
    doors: list[BakedOpening],
    roofs: list[DerivedRoof],
    level: Level,
    pal: DecorPalette,
) -> list[Decor]:
    """Grandes plantes dans les coins et au milieu des murs : à distance des meubles et des
    portes, là où la pente laisse la hauteur de la plante sur toute son emprise ; les places
    les plus hautes d'abord."""
    taken = [_footprint(f).buffer(PLANT_FREE_M) for f in furniture if not _walkable(f)]
    taken += [Point(o.center.x, o.center.y).buffer(o.width / 2 + 0.6) for o in doors]
    taken += [opening.union(arrival) for _, opening, arrival in stair_zones(level)]
    size, height = FLOOR_PLANT
    out: list[Decor] = []
    for poly in rooms:
        spots: list[tuple[float, float, float]] = []
        c = poly.centroid
        for sx, sy in _candidates(poly):
            spot = Point(sx, sy)
            if not poly.contains(spot.buffer(size / 2)):
                continue
            if any(t.intersects(spot.buffer(size / 2)) for t in taken):
                continue
            room = _headroom(sx, sy, size / 2, roofs, level)
            if room < height + 0.1:
                continue
            spots.append((room, sx, sy))
        spots.sort(key=lambda s: -s[0])
        chosen: list[tuple[float, float]] = []
        for _room, sx, sy in spots:
            if len(chosen) >= pal.floor_plants:
                break
            if any(math.hypot(sx - ax, sy - ay) < 1.0 for ax, ay in chosen):
                continue
            chosen.append((sx, sy))
            out.append(
                Decor(
                    kind="floor_plant",
                    model=DECOR_FLOOR_PLANT,
                    x=sx,
                    y=sy,
                    z=level.elevation,
                    yaw=math.atan2(c.y - sy, c.x - sx),
                    width=size,
                    depth=size,
                    height=height,
                )
            )
    return out


def _walkable(f: Furniture) -> bool:
    return f.category in WALKABLE_CATEGORIES


def _model(f: Furniture) -> str:
    try:
        return asset(f.asset_id).model or ""
    except CatalogError:
        return ""


def _curtains(
    openings: list[BakedOpening],
    rooms: list[Polygon],
    roofs: list[DerivedRoof],
    level: Level,
    pal: DecorPalette,
) -> list[Decor]:
    """Deux rideaux légers de part et d'autre de chaque fenêtre, côté pièce, du sol à un peu
    au-dessus de la baie (sous la pente s'il y en a une)."""
    out: list[Decor] = []
    for o in openings:
        if o.kind != "window":
            continue
        dx, dy = o.direction
        nx, ny = o.normal
        inset = o.wall_thickness / 2 + CURTAIN_OFFSET_M
        probe = Point(o.center.x + nx * (inset + 0.2), o.center.y + ny * (inset + 0.2))
        if rooms and not any(r.contains(probe) for r in rooms):
            nx, ny = -nx, -ny
        floor = level.elevation
        top = o.center.z + o.height + 0.25
        for side in (-1.0, 1.0):
            along = side * (o.width / 2 + CURTAIN_WIDTH_M / 2 - 0.05)
            cx = o.center.x + dx * along + nx * inset
            cy = o.center.y + dy * along + ny * inset
            room = _ceiling_at((cx, cy), roofs, level) + floor
            z_top = min(top, room - 0.05)
            if z_top - floor < 1.2:
                continue
            out.append(
                Decor(
                    kind="curtain",
                    x=cx,
                    y=cy,
                    z=floor + 0.02,
                    yaw=math.atan2(dy, dx),
                    width=CURTAIN_WIDTH_M,
                    depth=0.04,
                    height=z_top - floor - 0.02,
                    color=pal.curtain,
                )
            )
    return out


def palette_colors(style: DecorStyle) -> tuple[RGB, RGB]:
    """Teintes du tapis pour une ambiance (fond, rayures)."""
    pal = PALETTES[style]
    return pal.rug, pal.rug_stripe
