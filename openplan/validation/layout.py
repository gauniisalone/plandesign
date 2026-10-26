"""Règles d'aménagement géométriques (roadmap 4.1 et 3.2) : ce qui ne tient pas.

Chaque règle produit des violations compréhensibles, avec les éléments concernés :

- `element_too_tall_under_slope` : un meuble sous un versant doit tenir sous la hauteur libre
  à l'aplomb de chacun des coins de son emprise (dure par défaut) ;
- `furniture_overlap` : deux meubles (ou éléments fixes) qui se chevauchent (dure) ;
- `furniture_in_wall` : un meuble qui entre dans un mur (dure) ;
- `stairs_on_wall`, `stairs_outside_room` : une trémie qui coupe un mur ou sort de la pièce
  (dures, E4) ; `furniture_in_stairwell` (dure), `stairs_arrival_blocked` et
  `stairs_headroom` (souples) complètent les règles d'escalier ;
- `door_swing_blocked` : un meuble dans le débattement d'une porte (dure) ;
- `window_blocked` : un meuble plus haut que l'allège devant une fenêtre (souple) ;
- `clearance_blocked` : un meuble dans le dégagement d'un radiateur ou d'un élément fixe
  (`keep_clear`), ou dans le dégagement que demande un autre meuble du catalogue (souple) ;
- `furniture_unreachable` : un meuble que l'on ne peut pas atteindre depuis la porte avec un
  passage de 0,80 m de large (souple) ;
- `velux_casing` : un meuble qui entre dans le caisson d'un velux (KAN-67, souple) ; le volume
  est celui que le bake bâtit (`geom.velux.velux_casings`), en pleine profondeur ;
- `truss_off_ridge` : une ferme intermédiaire que le faîtage ne porte plus (KAN-94, souple ;
  gardée, jamais supprimée en silence), `truss_lowered` : sa poutre abaissée sous la pente.

« Dure » = sévérité `high` (l'édition est refusée, la proposition d'aménagement écartée) ;
« souple » = `medium` (affichée, pénalité de score). Une `Constraint` de la sorte
correspondante (`NO_OVERLAP`, `KEEP_CLEAR`, `MIN_PASSAGE`, `MAX_HEIGHT_UNDER_SLOPE`) ciblant
l'élément rend la règle souple (`hard: false`), dure (`hard: true`) ou la désactive
(`enabled: false`). Le moteur de propositions (3.4) applique exactement ces règles.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import unary_union

from openplan.geom.built_in_bed import (
    BUILT_IN_BED,
    MIN_HEADROOM_M,
    STANDING_M,
    headroom_at,
    standing_at,
)
from openplan.geom.convex import Solid
from openplan.geom.opening_symbols import (
    SLIDE_DEPTH_M,
    STACK_KINDS,
    SWING_KINDS,
    folding_depth,
    leaves_of,
    stack_span,
    swing_side,
)
from openplan.geom.roof import derived_roofs, height_under_slope
from openplan.geom.stairs import ARRIVAL_DEPTH_M, HEADROOM_M, arrival_zone, headroom, stair_layout
from openplan.geom.truss import truss_parts
from openplan.geom.velux import velux_casings as baked
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.scene.catalog import CatalogError, load_catalog
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import (
    DOOR_KINDS,
    Fixture,
    Furniture,
    Opening,
    OpeningKind,
    Radiator,
    Room,
    Stairs,
    Truss,
)
from openplan.schemas.numbers import metres
from openplan.schemas.results import Severity, Violation
from openplan.schemas.scene import ConstraintKind, Level, Project

MARGIN_M = 0.0
PASSAGE_WIDTH_M = 0.80
"""Largeur de passage exigée de la porte à chaque meuble (3.2)."""
WINDOW_STRIP_M = 0.30
"""Bande devant une fenêtre où un meuble plus haut que l'allège la masque."""
OVERLAP_EPS_M2 = 1e-4
"""En dessous, deux emprises se touchent sans se chevaucher."""
SECTOR_STEPS = 8


def footprint_corners(
    x: float, y: float, yaw: float, width: float, depth: float
) -> list[tuple[float, float]]:
    """Coins de l'emprise d'un objet centré en (x, y), tourné de `yaw` (antihoraire)."""
    c, s = math.cos(yaw), math.sin(yaw)
    out: list[tuple[float, float]] = []
    for dx, dy in (
        (-width / 2, -depth / 2),
        (width / 2, -depth / 2),
        (width / 2, depth / 2),
        (-width / 2, depth / 2),
    ):
        out.append((x + c * dx - s * dy, y + s * dx + c * dy))
    return out


def clearance_at(point: tuple[float, float], roofs: list[DerivedRoof], level: Level) -> float:
    """Hauteur libre au-dessus du sol à l'aplomb du point : la plus basse des pentes,
    plafonnée à la hauteur du niveau."""
    heights = [
        height_under_slope(
            point,
            (r.eave[0].x, r.eave[0].y),
            (r.eave[1].x, r.eave[1].y),
            (r.inward.x, r.inward.y),
            r.eave_height,
            r.pitch,
            level.height,
        )
        for r in roofs
    ]
    return min(heights, default=level.height)


@dataclass(frozen=True)
class Piece:
    """Un meuble ou un élément fixe avec son emprise au sol."""

    element: Furniture | Fixture
    polygon: Polygon

    @property
    def label(self) -> str:
        return f"« {self.element.name or self.element.category} »"

    @property
    def top(self) -> float:
        t, b = self.element.transform, self.element.bbox
        return t.position.z + b.height * t.scale

    def local_rect(self, dx0: float, dx1: float, dy0: float, dy1: float) -> Polygon:
        """Rectangle exprimé dans le repère du meuble (x = largeur, y = profondeur, +y =
        face avant), ramené dans le plan."""
        t = self.element.transform
        c, s = math.cos(t.yaw), math.sin(t.yaw)
        pts = [
            (t.position.x + c * dx - s * dy, t.position.y + s * dx + c * dy)
            for dx, dy in ((dx0, dy0), (dx1, dy0), (dx1, dy1), (dx0, dy1))
        ]
        return Polygon(pts)


WALKABLE_CATEGORIES = frozenset({"rug", "rug_round"})
"""Tapis : on marche dessus et on pose des meubles dessus (D4) ; ignorés par les règles de
chevauchement, de dégagement, de débattement et par le générateur comme obstacle."""


def pieces_of(level: Level) -> list[Piece]:
    out: list[Piece] = []
    for element in level.elements.values():
        if not isinstance(element, Furniture | Fixture) or not element.visible:
            continue
        if isinstance(element, Furniture) and element.category in WALKABLE_CATEGORIES:
            continue
        t, b = element.transform, element.bbox
        corners = footprint_corners(
            t.position.x, t.position.y, t.yaw, b.width * t.scale, b.depth * t.scale
        )
        out.append(Piece(element, Polygon(corners)))
    return out


def _room_polygons(level: Level, graph: WallGraph) -> dict[UUID, Polygon]:
    out: dict[UUID, Polygon] = {}
    for element in level.elements.values():
        if not isinstance(element, Room):
            continue
        try:
            polygon = graph.room_polygon(element.id)
        except GeometryError:
            continue
        if not polygon.is_empty:
            out[element.id] = polygon
    return out


def _wall_frame(
    graph: WallGraph, wall_id: UUID
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], float] | None:
    """(origine, direction unitaire, normale gauche, demi-épaisseur) d'un mur."""
    try:
        ux, uy = graph.wall_direction(wall_id)
    except GeometryError:
        return None
    a, _ = graph.wall_axis(wall_id)
    half = graph.wall(wall_id).thickness / 2.0
    return (a.x, a.y), (ux, uy), (-uy, ux), half


def _inside_sign(
    graph: WallGraph, wall_id: UUID, along: float, rooms: dict[UUID, Polygon]
) -> float:
    """+1 si l'intérieur (une pièce) est du côté de la normale gauche du mur, −1 sinon ; +1
    faute de pièce."""
    frame = _wall_frame(graph, wall_id)
    if frame is None:
        return 1.0
    (ax, ay), (ux, uy), (nx, ny), half = frame
    probe = half + 0.05
    left = Point(ax + ux * along + nx * probe, ay + uy * along + ny * probe)
    right = Point(ax + ux * along - nx * probe, ay + uy * along - ny * probe)
    for polygon in rooms.values():
        if polygon.contains(left):
            return 1.0
        if polygon.contains(right):
            return -1.0
    return 1.0


def door_zone(  # noqa: PLR0911 - une sortie par sorte d'ouverture
    graph: WallGraph, door: Opening, rooms: dict[UUID, Polygon]
) -> Polygon | None:
    """Débattement d'une porte : quart de disque de rayon = largeur du vantail autour de chaque
    gond (deux pour une double porte ou une porte-fenêtre double), du côté d'ouverture ; bande
    des panneaux repliés pour une pliante ; rien pour une coulissante (voir `slide_zone`) ;
    sans sens connu, un rectangle de la largeur de la porte de part et d'autre."""
    if door.kind not in SWING_KINDS:
        return None
    frame = _wall_frame(graph, door.host_wall)
    if frame is None:
        return None
    (ax, ay), (ux, uy), (nx, ny), half = frame
    p0 = (ax + ux * door.offset, ay + uy * door.offset)
    p1 = (p0[0] + ux * door.width, p0[1] + uy * door.width)
    if door.swing is None:
        depth = door.width
        return Polygon(
            [
                (p0[0] + nx * depth, p0[1] + ny * depth),
                (p1[0] + nx * depth, p1[1] + ny * depth),
                (p1[0] - nx * depth, p1[1] - ny * depth),
                (p0[0] - nx * depth, p0[1] - ny * depth),
            ]
        )
    side = swing_side(door)
    if door.kind is OpeningKind.FOLDING_DOOR:
        return _strip(
            graph, door.host_wall, door.offset, door.width, folding_depth(door.width), side
        )
    ox, oy = nx * side, ny * side
    sectors: list[Polygon] = []
    for leaf in leaves_of(door, p0, p1, (ux, uy)):
        (hx, hy), (dx, dy), r = leaf.hinge, leaf.toward, leaf.width
        points = [(hx, hy)]
        for k in range(SECTOR_STEPS + 1):
            t = (math.pi / 2.0) * k / SECTOR_STEPS
            points.append(
                (
                    hx + r * (math.cos(t) * dx + math.sin(t) * ox),
                    hy + r * (math.cos(t) * dy + math.sin(t) * oy),
                )
            )
        sectors.append(Polygon(points))
    del half
    if not sectors:
        return None
    if len(sectors) == 1:
        return sectors[0]
    merged = unary_union(sectors)
    return merged if isinstance(merged, Polygon) else merged.convex_hull


def slide_zone(graph: WallGraph, door: Opening) -> Polygon | None:
    """Coulissante en applique (E5) : la bande, le long de la face du mur côté rail, que le
    vantail parcourt en s'ouvrant ; elle doit rester libre."""
    if door.kind is not OpeningKind.SLIDING_DOOR:
        return None
    span = stack_span(door)
    if span is None:
        return None
    return _strip(
        graph, door.host_wall, span[0], span[1] - span[0], SLIDE_DEPTH_M, swing_side(door)
    )


def _strip(
    graph: WallGraph, wall_id: UUID, offset: float, width: float, depth: float, sign: float
) -> Polygon | None:
    """Bande de `depth` devant un tronçon de mur (du côté `sign` de la normale gauche),
    à partir de la face du mur."""
    frame = _wall_frame(graph, wall_id)
    if frame is None:
        return None
    (ax, ay), (ux, uy), (nx, ny), half = frame
    nx, ny = nx * sign, ny * sign
    p0 = (ax + ux * offset + nx * half, ay + uy * offset + ny * half)
    p1 = (p0[0] + ux * width, p0[1] + uy * width)
    return Polygon(
        [
            p0,
            p1,
            (p1[0] + nx * depth, p1[1] + ny * depth),
            (p0[0] + nx * depth, p0[1] + ny * depth),
        ]
    )


def window_zone(graph: WallGraph, window: Opening, rooms: dict[UUID, Polygon]) -> Polygon | None:
    sign = _inside_sign(graph, window.host_wall, window.offset + window.width / 2, rooms)
    return _strip(graph, window.host_wall, window.offset, window.width, WINDOW_STRIP_M, sign)


def radiator_zone(
    graph: WallGraph, radiator: Radiator, rooms: dict[UUID, Polygon]
) -> Polygon | None:
    sign = _inside_sign(graph, radiator.host_wall, radiator.offset + radiator.width / 2, rooms)
    return _strip(
        graph,
        radiator.host_wall,
        radiator.offset,
        radiator.width,
        radiator.depth + radiator.clearance,
        sign,
    )


def own_clearance_zone(piece: Piece) -> Polygon | None:
    """Dégagement que le catalogue demande autour du meuble (devant : portes d'armoire,
    recul de chaise ; côtés : accès au lit ; derrière), hors de son emprise."""
    if not isinstance(piece.element, Furniture):
        return None
    try:
        asset = load_catalog().get(piece.element.asset_id)
    except CatalogError:
        return None
    if asset is None:
        return None
    t, b = piece.element.transform, piece.element.bbox
    w, d = b.width * t.scale / 2.0, b.depth * t.scale / 2.0
    zones: list[Polygon] = []
    c = asset.clearance
    if c.front > 0:
        zones.append(piece.local_rect(-w, w, d, d + c.front))
    if c.back > 0:
        zones.append(piece.local_rect(-w, w, -d - c.back, -d))
    if c.sides > 0:
        zones.append(piece.local_rect(-w - c.sides, -w, -d, d))
        zones.append(piece.local_rect(w, w + c.sides, -d, d))
    if not zones:
        return None
    merged = unary_union(zones)
    return merged if isinstance(merged, Polygon) else merged.convex_hull


def front_zone(piece: Piece) -> Polygon | None:
    """Le seul dégagement avant (portes d'armoire, recul de chaise) : celui qui ne doit pas
    buter sur un mur. Les côtés d'un lit contre un mur, eux, sont légitimes."""
    if not isinstance(piece.element, Furniture):
        return None
    try:
        asset = load_catalog().get(piece.element.asset_id)
    except CatalogError:
        return None
    if asset is None or asset.clearance.front <= 0:
        return None
    t, b = piece.element.transform, piece.element.bbox
    w, d = b.width * t.scale / 2.0, b.depth * t.scale / 2.0
    return piece.local_rect(-w, w, d, d + asset.clearance.front)


def _mode(project: Project, element_id: UUID, kind: ConstraintKind) -> str | None:
    """ "hard", "soft" ou "off" si une contrainte de cette sorte cible l'élément ; None sinon."""
    for constraint in project.constraints.values():
        if constraint.kind is not kind or element_id not in constraint.element_ids:
            continue
        if not constraint.enabled:
            return "off"
        return "hard" if constraint.hard else "soft"
    return None


def _severity(
    project: Project, ids: list[UUID], kind: ConstraintKind, default: str
) -> Severity | None:
    """Sévérité effective d'une règle pour ces éléments ; None si désactivée."""
    modes = [_mode(project, eid, kind) for eid in ids]
    if "off" in modes:
        return None
    mode = next((m for m in modes if m is not None), default)
    return "high" if mode == "hard" else "medium"


def _constraint_mode(project: Project, element_id: UUID) -> str:
    return _mode(project, element_id, ConstraintKind.MAX_HEIGHT_UNDER_SLOPE) or "hard"


def _overlap_area(a: Polygon, b: Polygon) -> float:
    if not a.intersects(b):
        return 0.0
    return float(a.intersection(b).area)


def stair_zones(level: Level) -> list[tuple[Stairs, Polygon, Polygon]]:
    """(escalier, trémie, palier d'arrivée) de chaque escalier visible du niveau (S1)."""
    out: list[tuple[Stairs, Polygon, Polygon]] = []
    for e in level.elements.values():
        if isinstance(e, Stairs) and e.visible:
            lay = stair_layout(e)
            out.append((e, lay.opening, arrival_zone(lay)))
    return out


STAIRS_WALL_EPS_M2 = 0.005
"""Une trémie qui frôle un mur (accrochée à 2 cm) ne le coupe pas."""
STAIRS_ROOM_TOLERANCE_M = 0.05


def _stairs_placement(
    stairs: Stairs, opening: Polygon, label: str, rooms: list[Polygon], walls: Polygon | None
) -> list[Violation]:
    """Trémie sur un mur, ou hors de toute pièce fermée (règles dures, E4)."""
    if walls is not None and _overlap_area(opening, walls) > STAIRS_WALL_EPS_M2:
        return [
            Violation(
                type="stairs_on_wall",
                severity="high",
                element_ids=[stairs.id],
                message=f"La trémie de {label} coupe un mur : tourner ou déplacer l'escalier.",
            )
        ]
    if rooms and not any(r.buffer(STAIRS_ROOM_TOLERANCE_M).contains(opening) for r in rooms):
        return [
            Violation(
                type="stairs_outside_room",
                severity="high",
                element_ids=[stairs.id],
                message=f"La trémie de {label} sort de la pièce : tourner ou déplacer l'escalier.",
            )
        ]
    return []


def _check_stairs(
    project: Project, level: Level, graph: WallGraph, pieces: list[Piece]
) -> list[Violation]:
    """Trémie dans la pièce, hors des murs et libre de tout meuble (règles dures), palier
    d'arrivée dégagé, et échappée d'au moins 1,90 m au-dessus de chaque marche (signalée, sous
    une pente surtout)."""
    zones = stair_zones(level)
    if not zones:
        return []
    out: list[Violation] = []
    slopes = list(derived_roofs(level, graph).values())
    rooms = list(_room_polygons(level, graph).values())
    walls = graph.walls_union() if graph.walls else None
    if walls is not None and walls.is_empty:
        walls = None
    for stairs, opening, arrival in zones:
        label = stairs.name or "l'escalier"
        out.extend(_stairs_placement(stairs, opening, label, rooms, walls))
        for piece in pieces:
            area = _overlap_area(piece.polygon, opening)
            if area > OVERLAP_EPS_M2:
                severity = _severity(project, [piece.element.id], ConstraintKind.NO_OVERLAP, "hard")
                if severity is not None:
                    out.append(
                        Violation(
                            type="furniture_in_stairwell",
                            severity=severity,
                            element_ids=[piece.element.id, stairs.id],
                            message=f"{piece.label} est au-dessus de la trémie de {label} "
                            f"({area * 1e4:.0f} cm²) : à déplacer.",
                        )
                    )
            elif _overlap_area(piece.polygon, arrival) > OVERLAP_EPS_M2:
                out.append(
                    Violation(
                        type="stairs_arrival_blocked",
                        severity="medium",
                        element_ids=[piece.element.id, stairs.id],
                        message=f"{piece.label} encombre l'arrivée de {label} : laisser "
                        f"{metres(ARRIVAL_DEPTH_M)} libres devant la dernière marche.",
                    )
                )
        lay = stair_layout(stairs)

        def ceiling(point: tuple[float, float]) -> float:
            return clearance_at(point, slopes, level) if slopes else level.height

        free = headroom(lay, ceiling)
        if free < HEADROOM_M - 1e-6:
            out.append(
                Violation(
                    type="stairs_headroom",
                    severity="medium",
                    element_ids=[stairs.id],
                    message=f"Échappée de {metres(free)} au-dessus des marches de {label} (au "
                    f"moins {metres(HEADROOM_M)}) : la pente descend trop bas, tourner "
                    "l'escalier vers le faîtage ou le déplacer.",
                )
            )
    return out


def check_layout(project: Project) -> list[Violation]:
    out: list[Violation] = []
    for level in project.levels.values():
        pieces = pieces_of(level)
        graph = WallGraph.from_level(level)
        out.extend(_check_stairs(project, level, graph, pieces))
        out.extend(_check_stacks(level, graph))
        out.extend(_check_trusses(level, graph))
        if not pieces:
            continue
        out.extend(_check_slopes(project, level, graph, pieces))
        out.extend(_check_velux_casings(level, graph, pieces))
        out.extend(_check_bed_headroom(project, level, graph, pieces))
        out.extend(_check_overlaps(project, level, graph, pieces))
        rooms = _room_polygons(level, graph)
        out.extend(_check_openings(project, level, graph, pieces, rooms))
        out.extend(_check_clearances(project, level, graph, pieces, rooms))
        out.extend(_check_passages(project, level, graph, pieces, rooms))
    return out


def _check_trusses(level: Level, graph: WallGraph) -> list[Violation]:
    """KAN-94 : une ferme intermédiaire que le faîtage du moment ne porte plus (toit retouché,
    plan recalculé) reste dans le plan et le dit ; une poutre abaissée sous la pente aussi."""
    out: list[Violation] = []
    for truss in (e for e in level.elements.values() if isinstance(e, Truss) and e.visible):
        name = truss.name or "ferme intermédiaire"
        parts = truss_parts(level, graph, truss)
        if isinstance(parts, str):
            out.append(
                Violation(
                    type="truss_off_ridge",
                    severity="medium",
                    element_ids=[truss.id],
                    message=f"« {name} » : {parts} ; à replacer ou à supprimer.",
                )
            )
        elif parts.lowered:
            out.append(
                Violation(
                    type="truss_lowered",
                    severity="low",
                    element_ids=[truss.id],
                    message=(
                        f"« {name} » : poutre abaissée à {_metres(parts.beam_height)} m, la "
                        "pente passe plus bas que la hauteur demandée."
                    ),
                )
            )
    return out


CASING_TOLERANCE_M = 0.005
"""Un meuble entre dans le caisson d'un velux quand une boule de plus de 5 mm tient dans les
deux à la fois (même mesure que les tests de KAN-28 et KAN-63)."""


@dataclass(frozen=True)
class Casing:
    """Caisson d'un velux pour les règles (KAN-67) : ses planches préparées et son ombre."""

    window_id: UUID
    name: str
    boards: list[NDArray[np.float64]]
    solids: list[Solid]
    shadow: Polygon
    bottom: float

    def depth_in(self, polygon: Polygon, z0: float, top: float) -> float:
        """De combien (rayon, m) le prisme de l'emprise `polygon`, de `z0` à `top`, entre dans
        une planche du caisson ; ≤ 0 s'il n'y touche pas."""
        if top <= self.bottom or not polygon.intersects(self.shadow):
            return -1.0
        return max(s.depth_in(polygon, z0, top) for s in self.solids)


_CASING_CACHE: list[tuple[tuple[object, ...], list[Casing]]] = []
"""Dernier calcul des caissons : la clé garde les objets (pentes, velux, murs, sommets) dont
ils dépendent ; une variante d'aménagement ne change que les meubles, et les retrouve tels quels
(le générateur évalue des milliers de positions sur la même toiture)."""


def velux_casings(level: Level, graph: WallGraph) -> list[Casing]:
    """Les caissons des velux du niveau, tirés de la géométrie du bake (une seule source)."""
    key: tuple[object, ...] = (
        level.height,
        level.elevation,
        level.vertices,
        *(e for e in level.elements.values() if e.type in ("roof_plane", "roof_window", "wall")),
    )
    for cached_key, cached in _CASING_CACHE:
        if len(cached_key) == len(key) and all(
            a is b or (isinstance(a, float) and a == b)
            for a, b in zip(cached_key, key, strict=True)
        ):
            return cached
    casings = _velux_casings(level, graph)
    _CASING_CACHE[:] = [(key, casings)]
    return casings


def _velux_casings(level: Level, graph: WallGraph) -> list[Casing]:
    out: list[Casing] = []
    for window, boards in baked(level, graph):
        solids = [s for s in (Solid.of(b) for b in boards) if s is not None]
        if not solids:
            continue
        shadow = unary_union([s.shadow for s in solids]).convex_hull
        if isinstance(shadow, Polygon):
            bottom = min(s.bottom for s in solids)
            out.append(Casing(window.id, window.name or "velux", boards, solids, shadow, bottom))
    return out


def _metres(value: float) -> str:
    """Cote affichée à l'écran : deux décimales, virgule française (« 0,72 »)."""
    return f"{value:.2f}".replace(".", ",")


def _check_velux_casings(level: Level, graph: WallGraph, pieces: list[Piece]) -> list[Violation]:
    """KAN-67 : un meuble qui entre dans le caisson d'un velux (souple) ; le bake raccourcit
    alors le caisson au-dessus de lui (KAN-63), la règle le dit."""
    casings = velux_casings(level, graph)
    out: list[Violation] = []
    for piece in pieces:
        z0 = piece.element.transform.position.z
        for casing in casings:
            if casing.depth_in(piece.polygon, z0, piece.top) <= CASING_TOLERANCE_M:
                continue
            out.append(
                Violation(
                    type="velux_casing",
                    severity="medium",
                    element_ids=[piece.element.id, casing.window_id],
                    message=(
                        f"{piece.label} passe sous le caisson du « {casing.name} » (bas du "
                        f"caisson à {_metres(casing.bottom)} m, meuble à {_metres(piece.top)} "
                        "m) : à décaler, sinon le caisson s'arrête au-dessus du meuble."
                    ),
                )
            )
    return out


def _check_slopes(
    project: Project, level: Level, graph: WallGraph, pieces: list[Piece]
) -> list[Violation]:
    roofs = list(derived_roofs(level, graph).values())
    if not roofs:
        return []
    out: list[Violation] = []
    for piece in pieces:
        mode = _constraint_mode(project, piece.element.id)
        if mode == "off":
            continue
        lowest = min(clearance_at(c, roofs, level) for c in piece.polygon.exterior.coords[:-1])
        if piece.top > lowest + MARGIN_M:
            out.append(
                Violation(
                    type="element_too_tall_under_slope",
                    severity="high" if mode == "hard" else "medium",
                    element_ids=[piece.element.id],
                    message=(
                        f"{piece.label} monte à {metres(piece.top)} mais la pente ne laisse que "
                        f"{metres(lowest)} à l'aplomb de son emprise : à reculer vers le faîtage "
                        "ou à abaisser."
                    ),
                )
            )
    return out


def bed_headroom_at(
    level: Level, roofs: list[DerivedRoof], x: float, y: float, yaw: float, depth: float
) -> float:
    """Hauteur libre au-dessus du matelas d'un lit encastré, mesurée à l'oreiller (H1)."""
    return headroom_at(roofs, level.height, x, y, yaw, depth)


def _check_bed_headroom(
    project: Project, level: Level, graph: WallGraph, pieces: list[Piece]
) -> list[Violation]:
    """Lit encastré sous pente (H1) : au moins 0,90 m libres au-dessus du matelas, à l'oreiller."""
    beds = [
        p for p in pieces if isinstance(p.element, Furniture) and p.element.category == BUILT_IN_BED
    ]
    if not beds:
        return []
    roofs = list(derived_roofs(level, graph).values())
    if not roofs:
        return []
    out: list[Violation] = []
    for piece in beds:
        mode = _constraint_mode(project, piece.element.id)
        if mode == "off":
            continue
        bed = piece.element
        assert isinstance(bed, Furniture)
        t = bed.transform
        free = bed_headroom_at(
            level, roofs, t.position.x, t.position.y, t.yaw, bed.bbox.depth * t.scale
        )
        if free + 1e-6 < MIN_HEADROOM_M:
            out.append(
                Violation(
                    type="bed_headroom",
                    severity="high" if mode == "hard" else "medium",
                    element_ids=[bed.id],
                    message=(
                        f"{piece.label} : {_metres(free)} m libres au-dessus du matelas à "
                        f"l'oreiller, il en faut {_metres(MIN_HEADROOM_M)} m : à éloigner de la "
                        "jambette."
                    ),
                )
            )
        # KAN-25 : on entre dans le lit par le pied, debout (règle souple).
        standing = standing_at(
            roofs, level.height, t.position.x, t.position.y, t.yaw, bed.bbox.depth * t.scale
        )
        if standing + 1e-6 < STANDING_M:
            out.append(
                Violation(
                    type="bed_access",
                    severity="medium",
                    element_ids=[bed.id],
                    message=(
                        f"{piece.label} : {_metres(standing)} m sous la pente devant le pied du "
                        f"lit, il en faut {_metres(STANDING_M)} m pour s'y tenir debout : à "
                        "tourner le pied vers le faîtage."
                    ),
                )
            )
    return out


def _check_overlaps(
    project: Project, level: Level, graph: WallGraph, pieces: list[Piece]
) -> list[Violation]:
    out: list[Violation] = []
    for a, b in itertools.combinations(pieces, 2):
        area = _overlap_area(a.polygon, b.polygon)
        if area <= OVERLAP_EPS_M2:
            continue
        severity = _severity(
            project, [a.element.id, b.element.id], ConstraintKind.NO_OVERLAP, "hard"
        )
        if severity is None:
            continue
        out.append(
            Violation(
                type="furniture_overlap",
                severity=severity,
                element_ids=[a.element.id, b.element.id],
                message=f"{a.label} chevauche {b.label} ({area * 1e4:.0f} cm²) : à écarter.",
            )
        )
    walls = graph.walls_union() if graph.walls else None
    if walls is None or walls.is_empty:
        return out
    for piece in pieces:
        area = _overlap_area(piece.polygon, walls)
        if area <= OVERLAP_EPS_M2:
            continue
        severity = _severity(project, [piece.element.id], ConstraintKind.NO_OVERLAP, "hard")
        if severity is None:
            continue
        hit = next(
            (
                w.name or "un mur"
                for wid, w in graph.walls.items()
                if _overlap_area(piece.polygon, graph.wall_polygon(wid)) > OVERLAP_EPS_M2
            ),
            "un mur",
        )
        out.append(
            Violation(
                type="furniture_in_wall",
                severity=severity,
                element_ids=[piece.element.id],
                message=f"{piece.label} entre dans {hit} ({area * 1e4:.0f} cm²) : à reculer.",
            )
        )
    return out


def _check_openings(
    project: Project,
    level: Level,
    graph: WallGraph,
    pieces: list[Piece],
    rooms: dict[UUID, Polygon],
) -> list[Violation]:
    out: list[Violation] = []
    for element in level.elements.values():
        if not isinstance(element, Opening) or not element.visible:
            continue
        if element.host_wall not in graph.walls:
            continue
        host = graph.walls[element.host_wall].name or "un mur"
        kind = element.kind
        if kind in SWING_KINDS:
            zone = door_zone(graph, element, rooms)
            if zone is not None:
                out.extend(_door_violations(project, element, host, zone, pieces))
        if kind is OpeningKind.SLIDING_DOOR:
            zone = slide_zone(graph, element)
            if zone is not None:
                out.extend(_slide_violations(project, element, host, zone, pieces))
        if kind in (OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW):
            zone = window_zone(graph, element, rooms)
            if zone is not None:
                out.extend(_window_violations(project, element, host, zone, pieces))
    return out


def _slide_violations(
    project: Project, door: Opening, host: str, zone: Polygon, pieces: list[Piece]
) -> list[Violation]:
    out: list[Violation] = []
    for piece in pieces:
        if _overlap_area(piece.polygon, zone) <= OVERLAP_EPS_M2:
            continue
        severity = _severity(
            project, [piece.element.id, door.id], ConstraintKind.KEEP_CLEAR, "hard"
        )
        if severity is None:
            continue
        out.append(
            Violation(
                type="sliding_zone_blocked",
                severity=severity,
                element_ids=[piece.element.id, door.id],
                message=(
                    f"{piece.label} est sur le passage du vantail de la porte coulissante de "
                    f"{host} : il ne s'ouvre plus, laisser le long du mur "
                    f"{SLIDE_DEPTH_M * 100:.0f} cm libres."
                ),
            )
        )
    return out


def _check_stacks(level: Level, graph: WallGraph) -> list[Violation]:
    """Coulissantes et galandages du niveau : de la place pour le vantail ouvert (E5), avec
    ou sans meubles."""
    out: list[Violation] = []
    for e in level.elements.values():
        if (
            isinstance(e, Opening)
            and e.visible
            and e.kind in STACK_KINDS
            and e.host_wall in graph.walls
        ):
            host = graph.walls[e.host_wall].name or "un mur"
            out.extend(_stack_room(level, graph, e, host))
    return out


def _stack_room(level: Level, graph: WallGraph, door: Opening, host: str) -> list[Violation]:
    """Coulissante ou galandage : le vantail ouvert a besoin d'autant de mur libre que sa
    largeur, du côté où il s'efface (pas de bout de mur, pas d'autre ouverture)."""
    span = stack_span(door)
    if span is None:
        return []
    length = graph.wall_length(door.host_wall)
    blocked = span[0] < -1e-6 or span[1] > length + 1e-6
    for other in level.elements.values():
        if (
            isinstance(other, Opening)
            and other.id != door.id
            and other.visible
            and other.host_wall == door.host_wall
            and other.offset < span[1] - 1e-6
            and other.offset + other.width > span[0] + 1e-6
        ):
            blocked = True
    if not blocked:
        return []
    what = "porte à galandage" if door.kind is OpeningKind.POCKET_DOOR else "porte coulissante"
    side = "gauche" if door.swing is None or door.swing.hinge == "left" else "droite"
    return [
        Violation(
            type="slide_no_room",
            severity="medium",
            element_ids=[door.id],
            message=(
                f"Pas la place pour le vantail ouvert de la {what} de {host} : il faut "
                f"{metres(door.width)} de mur libre à {side} (changer de côté ou déplacer)."
            ),
        )
    ]


def _door_violations(
    project: Project, door: Opening, host: str, zone: Polygon, pieces: list[Piece]
) -> list[Violation]:
    out: list[Violation] = []
    for piece in pieces:
        if _overlap_area(piece.polygon, zone) <= OVERLAP_EPS_M2:
            continue
        severity = _severity(
            project, [piece.element.id, door.id], ConstraintKind.KEEP_CLEAR, "hard"
        )
        if severity is None:
            continue
        out.append(
            Violation(
                type="door_swing_blocked",
                severity=severity,
                element_ids=[piece.element.id, door.id],
                message=(
                    f"{piece.label} est dans le débattement de la porte de {host} "
                    f"({metres(door.width)}) : la porte ne s'ouvre plus."
                ),
            )
        )
    return out


def _window_violations(
    project: Project, window: Opening, host: str, zone: Polygon, pieces: list[Piece]
) -> list[Violation]:
    out: list[Violation] = []
    for piece in pieces:
        if piece.top <= window.sill_height + 0.01:
            continue
        if _overlap_area(piece.polygon, zone) <= OVERLAP_EPS_M2:
            continue
        severity = _severity(
            project, [piece.element.id, window.id], ConstraintKind.KEEP_CLEAR, "soft"
        )
        if severity is None:
            continue
        out.append(
            Violation(
                type="window_blocked",
                severity=severity,
                element_ids=[piece.element.id, window.id],
                message=(
                    f"{piece.label} ({metres(piece.top)}) masque la fenêtre de {host} "
                    f"(allège {metres(window.sill_height)})."
                ),
            )
        )
    return out


def _check_clearances(
    project: Project,
    level: Level,
    graph: WallGraph,
    pieces: list[Piece],
    rooms: dict[UUID, Polygon],
) -> list[Violation]:
    out: list[Violation] = []
    for element in level.elements.values():
        if not isinstance(element, Radiator) or not element.visible:
            continue
        if element.host_wall not in graph.walls:
            continue
        zone = radiator_zone(graph, element, rooms)
        if zone is None:
            continue
        host = graph.walls[element.host_wall].name or "un mur"
        clearance = element.clearance

        def radiator_message(p: Piece, distance: float = clearance, wall: str = host) -> str:
            return f"{p.label} est à moins de {metres(distance)} du radiateur de {wall}."

        out.extend(_zone_violations(project, zone, pieces, element.id, radiator_message))
    walls = graph.walls_union() if graph.walls else None
    for piece in pieces:
        if isinstance(piece.element, Fixture) and piece.element.keep_clear > 0:
            zone = piece.polygon.buffer(piece.element.keep_clear)
            what = f"de {piece.label}"
        else:
            own = own_clearance_zone(piece)
            if own is None:
                continue
            zone = own
            what = f"que demande {piece.label}"
        others = [p for p in pieces if p is not piece and not companions(piece, p)]

        def clearance_message(p: Piece, owner: str = what) -> str:
            return f"{p.label} est dans le dégagement {owner}."

        out.extend(_zone_violations(project, zone, others, piece.element.id, clearance_message))
        front = front_zone(piece)
        if front is None or walls is None or _overlap_area(front, walls) <= OVERLAP_EPS_M2:
            continue
        severity = _severity(project, [piece.element.id], ConstraintKind.KEEP_CLEAR, "soft")
        if severity is not None:
            out.append(
                Violation(
                    type="clearance_blocked",
                    severity=severity,
                    element_ids=[piece.element.id],
                    message=(
                        f"Le dégagement {what} bute sur un mur : le meuble est sans doute "
                        "tourné du mauvais côté."
                    ),
                )
            )
    return out


_PAIRS = (
    ("sofa", "coffee_table"),
    ("sofa_lounge", "coffee_table"),
    ("sofa_corner", "coffee_table"),
    ("armchair", "coffee_table"),
    ("sofa", "floor_lamp"),
    ("armchair", "floor_lamp"),
    ("armchair", "nightstand"),
    ("bed_double", "nightstand"),
    ("bed_single", "nightstand"),
    ("bed_bunk", "nightstand"),
    ("desk", "chair"),
    ("desk_corner", "chair"),
    ("table", "dining_chair"),
    ("round_table", "dining_chair"),
)
COMPANIONS: frozenset[frozenset[str]] = frozenset(frozenset(p) for p in _PAIRS)
"""Meubles qui vont ensemble (V1, gabarits de composition) : l'un est dans le dégagement de
l'autre par construction (table basse devant le canapé, chevet contre le lit, chaise devant
le bureau) ; ce n'est pas une remarque. Les autres règles s'appliquent toujours."""


def companions(a: Piece, b: Piece) -> bool:
    ca = getattr(a.element, "category", None)
    cb = getattr(b.element, "category", None)
    return isinstance(ca, str) and isinstance(cb, str) and frozenset((ca, cb)) in COMPANIONS


def _zone_violations(
    project: Project,
    zone: Polygon,
    pieces: list[Piece],
    owner_id: UUID,
    message: Callable[[Piece], str],
) -> list[Violation]:
    """Un `clearance_blocked` (souple par défaut) par meuble qui entre dans la zone."""
    out: list[Violation] = []
    for piece in pieces:
        if _overlap_area(piece.polygon, zone) <= OVERLAP_EPS_M2:
            continue
        severity = _severity(
            project, [piece.element.id, owner_id], ConstraintKind.KEEP_CLEAR, "soft"
        )
        if severity is None:
            continue
        out.append(
            Violation(
                type="clearance_blocked",
                severity=severity,
                element_ids=[piece.element.id, owner_id],
                message=message(piece),
            )
        )
    return out


def _check_passages(
    project: Project,
    level: Level,
    graph: WallGraph,
    pieces: list[Piece],
    rooms: dict[UUID, Polygon],
) -> list[Violation]:
    """Depuis chaque porte d'une pièce, la zone où un passage de 0,80 m tient (espace libre
    érodé de 0,40 m) doit toucher l'abord de chaque meuble de la pièce."""
    out: list[Violation] = []
    doors = [
        e
        for e in level.elements.values()
        if isinstance(e, Opening) and e.kind in DOOR_KINDS and e.visible
    ]
    radiators = [
        z
        for e in level.elements.values()
        if isinstance(e, Radiator) and e.visible and e.host_wall in graph.walls
        for z in (radiator_zone(graph, e, rooms),)
        if z is not None
    ]
    for room_id, polygon in rooms.items():
        room = level.elements[room_id]
        assert isinstance(room, Room)
        inside = [p for p in pieces if p.polygon.intersects(polygon)]
        if not inside:
            continue
        entries = [
            entry
            for door in doors
            if door.host_wall in room.boundary_walls
            for entry in (_door_entry(graph, door, polygon),)
            if entry is not None
        ]
        if not entries:
            continue
        blockers = unary_union([p.polygon for p in inside] + radiators)
        free = polygon.difference(blockers).buffer(-PASSAGE_WIDTH_M / 2.0 + 0.01)
        components = (
            list(free.geoms)
            if isinstance(free, MultiPolygon)
            else [free]
            if not free.is_empty
            else []
        )
        reachable = unary_union(
            [c for c in components if any(c.distance(e) <= 0.05 for e in entries)]
        )
        for piece in inside:
            severity = _severity(project, [piece.element.id], ConstraintKind.MIN_PASSAGE, "soft")
            if severity is None:
                continue
            access = piece.polygon.buffer(PASSAGE_WIDTH_M / 2.0 + 0.05)
            if not reachable.is_empty and reachable.intersects(access):
                continue
            out.append(
                Violation(
                    type="furniture_unreachable",
                    severity=severity,
                    element_ids=[piece.element.id],
                    message=(
                        f"{piece.label} n'est pas atteignable depuis la porte avec un passage de "
                        f"{metres(PASSAGE_WIDTH_M)} : dégager un chemin."
                    ),
                )
            )
    return out


def _door_entry(graph: WallGraph, door: Opening, room: Polygon) -> Point | None:
    """Point d'entrée dans la pièce : le milieu de la porte, 0,45 m à l'intérieur."""
    frame = _wall_frame(graph, door.host_wall)
    if frame is None:
        return None
    (ax, ay), (ux, uy), (nx, ny), half = frame
    mid = (ax + ux * (door.offset + door.width / 2), ay + uy * (door.offset + door.width / 2))
    for sign in (1.0, -1.0):
        probe = half + 0.45
        point = Point(mid[0] + nx * probe * sign, mid[1] + ny * probe * sign)
        if room.contains(point):
            return point
    return None
