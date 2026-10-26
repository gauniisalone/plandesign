"""Générateur de dispositions (roadmap 3.4) : un brief → N variantes valides, scorées.

Aucune IA ici : un moteur géométrique déterministe (graine) qui place les meubles du brief
dans la pièce, sous les règles de `validation/layout.py`. Placement glouton puis amélioration
locale : les meubles indispensables d'abord, du plus encombrant au plus petit ; positions
candidates le long des murs pour les meubles « dos au mur » (catalogue `anchor`), grille pour
les meubles libres, quatre orientations. Une variante ne contient jamais de violation dure
(collision, mur, débattement de porte, hauteur sous pente) ; les règles souples (fenêtre,
dégagements, passage) et les préférences du brief (`near` / `away_from`) font le score et
la phrase de synthèse de chaque variante. Rien n'entre dans la scène : `apply_layout` est un
geste de l'utilisateur (acteur `user`, meubles `generated`, annulable).
"""

from __future__ import annotations

import math
import random
import re
import time
import unicodedata
from dataclasses import dataclass, field, replace
from functools import cached_property
from pathlib import Path
from uuid import UUID, uuid4

import shapely
from pydantic import Field
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from openplan.geom.built_in_bed import (
    BUILT_IN_BED,
    HEAD_THICKNESS_M,
    MATTRESS_TOP_M,
    MIN_HEADROOM_M,
    PILLOW_FROM_HEAD_M,
    STANDING_M,
    standing_at,
)
from openplan.geom.floor_zones import quad
from openplan.geom.roof import derived_roofs, roof_window_footprint
from openplan.geom.truss import post_shapes
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.compose import DINING_TABLES, TABLE_CHAIRS, Slot, compose
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.zones import USABLE_HEIGHT_M, long_axis, zone_bays, zone_brief
from openplan.scene import history
from openplan.scene.catalog import asset as catalog_asset
from openplan.scene.catalog import furniture_from_asset, load_catalog
from openplan.scene.history import USER_ACTOR
from openplan.schemas.catalog import ROOM_LABELS, CatalogAsset
from openplan.schemas.common import Point2, Point3, Provenance, Source, StrictModel
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import (
    DOOR_KINDS,
    FloorZone,
    Furniture,
    Opening,
    OpeningKind,
    Radiator,
    RoofWindow,
    Room,
    Stairs,
    Swing,
    Wall,
)
from openplan.schemas.intent import DesignIntent
from openplan.schemas.layout import (
    MAX_BRIEF_CONFIDENCE,
    PLACE_OF,
    RULE_LABELS,
    BriefItem,
    CritiqueAction,
    ItemDimensions,
    LayoutBrief,
    Place,
)
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Assumption, StepResult, Violation
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import (
    CASING_TOLERANCE_M,
    OVERLAP_EPS_M2,
    Casing,
    _inside_sign,
    _strip,
    bed_headroom_at,
    check_layout,
    clearance_at,
    door_zone,
    footprint_corners,
    pieces_of,
    radiator_zone,
    slide_zone,
    stair_zones,
    velux_casings,
    window_zone,
)

STEP = "layout.generate"
APPLY_STEP = "layout.apply"
WALL_STEP_M = 0.10
GRID_STEP_M = 0.25
ZONE_EDGE_M = 0.02
"""KAN-92 : demi-largeur de la bande interdite sur le bord d'une zone de sol surélevée."""
WALL_GAP_M = 0.02
"""Jour laissé entre le dos d'un meuble et la face du mur."""
NEAR_M = 1.2
"""« Près de » : centre du meuble à moins de cette distance du repère."""
AWAY_M = 0.8
CORNER_M = 0.3
"""« Dans le coin » : le meuble touche presque un angle de la pièce (à 30 cm près)."""
"""« Loin de » : le meuble ne doit pas être à moins de cette distance du repère."""
DEFAULT_BUDGET_S = 4.0
MAX_SEED_TRIES = 12
DIVERSITY_M = 0.5
"""Deux variantes sont distinctes si un meuble indispensable bouge d'au moins cette distance."""
NOVELTY_PENALTY = 1.0
"""Malus (au choix, pas au score) d'une position déjà prise par une variante précédente."""
BAY_MARGIN_M = 0.6
NIGHT_GAP_M = 0.8
"""Studio (L3b) : passage entre le coin nuit et le coin détente."""
GROUP_STEP_M = 0.25
GROUP_SETBACKS_M = tuple(round(0.2 * k, 2) for k in range(16))
"""Reculs essayés pour un groupe depuis un mur d'égout bas (jusqu'à 3 m)."""
MISSING_COMPANION = 3.0
"""Pénalité par meuble d'accompagnement qui ne tient pas dans le groupe."""
EAVE_SETBACKS_M = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6)
"""Reculs essayés depuis un mur d'égout (sans jambette ou jambette basse)."""
BAY_BONUS = 2.0
WALL_PULL = 1.0
LOW_BONUS = 1.0
"""Pénalité par mètre d'écart au mur d'un meuble de mur."""
NO_ROOM_REASON = "aucune position sans collision, hors des portes et sous la hauteur libre"
ORIENTATIONS = (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)
PLACE_LABELS = PLACE_OF
"""Compléments de « près … » / « loin … » dans les phrases de synthèse."""


class LayoutBody(StrictModel):
    brief: LayoutBrief
    room_id: UUID | None = None
    variants: int = Field(default=3, ge=1, le=6)
    seed: int = Field(default=0, ge=0)
    budget_s: float = Field(default=DEFAULT_BUDGET_S, gt=0.0, le=30.0)


class Unplaced(StrictModel):
    category: str
    label: str
    reason: str


class LayoutProposal(StrictModel):
    index: int
    seed: int
    score: float
    valid: bool
    summary: str
    """Une phrase lisible : ce qui est placé, les souhaits tenus, les remarques souples."""
    furniture: list[Furniture] = Field(default_factory=list)
    """Meubles à ajouter (source `generated`), pas encore dans la scène."""
    unplaced: list[Unplaced] = Field(default_factory=list)
    violations: list[Violation] = Field(default_factory=list)
    """Violations souples de la variante (les dures l'auraient écartée)."""
    satisfied: list[str] = Field(default_factory=list)
    """Préférences du brief tenues, en clair."""
    missed: list[str] = Field(default_factory=list)
    """Préférences du brief non tenues, en clair."""


class LayoutRun(StrictModel):
    run_id: UUID
    level_id: UUID
    room_id: UUID | None
    brief: LayoutBrief
    proposals: list[LayoutProposal] = Field(default_factory=list)
    result: StepResult


class ApplyBody(StrictModel):
    proposal: LayoutProposal


class ReviseBody(StrictModel):
    """Action en un clic d'une remarque (3.5) : la variante révisée revient à relire."""

    proposal: LayoutProposal
    action: CritiqueAction
    brief: LayoutBrief | None = None
    """Brief d'origine : ses préférences comptent dans le score de la variante révisée."""
    room_id: UUID | None = None


# ------------------------------------------------------------------ contexte géométrique
@dataclass
class WallFace:
    wall: Wall
    start: tuple[float, float]
    unit: tuple[float, float]
    inward: tuple[float, float]
    length: float
    eave: bool = False


@dataclass
class Landmarks:
    """Repères de la pièce pour les préférences `near` / `away_from`."""

    windows: list[Polygon] = field(default_factory=list)
    doors: list[Polygon] = field(default_factory=list)
    door_zones: list[Polygon] = field(default_factory=list)
    radiators: list[Polygon] = field(default_factory=list)
    low_zone: Polygon | None = None
    ridge: Polygon | None = None
    walls: Polygon | None = None
    corners: list[Polygon] = field(default_factory=list)
    """Angles de la face intérieure de la pièce (« dans le coin »)."""
    skylights: list[Polygon] = field(default_factory=list)
    """KAN-67 : ombre au sol du caisson de chaque velux (« loin d'un velux »)."""

    def near(self, place: Place, polygon: Polygon) -> bool:
        return self.distance(place, polygon) <= (CORNER_M if place == "corner" else NEAR_M)

    def far(self, place: Place, polygon: Polygon) -> bool:
        return self.distance(place, polygon) > AWAY_M

    def distance(self, place: Place, polygon: Polygon) -> float:
        """Distance (m) du meuble au repère le plus proche ; infini si la pièce n'en a pas."""
        targets: list[Polygon] = []
        if place == "window":
            targets = self.windows
        elif place == "door":
            targets = self.doors
        elif place == "door_swing":
            targets = self.door_zones
        elif place == "radiator":
            targets = self.radiators
        elif place == "wall" and self.walls is not None:
            targets = [self.walls]
        elif place == "low_wall" and self.low_zone is not None:
            targets = [self.low_zone]
        elif place == "ridge" and self.ridge is not None:
            targets = [self.ridge]
        elif place == "corner":
            targets = self.corners
        elif place == "skylight":
            targets = self.skylights
        if not targets:
            return math.inf
        return min(float(polygon.distance(t)) for t in targets)


@dataclass
class Context:
    project: Project
    level: Level
    graph: WallGraph
    room: Room | None
    room_polygon: Polygon
    faces: list[WallFace]
    obstacles: list[Polygon]
    """Emprises déjà occupées : meubles existants, éléments fixes."""
    forbidden: Polygon
    """Zones interdites : murs, débattements de portes, bandes de radiateurs."""
    landmarks: Landmarks
    roofs: list[DerivedRoof]
    catalog_by_category: dict[str, CatalogAsset]
    bays: dict[str, Polygon] = field(default_factory=dict)
    night_gap: float = 0.0
    """Coin nuit dans une pièce de vie (studio, L3b) : écart entre le coin nuit et les autres."""
    inner_faces: list[WallFace] = field(default_factory=list)
    """Faces des cloisons intérieures à la pièce (V1) : les groupes s'y adossent aussi."""
    """Baie de chaque zone du brief (V1) : tranches de la pièce le long du faîtage."""
    casings: list[Casing] = field(default_factory=list)
    """KAN-67 : caissons des velux (volume du bake) ; aucun meuble généré n'y entre."""
    pools: dict[int, tuple[Want, list[Candidate]]] = field(default_factory=dict)
    """KAN-69 : candidats de chaque meuble avec leur emprise, calculés une fois par génération."""
    statics: dict[int, tuple[Want, BriefItem, dict[int, Static]]] = field(default_factory=dict)
    """KAN-74 : pour chaque meuble, ce que vaut chaque candidat sans les meubles posés."""
    spots: dict[
        int, tuple[Want, BriefItem, dict[tuple[float, float, float], tuple[Polygon, Static]]]
    ] = field(default_factory=dict)
    """KAN-74 : de même pour les positions d'ancrage des groupes."""

    @cached_property
    def inner(self) -> Polygon:
        """La pièce élargie d'un millimètre (bords tolérés), préparée une seule fois (KAN-69)."""
        inner = self.room_polygon.buffer(1e-3)
        shapely.prepare(inner)
        return inner

    def fits_height(self, polygon: Polygon, top: float) -> bool:
        if not self.roofs:
            return top <= self.level.height + 1e-6
        corners = list(polygon.exterior.coords)[:-1]
        if not all(clearance_at((x, y), self.roofs, self.level) >= top for x, y in corners):
            return False
        # KAN-67 : ni sous le caisson d'un velux (il garde sa pleine profondeur au rendu).
        return all(c.depth_in(polygon, 0.0, top) <= CASING_TOLERANCE_M for c in self.casings)


def _room_of(level: Level, graph: WallGraph, room_id: UUID | None) -> tuple[Room | None, Polygon]:
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    if room_id is not None:
        chosen = level.elements.get(room_id)
        if not isinstance(chosen, Room):
            msg = f"pièce inconnue : {room_id}"
            raise DetectionError(msg)
        rooms = [chosen]
    best: tuple[Room, Polygon] | None = None
    for room in rooms:
        try:
            polygon = graph.room_polygon(room.id)
        except GeometryError:
            continue
        if polygon.is_empty:
            continue
        if best is None or polygon.area > best[1].area:
            best = (room, polygon)
    if best is None:
        msg = "aucune pièce fermée dans ce niveau : tracer les murs et détecter les pièces d'abord"
        raise DetectionError(msg)
    return best


def _faces(level: Level, graph: WallGraph, room: Room | None, polygon: Polygon) -> list[WallFace]:
    """Faces intérieures des murs de la pièce (tournées vers son centre), avec la longueur du
    tronçon et le drapeau « mur d'égout » (un versant part de ce mur)."""
    wall_ids = list(room.boundary_walls) if room is not None else list(graph.walls)
    roofs = derived_roofs(level, graph)
    eaves = [((r.eave[0].x, r.eave[0].y), (r.eave[1].x, r.eave[1].y)) for r in roofs.values()]
    centroid = polygon.centroid
    out: list[WallFace] = []
    for wid in wall_ids:
        wall = graph.walls.get(wid)
        if wall is None:
            continue
        try:
            ux, uy = graph.wall_direction(wid)
        except GeometryError:
            continue
        a, _ = graph.wall_axis(wid)
        nx, ny = -uy, ux
        half = wall.thickness / 2.0
        mid_along = graph.wall_length(wid) / 2.0
        probe = Point(
            a.x + ux * mid_along + nx * (half + 0.05), a.y + uy * mid_along + ny * (half + 0.05)
        )
        sign = 1.0 if polygon.contains(probe) else -1.0
        if sign < 0 and not polygon.contains(
            Point(
                a.x + ux * mid_along - nx * (half + 0.05), a.y + uy * mid_along - ny * (half + 0.05)
            )
        ):
            # Ni d'un côté ni de l'autre : le mur ne borde pas la pièce au milieu (pièce en L)
            # ; on le garde quand même, côté centre.
            sign = 1.0 if (centroid.x - a.x) * nx + (centroid.y - a.y) * ny > 0 else -1.0
        inward = (nx * sign, ny * sign)
        start = (a.x + inward[0] * half, a.y + inward[1] * half)
        eave = any(
            abs((e0[0] - a.x) * nx + (e0[1] - a.y) * ny) < 0.3
            and abs((e1[0] - a.x) * nx + (e1[1] - a.y) * ny) < 0.3
            for e0, e1 in eaves
        ) or _low_wall(level, list(roofs.values()), start, (ux, uy), inward, mid_along)
        out.append(WallFace(wall, start, (ux, uy), inward, graph.wall_length(wid), eave))
    return out


def _low_wall(
    level: Level,
    roofs: list[DerivedRoof],
    start: tuple[float, float],
    unit: tuple[float, float],
    inward: tuple[float, float],
    along: float,
) -> bool:
    """KAN-74 : un mur bas sous pente, même quand l'égout est à 0 m ou inconnu et passe au-delà
    du mur (vrai grenier : égout 1,5 m dehors, mur de 0,96 m) — d'après la hauteur réelle de la
    pente : elle monte d'au moins HEAD_DROP_M sur le premier mètre vers l'intérieur."""
    if not roofs:
        return False
    x, y = start[0] + unit[0] * along, start[1] + unit[1] * along
    low = clearance_at((x + inward[0] * 0.05, y + inward[1] * 0.05), roofs, level)
    high = clearance_at((x + inward[0] * 1.05, y + inward[1] * 1.05), roofs, level)
    return low < level.height - 0.05 and high - low >= HEAD_DROP_M


def _inner_faces(graph: WallGraph, room: Room | None, polygon: Polygon) -> list[WallFace]:
    """Les deux faces de chaque cloison qui se trouve dans la pièce sans la border."""
    boundary = set(room.boundary_walls) if room is not None else set()
    out: list[WallFace] = []
    for wid, wall in graph.walls.items():
        if wid in boundary or not wall.visible:
            continue
        try:
            ux, uy = graph.wall_direction(wid)
        except GeometryError:
            continue
        a, b = graph.wall_axis(wid)
        mid = Point((a.x + b.x) / 2.0, (a.y + b.y) / 2.0)
        if not polygon.buffer(0.05).contains(mid):
            continue
        half = wall.thickness / 2.0
        for sign in (1.0, -1.0):
            nx, ny = -uy * sign, ux * sign
            start = (a.x + nx * half, a.y + ny * half)
            out.append(WallFace(wall, start, (ux, uy), (nx, ny), graph.wall_length(wid)))
    return out


def _opening_landmarks(
    level: Level, graph: WallGraph, rooms: dict[UUID, Polygon], landmarks: Landmarks
) -> list[Polygon]:
    """Remplit portes, fenêtres, radiateurs des repères ; renvoie les zones interdites."""
    forbidden: list[Polygon] = []
    for element in level.elements.values():
        if not element.visible:
            continue
        if isinstance(element, Opening) and element.host_wall in graph.walls:
            if element.kind in DOOR_KINDS:
                swing = door_zone(graph, element, rooms)
                slide = slide_zone(graph, element)
                if swing is None:
                    # Coulissante, galandage : l'abord de la baie tient lieu de débattement.
                    sign = _inside_sign(
                        graph, element.host_wall, element.offset + element.width / 2, rooms
                    )
                    swing = _strip(
                        graph, element.host_wall, element.offset, element.width, 0.6, sign
                    )
                if swing is not None:
                    landmarks.door_zones.append(swing)
                    landmarks.doors.append(swing)
                    forbidden.append(swing)
                if slide is not None:
                    forbidden.append(slide)
            if element.kind in (OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW) or (
                element.kind is OpeningKind.FRENCH_WINDOW
            ):
                strip = window_zone(graph, element, rooms)
                if strip is not None:
                    landmarks.windows.append(strip)
        elif isinstance(element, Radiator) and element.host_wall in graph.walls:
            zone = radiator_zone(graph, element, rooms)
            if zone is not None:
                landmarks.radiators.append(zone)
                forbidden.append(zone)
    return forbidden


def _roof_landmarks(roofs: list[DerivedRoof], polygon: Polygon, landmarks: Landmarks) -> None:
    """Zone basse sous pente et bande de faîtage (± 0,3 m) comme repères."""
    from openplan.pipeline.derived import low_zone

    zones = [z for z in (low_zone(r) for r in roofs) if z is not None]
    if zones:
        landmarks.low_zone = unary_union(zones).intersection(polygon)
    ridge_bands = []
    for r in roofs:
        a, b = r.eave
        near, far = r.run_m - 0.3, r.run_m + 0.3
        ridge_bands.append(
            Polygon(
                [
                    (a.x + r.inward.x * near, a.y + r.inward.y * near),
                    (b.x + r.inward.x * near, b.y + r.inward.y * near),
                    (b.x + r.inward.x * far, b.y + r.inward.y * far),
                    (a.x + r.inward.x * far, a.y + r.inward.y * far),
                ]
            )
        )
    if ridge_bands:
        landmarks.ridge = unary_union(ridge_bands)


def _skylight_footprints(level: Level, roofs: dict[UUID, DerivedRoof]) -> list[Polygon]:
    out: list[Polygon] = []
    for e in level.elements.values():
        if not isinstance(e, RoofWindow) or not e.visible or e.roof not in roofs:
            continue
        r = roofs[e.roof]
        a, b = r.eave
        corners = roof_window_footprint(
            (a.x, a.y),
            (b.x, b.y),
            (r.inward.x, r.inward.y),
            r.pitch,
            e.offset,
            e.distance,
            e.width,
            e.length,
        )
        if len(corners) >= 3:
            out.append(Polygon(corners))
    return out


def build_context(project: Project, level_id: UUID, room_id: UUID | None) -> Context:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    graph = WallGraph.from_level(level)
    room, polygon = _room_of(level, graph, room_id)
    faces = _faces(level, graph, room, polygon)
    rooms = {room.id: polygon} if room is not None else {}
    landmarks = Landmarks(walls=graph.walls_union() if graph.walls else None)
    if isinstance(polygon, Polygon):
        landmarks.corners = [Point(c).buffer(0.01) for c in list(polygon.exterior.coords)[:-1]]
    forbidden: list[Polygon] = [graph.walls_union()] if graph.walls else []
    forbidden.extend(_opening_landmarks(level, graph, rooms, landmarks))
    by_roof = derived_roofs(level, graph)
    roofs = list(by_roof.values())
    if roofs:
        _roof_landmarks(roofs, polygon, landmarks)
        # Les velux comptent comme lumière (V1) : lecture et bureau s'en approchent aussi.
        landmarks.windows.extend(_skylight_footprints(level, by_roof))
    casings = velux_casings(level, graph) if roofs else []
    landmarks.skylights = [c.shadow for c in casings]
    obstacles = [p.polygon for p in pieces_of(level) if p.polygon.intersects(polygon)]
    # Escaliers (S1) : ni meuble sur la trémie, ni sur le palier d'arrivée.
    forbidden.extend(z for _, opening, arrival in stair_zones(level) for z in (opening, arrival))
    # KAN-92 : un meuble est tout entier sur une zone de sol surélevée ou à côté, jamais à
    # cheval sur la marche.
    forbidden.extend(
        quad(e.polygon).exterior.buffer(ZONE_EDGE_M)
        for e in level.elements.values()
        if isinstance(e, FloorZone) and e.visible and e.elevation > 0.0
    )
    # KAN-94 : aucun meuble sur un poteau, seul ou de ferme.
    forbidden.extend(post_shapes(level, graph))
    catalog = load_catalog()
    by_category: dict[str, CatalogAsset] = {}
    for asset in catalog.assets:
        by_category.setdefault(asset.category, asset)
    forbidden_union = unary_union(forbidden) if forbidden else Polygon()
    # KAN-69 : géométries testées contre chaque candidat, préparées une fois.
    walls = [landmarks.walls] if landmarks.walls is not None else []
    for geometry in (forbidden_union, *walls, *landmarks.windows, *obstacles):
        shapely.prepare(geometry)
    return Context(
        project=project,
        level=level,
        graph=graph,
        room=room,
        room_polygon=polygon,
        inner_faces=_inner_faces(graph, room, polygon),
        faces=faces,
        obstacles=obstacles,
        forbidden=forbidden_union,
        landmarks=landmarks,
        roofs=roofs,
        catalog_by_category=by_category,
        casings=casings,
    )


# ------------------------------------------------------------------ placement
@dataclass
class Want:
    item: BriefItem
    asset: CatalogAsset
    width: float
    depth: float
    height: float
    label: str
    notes: list[str] = field(default_factory=list)
    bay: Polygon | None = None
    """Baie de sa zone (V1) : le meuble y cherche sa place."""

    @property
    def area(self) -> float:
        return self.width * self.depth


@dataclass
class Placed:
    want: Want
    x: float
    y: float
    yaw: float
    polygon: Polygon
    score: float

    @cached_property
    def front(self) -> Polygon:
        """Zone d'usage devant le meuble, calculée une fois (KAN-69)."""
        return _front(self.x, self.y, self.yaw, self.want)

    @cached_property
    def box(self) -> Box4:
        """Boîte englobante de l'emprise, calculée une fois (KAN-74)."""
        return _box(self.polygon)

    @cached_property
    def front_box(self) -> Box4:
        return _box(self.front)


# ------------------------------------------------------------------ usage de la pièce (F5)
_USE_WORDS: tuple[tuple[str, str], ...] = (
    ("salle de bain", "bathroom"),
    ("salle d'eau", "bathroom"),
    ("toilettes", "bathroom"),
    ("wc", "bathroom"),
    ("cuisine", "kitchen"),
    ("chambre", "bedroom"),
    ("salon", "living_room"),
    ("sejour", "living_room"),
    ("bureau", "office"),
)
"""Usage lu dans le nom de la pièce quand son type n'est pas renseigné."""
_DRY = frozenset({"cuisine", "salle_de_bain"})
FORBIDDEN_FAMILIES: dict[str, frozenset[str]] = {
    "bedroom": _DRY,
    "living_room": _DRY,
    "office": _DRY,
    "kitchen": frozenset({"chambre", "salle_de_bain"}),
    "bathroom": frozenset({"chambre", "cuisine", "sejour", "bureau"}),
}
"""Pièces de la bibliothèque (`CatalogAsset.room`) qu'un usage exclut : pas de WC dans une
chambre, pas de lit dans une cuisine. Grenier, couloir, autre : rien d'exclu."""
_ALLOWED_ANYWAY: dict[str, frozenset[str]] = {"kitchen": frozenset({"washer", "dryer"})}
"""Exceptions courantes : le lave-linge et le sèche-linge vont aussi en cuisine."""
USE_LABELS: dict[str, str] = {
    "bedroom": "une chambre",
    "living_room": "un séjour",
    "office": "un bureau",
    "kitchen": "une cuisine",
    "bathroom": "une salle de bain",
}


def _fold_name(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    ).replace("’", "'")


def room_use(room: Room | None) -> str | None:
    """Usage de la pièce : son type s'il est renseigné, sinon lu dans son nom ; None si on ne
    sait pas (rien n'est alors exclu)."""
    if room is None:
        return None
    if room.room_type in FORBIDDEN_FAMILIES:
        return room.room_type
    name = _fold_name(room.name or "")
    for word, use in _USE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", name):
            return use
    return None


def misplaced(asset: CatalogAsset, use: str | None) -> str | None:
    """Pourquoi ce meuble n'a pas sa place dans une pièce de cet usage ; None s'il l'a."""
    if use is None or asset.category in _ALLOWED_ANYWAY.get(use, frozenset()):
        return None
    if asset.room not in FORBIDDEN_FAMILIES.get(use, frozenset()):
        return None
    family = ROOM_LABELS[asset.room].lower()
    return f"meuble de {family}, pas pour {USE_LABELS[use]}"


def expand_brief(brief: LayoutBrief, ctx: Context) -> tuple[list[Want], list[Unplaced]]:
    """Un `Want` par exemplaire demandé, avec l'asset du catalogue et ses dimensions (celles du
    brief si elles tiennent dans la plage, sinon le défaut, dit)."""
    wants: list[Want] = []
    unplaced: list[Unplaced] = []
    use = room_use(ctx.room)
    for item in brief.items:
        asset = ctx.catalog_by_category.get(item.category)
        if asset is None:
            unplaced.append(
                Unplaced(
                    category=item.category,
                    label=item.category,
                    reason="catégorie sans meuble au catalogue",
                )
            )
            continue
        mismatch = misplaced(asset, use)
        # Séjour avec cuisine ouverte (L3) : la zone cuisine admet les meubles de cuisine.
        if item.zone == "cuisine" and asset.room == "cuisine":
            mismatch = None
        if mismatch is not None:
            unplaced.append(Unplaced(category=item.category, label=asset.label, reason=mismatch))
            continue
        notes: list[str] = []
        dims = {"width": None, "depth": None, "height": None}
        if item.dimensions_m is not None:
            for key in dims:
                wanted = getattr(item.dimensions_m, key)
                if wanted is None:
                    continue
                rng = getattr(asset, key)
                if rng.min <= wanted <= rng.max:
                    dims[key] = wanted
                else:
                    notes.append(
                        f"{asset.label} : {key} {metres(wanted)} hors de la plage du catalogue "
                        f"[{decimal(rng.min)} ; {decimal(rng.max)}], "
                        f"défaut {metres(rng.default)} gardé"
                    )
        bbox = asset.bbox(dims["width"], dims["depth"], dims["height"])
        bay = ctx.bays.get(item.zone) if item.zone else None
        for _ in range(item.count):
            wants.append(
                Want(item, asset, bbox.width, bbox.depth, bbox.height, asset.label, notes, bay)
            )
    return wants, unplaced


def _yaw_facing(inward: tuple[float, float]) -> float:
    """Lacet pour que la face avant (+y local) regarde `inward`."""
    return math.atan2(inward[1], inward[0]) - math.pi / 2.0


BED_SETBACK_STEPS = 4
"""KAN-74 : reculs essayés pour un lit encastré au-delà du premier qui donne 0,90 m à l'oreiller."""


def _bed_setbacks(face: WallFace, ctx: Context) -> tuple[float, ...]:
    """Reculs d'un lit encastré depuis un mur bas (KAN-74) : ceux de V1 ; si aucun ne laisse
    0,90 m au-dessus du matelas à l'oreiller (égout à 0 m ou inconnu, loin derrière le mur), le
    premier qui les laisse d'après la hauteur réelle de la pente, et quelques pas au-delà.
    L'oreiller est à WALL_GAP_M + recul + fond + PILLOW_FROM_HEAD_M du mur ; la hauteur est la
    plus basse aux deux bouts et au milieu."""
    need = MATTRESS_TOP_M + MIN_HEADROOM_M
    offset = WALL_GAP_M + HEAD_THICKNESS_M + PILLOW_FROM_HEAD_M
    points = [
        (face.start[0] + face.unit[0] * t, face.start[1] + face.unit[1] * t)
        for t in (0.0, face.length / 2.0, face.length)
    ]

    def fits(setback: float) -> bool:
        d = offset + setback
        return all(
            clearance_at((x + face.inward[0] * d, y + face.inward[1] * d), ctx.roofs, ctx.level)
            >= need - 1e-6
            for x, y in points
        )

    # Quand un recul de V1 suffit, rien ne change (mêmes candidats qu'avant KAN-74).
    if any(fits(s) for s in EAVE_SETBACKS_M):
        return EAVE_SETBACKS_M
    for k in range(61):  # jusqu'à 3 m de recul, par 5 cm
        setback = k * 0.05
        if fits(setback):
            extra = {round(setback + WALL_STEP_M * 2 * j, 3) for j in range(BED_SETBACK_STEPS)}
            return tuple(sorted(set(EAVE_SETBACKS_M) | extra))
    return EAVE_SETBACKS_M


def candidates(want: Want, ctx: Context) -> list[tuple[float, float, float]]:
    """Positions (x, y, lacet) à essayer pour ce meuble."""
    out: list[tuple[float, float, float]] = []
    anchored = want.asset.anchor in ("wall", "eave_wall")
    faces = ctx.faces
    if want.asset.anchor == "eave_wall":
        eave_faces = [f for f in faces if f.eave]
        faces = eave_faces or faces
    if anchored:
        for face in faces:
            yaw = _yaw_facing(face.inward)
            n = max(1, int((face.length - want.width) / WALL_STEP_M) + 1)
            # Mur d'égout (V1) : sans jambette assez haute, le meuble recule dans la bande
            # basse, dos à la pente, jusqu'à tenir sous la hauteur libre.
            setbacks: tuple[float, ...] = EAVE_SETBACKS_M if face.eave else (0.0,)
            if face.eave and want.asset.category == BUILT_IN_BED and ctx.roofs:
                setbacks = _bed_setbacks(face, ctx)
            for setback in setbacks:
                back = want.depth / 2.0 + WALL_GAP_M + setback
                for k in range(n):
                    along = want.width / 2.0 + k * WALL_STEP_M
                    if along + want.width / 2.0 > face.length + 1e-6:
                        break
                    x = face.start[0] + face.unit[0] * along + face.inward[0] * back
                    y = face.start[1] + face.unit[1] * along + face.inward[1] * back
                    out.append((x, y, yaw))
    if not anchored or not out:
        minx, miny, maxx, maxy = ctx.room_polygon.bounds
        x = minx
        while x <= maxx:
            y = miny
            while y <= maxy:
                if ctx.room_polygon.contains(Point(x, y)):
                    out.extend((x, y, yaw) for yaw in ORIENTATIONS)
                y += GRID_STEP_M
            x += GRID_STEP_M
    return out


def _polygon(x: float, y: float, yaw: float, want: Want) -> Polygon:
    return Polygon(footprint_corners(x, y, yaw, want.width, want.depth))


Candidate = tuple[float, float, float, Polygon]
"""Position (x, y, lacet) d'un meuble et son emprise au sol."""


def _pool(want: Want, ctx: Context) -> list[Candidate]:
    """Candidats du meuble et leurs emprises : ils ne dépendent que de ses dimensions et de la
    pièce, donc se calculent une fois par génération, pas à chaque essai (KAN-69)."""
    entry = ctx.pools.get(id(want))
    if entry is None or entry[0] is not want:
        pool = [(x, y, yaw, _polygon(x, y, yaw, want)) for x, y, yaw in candidates(want, ctx)]
        entry = (want, pool)
        ctx.pools[id(want)] = entry
    return entry[1]


def _overlaps(a: Polygon, b: Polygon) -> bool:
    """Les deux emprises se recouvrent de plus de OVERLAP_EPS_M2. `intersects` (préparé si `b`
    l'est) écarte d'abord les cas sans contact : leur aire commune est nulle, inutile de la
    calculer (KAN-69)."""
    if not shapely.intersects(b, a):
        return False
    return bool(a.intersection(b).area > OVERLAP_EPS_M2)


Box4 = tuple[float, float, float, float]
"""Boîte englobante (x0, y0, x1, y1)."""


def _box(polygon: Polygon) -> Box4:
    x0, y0, x1, y1 = polygon.bounds
    return float(x0), float(y0), float(x1), float(y1)


def _apart(a: Box4, b: Box4) -> bool:
    """Boîtes disjointes : les emprises ne peuvent pas se recouvrir (KAN-74, préfiltre exact)."""
    return a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]


def feasible(polygon: Polygon, want: Want, ctx: Context, placed: list[Placed]) -> bool:
    return (
        _fits_room(polygon, want, ctx)
        and _clear_of(polygon, _box(polygon), want, ctx, placed)
        and ctx.fits_height(polygon, want.height)
    )


def _fits_room(polygon: Polygon, want: Want, ctx: Context) -> bool:
    """Ce qui ne dépend pas des meubles posés : dans la pièce, hors des zones interdites et des
    obstacles (la hauteur sous pente est à part, `Context.fits_height`)."""
    if not ctx.inner.contains(polygon):
        return False
    if not ctx.forbidden.is_empty and _overlaps(polygon, ctx.forbidden):
        return False
    return not any(
        not want.asset.walkable and _overlaps(polygon, obstacle) for obstacle in ctx.obstacles
    )


def _clear_of(polygon: Polygon, box: Box4, want: Want, ctx: Context, placed: list[Placed]) -> bool:
    """Aucun recouvrement avec les meubles déjà posés (et l'écart du coin nuit, L3b)."""
    night = want.item.zone == "couchage"
    for other in placed:
        if want.asset.walkable or other.want.asset.walkable:
            continue  # un tapis se pose sous les meubles
        if not _apart(box, other.box) and _overlaps(polygon, other.polygon):
            return False
        # Studio : le coin nuit ne touche pas le coin détente (80 cm entre les zones).
        zone = other.want.item.zone
        if (
            ctx.night_gap
            and want.item.zone
            and zone
            and (zone == "couchage") != night
            and polygon.distance(other.polygon) < ctx.night_gap
        ):
            return False
    return True


def _front(x: float, y: float, yaw: float, want: Want) -> Polygon:
    c, s = math.cos(yaw), math.sin(yaw)
    w, d = want.width / 2.0, want.depth / 2.0
    front = want.asset.clearance.front
    pts = [
        (x + c * dx - s * dy, y + s * dx + c * dy)
        for dx, dy in ((-w, d), (w, d), (w, d + max(front, 0.3)), (-w, d + max(front, 0.3)))
    ]
    return Polygon(pts)


def _preferences(want: Want, polygon: Polygon, lm: Landmarks) -> tuple[float, list[str], list[str]]:
    """Préférences du brief (`near` / `away_from`) : score, tenues, manquées."""
    score = 0.0
    satisfied: list[str] = []
    missed: list[str] = []
    for place in want.item.near:
        if lm.near(place, polygon):
            score += 2.0
            satisfied.append(f"{want.label} près {PLACE_LABELS[place]}")
        else:
            score -= 1.5
            missed.append(f"{want.label} n'est pas près {PLACE_LABELS[place]}")
    for place in want.item.away_from:
        if lm.far(place, polygon):
            score += 1.0
            satisfied.append(f"{want.label} loin {PLACE_LABELS[place]}")
        else:
            score -= 2.5
            missed.append(f"{want.label} est trop près {PLACE_LABELS[place]}")
    return score, satisfied, missed


def score_candidate(
    x: float, y: float, yaw: float, polygon: Polygon, want: Want, ctx: Context, placed: list[Placed]
) -> tuple[float, list[str], list[str]]:
    """Score d'une position : préférences du brief tenues (+), manquées (−), règles souples."""
    parts = _score_parts(x, y, yaw, polygon, want, ctx)
    return parts.total(polygon, placed), parts.satisfied, parts.missed


@dataclass
class ScoreParts:
    """Score d'une position découpé autour de ce qui dépend des meubles posés (KAN-74) : la tête
    (préférences, mur, pièce) et la queue (fenêtres, bureau, meuble libre, zones) ne dépendent que
    de la position ; `total` rejoue les termes dans l'ordre de toujours (mêmes nombres)."""

    head: float
    tail: tuple[float, ...]
    zone: float
    front: Polygon
    box: Box4
    front_box: Box4
    satisfied: list[str]
    missed: list[str]

    def total(self, polygon: Polygon, placed: list[Placed]) -> float:
        score = self.head
        for other in placed:
            if not _apart(self.front_box, other.box) and _overlaps(self.front, other.polygon):
                score -= 2.0
            if not _apart(other.front_box, self.box) and _overlaps(other.front, polygon):
                score -= 2.0
        for term in self.tail:
            score += term
        return score + self.zone


def _score_parts(
    x: float, y: float, yaw: float, polygon: Polygon, want: Want, ctx: Context
) -> ScoreParts:
    lm = ctx.landmarks
    score, satisfied, missed = _preferences(want, polygon, lm)
    front = _front(x, y, yaw, want)
    if lm.walls is not None and _overlaps(front, lm.walls):
        score -= 6.0  # face contre un mur : le meuble ne sert à rien
    if not ctx.inner.contains(front):
        score -= 1.0
    tail: list[float] = []
    for zone in lm.windows:
        if want.height > 0.9 and _overlaps(polygon, zone):
            tail.append(-3.0)
    if want.asset.category == "desk" and lm.windows and lm.near("window", polygon):
        tail.append(1.0)
    if want.asset.anchor == "free" and lm.walls is not None and polygon.distance(lm.walls) > 0.6:
        tail.append(-0.3)
    return ScoreParts(
        score,
        tuple(tail),
        _zone_terms(want, polygon, lm),
        front,
        _box(polygon),
        _box(front),
        satisfied,
        missed,
    )


@dataclass
class Static:
    """Ce qu'un candidat vaut sans les meubles posés, calculé une fois par génération (KAN-74) :
    s'il tient dans la pièce et sous la pente, et les parties fixes de son score."""

    fits: bool
    parts: ScoreParts | None


def _spot(want: Want, ctx: Context, x: float, y: float, yaw: float) -> tuple[Polygon, Static]:
    """Emprise et parties fixes d'une position quelconque (ancrage d'un groupe), en mémoire pour
    la génération, comme les candidats de `_pool` (KAN-74)."""
    entry = ctx.spots.get(id(want))
    if entry is None or entry[0] is not want or entry[1] is not want.item:
        entry = (want, want.item, {})
        ctx.spots[id(want)] = entry
    key = (x, y, yaw)
    found = entry[2].get(key)
    if found is None:
        polygon = _polygon(x, y, yaw, want)
        fits = _fits_room(polygon, want, ctx) and ctx.fits_height(polygon, want.height)
        parts = _score_parts(x, y, yaw, polygon, want, ctx) if fits else None
        found = entry[2][key] = (polygon, Static(fits, parts))
    return found


def _static(want: Want, ctx: Context, index: int, candidate: Candidate) -> Static:
    key = id(want)
    entry = ctx.statics.get(key)
    if entry is None or entry[0] is not want or entry[1] is not want.item:
        entry = (want, want.item, {})
        ctx.statics[key] = entry
    cache = entry[2]
    static = cache.get(index)
    if static is None:
        x, y, yaw, polygon = candidate
        fits = _fits_room(polygon, want, ctx) and ctx.fits_height(polygon, want.height)
        parts = _score_parts(x, y, yaw, polygon, want, ctx) if fits else None
        static = cache[index] = Static(fits, parts)
    return static


def _zone_terms(want: Want, polygon: Polygon, lm: Landmarks) -> float:
    """Termes du brief par zones (V1) : dans sa baie ; *dans* la bande basse sous pente (tête
    sous la pente, pas seulement à côté) ; contre le mur pour un meuble de mur, qui ne recule
    (mur d'égout bas) que s'il le faut."""
    score = 0.0
    if want.bay is not None:
        score += BAY_BONUS if want.bay.contains(polygon.centroid) else -BAY_BONUS
    low = lm.low_zone
    if (
        "low_wall" in want.item.near
        and low is not None
        and polygon.intersection(low).area > OVERLAP_EPS_M2
    ):
        score += LOW_BONUS
    if want.asset.anchor in ("wall", "eave_wall") and lm.walls is not None:
        score -= WALL_PULL * float(polygon.distance(lm.walls))
    return score


Taken = dict[str, list[tuple[float, float]]]
"""Positions (par catégorie) déjà prises par les variantes précédentes."""


def place_all(
    wants: list[Want], ctx: Context, rng: random.Random, taken: Taken | None = None
) -> tuple[list[Placed], list[Unplaced], list[str], list[str]]:
    seen = taken or {}
    placed, loose = _place_groups(wants, ctx, rng)
    fixed = len(placed)
    order = sorted(
        loose, key=lambda w: (0 if w.item.priority == "required" else 1, -w.area, rng.random())
    )
    unplaced: list[Unplaced] = []
    for want in order:
        best = _best_position(want, ctx, placed, rng, seen)
        if best is None:
            unplaced.append(
                Unplaced(category=want.asset.category, label=want.label, reason=NO_ROOM_REASON)
            )
            continue
        placed.append(best)
    # Amélioration locale : chaque meuble hors groupe est replacé au mieux, les autres fixes
    # (deux passes) ; un groupe composé ne se défait pas.
    for _ in range(2):
        for index in range(fixed, len(placed)):
            current = placed[index]
            others = placed[:index] + placed[index + 1 :]
            better = _best_position(current.want, ctx, others, rng, seen)
            if better is not None and better.score > current.score + 1e-9:
                placed[index] = better
    satisfied: list[str] = []
    missed: list[str] = []
    for p in placed:
        _, s, m = score_candidate(
            p.x, p.y, p.yaw, p.polygon, p.want, ctx, [q for q in placed if q is not p]
        )
        satisfied.extend(s)
        missed.extend(m)
    return placed, unplaced, satisfied, missed


def _place_groups(
    wants: list[Want], ctx: Context, rng: random.Random
) -> tuple[list[Placed], list[Want]]:
    """Gabarits de composition (V1) : chaque zone qui en a un est posée d'un bloc (lit et
    chevets, canapé, table basse et fauteuil…). Renvoie les meubles posés et ceux qui restent
    à placer un par un (hors gabarit, ou groupe qui ne tient nulle part)."""
    placed: list[Placed] = []
    loose: list[Want] = []
    by_zone: dict[str, list[Want]] = {}
    for want in wants:
        if want.item.zone:
            by_zone.setdefault(want.item.zone, []).append(want)
        else:
            loose.append(want)
    for zone, members in by_zone.items():
        items = [(w.asset.category, w.width, w.depth) for w in members]
        slots = compose(zone, items)
        found = _place_somewhere(members, slots, ctx, placed, rng)
        if found is None and (compact := compose(zone, items, compact=True)) != slots:
            # Variante serrée (table de repas au mur) quand la composition ample ne tient pas.
            slots, found = compact, _place_somewhere(members, compact, ctx, placed, rng)
        used = {s.index for s in slots}
        table = any(members[s.index].asset.category in DINING_TABLES for s in slots)
        # Une chaise de table hors composition n'est pas posée seule (voir `_place_group`).
        loose.extend(
            w
            for i, w in enumerate(members)
            if i not in used
            and not (found is not None and table and w.asset.category in TABLE_CHAIRS)
        )
        if found is None:
            loose.extend(members[s.index] for s in slots)
        else:
            group, skipped = found
            placed.extend(group)
            loose.extend(skipped)
    return placed, loose


def _place_somewhere(
    members: list[Want], slots: list[Slot], ctx: Context, placed: list[Placed], rng: random.Random
) -> tuple[list[Placed], list[Want]] | None:
    """Le groupe dans sa baie ; si elle est trop étroite (porte-fenêtre, lit voisin), dans
    toute la pièce, la baie restant préférée au score, plutôt que défait meuble par meuble."""
    if not slots:
        return None
    group = [(members[s.index], s) for s in slots]
    found = _place_group(group, ctx, placed, rng)
    if found is None and group[0][0].bay is not None:
        found = _place_group(group, ctx, placed, rng, anywhere=True)
    return found


def _smaller(want: Want) -> list[Want]:
    """Le même meuble en plus petit, par paliers, sans sortir de la plage du catalogue."""
    out: list[Want] = []
    for k in (0.85, 0.7, 0.55):
        width = max(want.asset.width.min, want.width * k)
        depth = max(want.asset.depth.min, want.depth * k)
        if out and abs(width - out[-1].width) < 1e-6 and abs(depth - out[-1].depth) < 1e-6:
            break
        if width < want.width - 1e-6 or depth < want.depth - 1e-6:
            out.append(replace(want, width=width, depth=depth))
    return out


def _anchors(
    ctx: Context, reach: Polygon | None
) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """Repères où accrocher un groupe : points le long de la face intérieure de chaque mur
    (reculés sous une pente basse), direction du mur, normale vers la pièce."""
    out = []
    for face in [*ctx.faces, *ctx.inner_faces]:
        setbacks = GROUP_SETBACKS_M if face.eave else (0.0,)
        n = int(face.length / GROUP_STEP_M) + 1
        for setback in setbacks:
            back = WALL_GAP_M + setback
            for k in range(n):
                along = k * GROUP_STEP_M
                ox = face.start[0] + face.unit[0] * along + face.inward[0] * back
                oy = face.start[1] + face.unit[1] * along + face.inward[1] * back
                if reach is not None and not reach.contains(Point(ox, oy)):
                    continue
                out.append(((ox, oy), face.unit, face.inward))
    return out


def _place_group(
    members: list[tuple[Want, Slot]],
    ctx: Context,
    placed: list[Placed],
    rng: random.Random,
    *,
    anywhere: bool = False,
) -> tuple[list[Placed], list[Want]] | None:
    """Le meilleur emplacement du groupe entier (gabarit de composition) : ses meubles
    principaux faisables à la fois, ceux d'accompagnement quand ils tiennent (sinon placés à
    part) ; le score est la somme des scores, moins une part par accompagnement manquant.
    None si le cœur du groupe ne tient nulle part (dans sa baie, ou partout si `anywhere`)."""
    bay = members[0][0].bay
    reach = bay.buffer(BAY_MARGIN_M) if bay is not None and not anywhere else None
    best: tuple[list[Placed], list[Want]] | None = None
    best_key = -math.inf
    for (ox, oy), (ux, uy), (nx, ny) in _anchors(ctx, reach):
        base = _yaw_facing((nx, ny))
        group: list[Placed] = []
        skipped: list[Want] = []
        total = 0.0
        for want, slot in members:
            # Un meuble d'accompagnement essaie aussi l'autre côté du groupe (en miroir).
            tries = [(want, slot)]
            if slot.optional and slot.dx:
                tries.append((want, Slot(slot.index, -slot.dx, slot.dy, -slot.dyaw, optional=True)))
            if want.asset.walkable:
                # Un tapis trop grand se réduit (plage du catalogue) avant d'être omis.
                tries.extend((smaller, slot) for smaller in _smaller(want))
            spot = None
            for w, t in tries:
                x = ox + ux * t.dx + nx * t.dy
                y = oy + uy * t.dx + ny * t.dy
                yaw = base + t.dyaw
                # KAN-74 : même mémoire que `_best_position` (les ancrages reviennent à chaque
                # essai) ; même réponse que `feasible` puis `score_candidate`.
                polygon, static = _spot(w, ctx, x, y, yaw)
                if (
                    static.parts is not None
                    and _clear_of(polygon, static.parts.box, w, ctx, placed + group)
                    and _bed_fits(w, x, y, yaw, ctx, placed + group)
                ):
                    spot = (x, y, yaw, polygon, w, static.parts)
                    break
            if spot is None:
                if slot.optional:
                    # Un tapis n'a de sens qu'au pied du lit ou sous la table basse, une
                    # chaise qu'autour de sa table : sans place dans la composition, ils sont
                    # omis plutôt que posés seuls.
                    if not want.asset.walkable and want.asset.category not in TABLE_CHAIRS:
                        skipped.append(want)
                    total -= MISSING_COMPANION
                    continue
                break
            x, y, yaw, polygon, chosen, parts = spot
            score = parts.total(polygon, placed + group)
            if chosen.asset.category == BUILT_IN_BED and ctx.roofs:
                score += _head_under_slope(x, y, yaw, chosen, ctx)
            group.append(Placed(chosen, x, y, yaw, polygon, score))
            total += score
        else:
            key = total + rng.random() * 1e-6
            if key > best_key:
                best_key, best = key, (group, skipped)
    return best


def _best_position(
    want: Want, ctx: Context, placed: list[Placed], rng: random.Random, taken: Taken
) -> Placed | None:
    """Meilleure position faisable. Le choix (pas le score) pénalise les positions déjà
    prises par une variante précédente, pour que les variantes soient vraiment distinctes."""
    best: Placed | None = None
    best_key = (-math.inf, 0.0)
    previous = taken.get(want.asset.category, [])
    pool = _pool(want, ctx)
    passes = [list(range(len(pool)))]
    if want.bay is not None:
        # Brief par zones (V1) : d'abord dans la baie de la zone (et un peu autour), ce qui
        # regroupe la zone et borne le calcul ; ailleurs seulement si rien n'y tient.
        reach = want.bay.buffer(BAY_MARGIN_M)
        inside = [reach.contains(Point(c[0], c[1])) for c in pool]
        passes = [
            [i for i, ok in enumerate(inside) if ok],
            [i for i, ok in enumerate(inside) if not ok],
        ]
    for batch in passes:
        for index in batch:
            x, y, yaw, polygon = candidate = pool[index]
            # KAN-74 : la pièce, la pente et les parties fixes du score sont en mémoire ;
            # seuls les meubles posés se recalculent (mêmes réponses que `feasible` et
            # `score_candidate`).
            static = _static(want, ctx, index, candidate)
            if static.parts is None:
                continue
            if not _clear_of(polygon, static.parts.box, want, ctx, placed):
                continue
            # Lit encastré (H1) : 0,90 m libres au-dessus du matelas, à l'oreiller.
            if not _bed_fits(want, x, y, yaw, ctx, placed):
                continue
            score = static.parts.total(polygon, placed)
            if want.asset.category == BUILT_IN_BED and ctx.roofs:
                score += _head_under_slope(x, y, yaw, want, ctx)
            novelty = (
                NOVELTY_PENALTY
                if any(math.hypot(x - px, y - py) < DIVERSITY_M for px, py in previous)
                else 0.0
            )
            key = (score - novelty, rng.random())
            if key > best_key:
                best_key = key
                best = Placed(want, x, y, yaw, polygon, score)
        if best is not None:
            break
    return best


# ------------------------------------------------------------------ variantes
ZONE_KEY = "zone"
"""Zone du brief (couchage, lecture…) gardée dans le meuble posé : la vue de zone (V1) la lit."""


def _furniture(placed: Placed, provenance: Provenance) -> Furniture:
    furniture = furniture_from_asset(
        placed.want.asset,
        Point2(x=placed.x, y=placed.y),
        placed.yaw,
        width=placed.want.width,
        depth=placed.want.depth,
        height=placed.want.height,
        source=Source.GENERATED,
        confidence=MAX_BRIEF_CONFIDENCE,
        provenance=provenance,
    )
    if placed.want.item.zone:
        furniture = furniture.model_copy(
            update={"metadata": {**furniture.metadata, ZONE_KEY: placed.want.item.zone}}
        )
    return furniture


def _with_furniture(project: Project, level: Level, furniture: list[Furniture]) -> Project:
    new_level = level.model_copy(
        update={"elements": {**level.elements, **{f.id: f for f in furniture}}}
    )
    return project.model_copy(update={"levels": {**project.levels, level.id: new_level}})


def _positions(proposal: LayoutProposal, categories: set[str]) -> list[tuple[str, float, float]]:
    return sorted(
        (f.category, f.transform.position.x, f.transform.position.y)
        for f in proposal.furniture
        if not categories or f.category in categories
    )


def _distinct(a: LayoutProposal, b: LayoutProposal, categories: set[str]) -> bool:
    """Distinctes si un meuble indispensable (ou n'importe lequel sans indispensable) bouge
    d'au moins `DIVERSITY_M`."""
    pa, pb = _positions(a, categories), _positions(b, categories)
    if len(pa) != len(pb):
        return True
    return any(
        math.hypot(p[1] - q[1], p[2] - q[2]) >= DIVERSITY_M for p, q in zip(pa, pb, strict=True)
    )


def _taken(proposals: list[LayoutProposal]) -> Taken:
    taken: Taken = {}
    for proposal in proposals:
        for f in proposal.furniture:
            taken.setdefault(f.category, []).append(
                (f.transform.position.x, f.transform.position.y)
            )
    return taken


def _n(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {plural or singular + 's'}" if count > 1 else f"{count} {singular}"


def _summary(
    proposal_furniture: list[Furniture],
    unplaced: list[Unplaced],
    soft: list[Violation],
    satisfied: list[str],
    missed: list[str],
    score: float,
) -> str:
    parts = [_n(len(proposal_furniture), "meuble placé", "meubles placés")]
    if unplaced:
        names = ", ".join(u.label for u in unplaced)
        parts.append(f"{_n(len(unplaced), 'non placé')} ({names})")
    if satisfied:
        parts.append(_n(len(satisfied), "souhait tenu", "souhaits tenus"))
    if missed:
        parts.append(f"{_n(len(missed), 'souhait manqué', 'souhaits manqués')} : {missed[0]}")
    if soft:
        parts.append(f"{_n(len(soft), 'remarque')} : {soft[0].message.rstrip('.')}")
    else:
        parts.append("aucune remarque")
    return f"{verdict(unplaced, soft, missed)} · " + " · ".join(parts) + "."


VERDICTS = ("Très bien", "Correct", "À revoir")
"""Verdict lisible d'une variante (le score chiffré reste dans `LayoutProposal.score`)."""


def verdict(unplaced: list[Unplaced], soft: list[Violation], missed: list[str]) -> str:
    """« Très bien » : tout est placé, sans remarque ni souhait manqué ; « Correct » : tout
    est placé avec au plus une remarque ou un souhait manqué ; sinon « À revoir »."""
    if not unplaced and not soft and not missed:
        return VERDICTS[0]
    if not unplaced and len(soft) + len(missed) <= 1:
        return VERDICTS[1]
    return VERDICTS[2]


def generate_layouts(
    project: Project,
    level_id: UUID,
    body: LayoutBody,
    *,
    run_id: UUID | None = None,
) -> LayoutRun:
    """Dispositions du brief ; studio (L3b) : si le coin nuit ne tient pas à 80 cm du coin
    détente, une seconde passe le pose sans cet écart."""
    run, gap_used = _generate_layouts(project, level_id, body, run_id=run_id, night_gap=True)
    has_bed = any(f.category.startswith("bed") for p in run.proposals for f in p.furniture)
    wants_bed = any(i.zone == "couchage" for i in body.brief.items)
    if wants_bed and not has_bed and gap_used:
        run, _ = _generate_layouts(project, level_id, body, run_id=run_id, night_gap=False)
    # Combles (H1) : si aucun lit encastré ne tient sous la pente (bande basse trop étroite
    # ou trop basse), le lit classique et ses chevets prennent sa place.
    builtin = [i for i in body.brief.items if i.category == BUILT_IN_BED]
    placed_builtin = any(f.category == BUILT_IN_BED for p in run.proposals[:1] for f in p.furniture)
    if builtin and not placed_builtin:
        classic = classic_beds(body.brief)
        run, _ = _generate_layouts(
            project,
            level_id,
            body.model_copy(update={"brief": classic}),
            run_id=run_id,
            night_gap=True,
        )
    return run


BED_GAP_M = 0.4
"""Deux lits encastrés côte à côte (H5) : 40 cm de passage au moins entre eux."""


def _bed_fits(
    want: Want,
    x: float,
    y: float,
    yaw: float,
    ctx: Context,
    placed: list[Placed] | None = None,
) -> bool:
    """Lit encastré (H1, H5, KAN-25) : 0,90 m libres au-dessus du matelas, à l'oreiller, 1,80 m
    pour se tenir debout devant le pied, et 40 cm au moins jusqu'au lit encastré voisin ; tout
    autre meuble passe."""
    if want.asset.category != BUILT_IN_BED:
        return True
    if ctx.roofs and bed_headroom_at(ctx.level, ctx.roofs, x, y, yaw, want.depth) < (
        MIN_HEADROOM_M - 1e-6
    ):
        return False
    if ctx.roofs and not _head_is_lower(x, y, yaw, want, ctx):
        # H5 : un lit encastré a la tête sous le rampant (décision PO), jamais le long de la pente.
        return False
    if ctx.roofs and standing_at(ctx.roofs, ctx.level.height, x, y, yaw, want.depth) < (
        STANDING_M - 1e-6
    ):
        # KAN-25 : la niche ferme les grands côtés, on entre par le pied, debout.
        return False
    polygon = _polygon(x, y, yaw, want)
    return not any(
        p.want.asset.category == BUILT_IN_BED and polygon.distance(p.polygon) < BED_GAP_M - 1e-6
        for p in placed or []
    )


HEAD_UNDER_SLOPE_BONUS = 3.0
"""Lit encastré (H1) : la tête sous le rampant, le pied vers le faîtage, comme dans les
combles de référence (« tête sous le rampant », décision PO du 9 octobre)."""


HEAD_DROP_M = 0.3
"""Tête sous le rampant : la pente est au moins 30 cm plus basse à la tête qu'au pied."""


def _head_is_lower(x: float, y: float, yaw: float, want: Want, ctx: Context) -> bool:
    fx, fy = -math.sin(yaw), math.cos(yaw)
    half = want.depth / 2.0 - 0.1
    head = clearance_at((x - fx * half, y - fy * half), ctx.roofs, ctx.level)
    foot = clearance_at((x + fx * half, y + fy * half), ctx.roofs, ctx.level)
    return foot - head >= HEAD_DROP_M


def _head_under_slope(x: float, y: float, yaw: float, want: Want, ctx: Context) -> float:
    """Bonus quand la tête du lit est nettement plus basse que son pied sous la pente."""
    fx, fy = -math.sin(yaw), math.cos(yaw)
    half = want.depth / 2.0 - 0.1
    head = clearance_at((x - fx * half, y - fy * half), ctx.roofs, ctx.level)
    foot = clearance_at((x + fx * half, y + fy * half), ctx.roofs, ctx.level)
    return HEAD_UNDER_SLOPE_BONUS if foot - head > 0.5 else 0.0


def classic_beds(brief: LayoutBrief) -> LayoutBrief:
    """Le brief avec un lit double et deux chevets à la place d'un lit encastré, ou autant de lits
    simples (un chevet chacun) à la place d'une rangée de lits encastrés (H5)."""
    items: list[BriefItem] = []
    for item in brief.items:
        if item.category != BUILT_IN_BED:
            items.append(item)
            continue
        if item.count == 1:
            items.append(item.model_copy(update={"category": "bed_double", "dimensions_m": None}))
            items.append(
                item.model_copy(update={"category": "nightstand", "count": 2, "dimensions_m": None})
            )
        else:
            items.append(item.model_copy(update={"category": "bed_single", "dimensions_m": None}))
            items.append(item.model_copy(update={"category": "nightstand", "dimensions_m": None}))
    return brief.model_copy(update={"items": items})


def _generate_layouts(
    project: Project,
    level_id: UUID,
    body: LayoutBody,
    *,
    run_id: UUID | None,
    night_gap: bool,
) -> tuple[LayoutRun, bool]:
    started = time.perf_counter()
    rid = run_id or uuid4()
    ctx = build_context(project, level_id, body.room_id)
    zones = list(dict.fromkeys(i.zone for i in body.brief.items if i.zone))
    if zones:
        axis = layout_axis(ctx)
        ctx.bays = zone_bays(ctx.room_polygon, zones, axis)
        if (
            night_gap
            and "couchage" in zones
            and len(zones) > 1
            and room_use(ctx.room) == "living_room"
        ):
            # Coin nuit d'une pièce de vie (studio, L3b) : au fond, à l'opposé de l'entrée, et à
            # 80 cm des autres coins.
            ctx.night_gap = NIGHT_GAP_M
            doors = unary_union(ctx.landmarks.doors) if ctx.landmarks.doors else None
            if doors is not None and zones[0] == "couchage":
                last = ctx.bays.get(zones[-1])
                night = ctx.bays.get("couchage")
                if (
                    night is not None
                    and last is not None
                    and night.distance(doors) < last.distance(doors)
                ):
                    ctx.bays = zone_bays(ctx.room_polygon, zones, (-axis[0], -axis[1]))
    wants, unplaceable = expand_brief(body.brief, ctx)
    provenance = Provenance(step=STEP, run_id=rid)
    proposals: list[LayoutProposal] = []
    warnings: list[str] = [n for w in wants for n in w.notes]
    warnings = list(dict.fromkeys(warnings))
    seed = body.seed
    tries = 0
    required = {i.category for i in body.brief.required}
    while len(proposals) < body.variants and tries < MAX_SEED_TRIES:
        if time.perf_counter() - started > body.budget_s and proposals:
            warnings.append(
                f"Budget de {body.budget_s:.0f} s atteint : {len(proposals)} variante(s) sur "
                f"{body.variants} demandée(s)."
            )
            break
        rng = random.Random(seed + tries)
        placed, unplaced, satisfied, missed = place_all(wants, ctx, rng, _taken(proposals))
        furniture = [_furniture(p, provenance) for p in placed]
        trial = _with_furniture(project, ctx.level, furniture)
        violations = [
            v for v in check_layout(trial) if any(f.id in v.element_ids for f in furniture)
        ]
        hard = [v for v in violations if v.severity == "high"]
        soft = [v for v in violations if v.severity != "high"]
        score = sum(p.score for p in placed) - 2.0 * len(soft) - 5.0 * len(unplaced + unplaceable)
        candidate = LayoutProposal(
            index=len(proposals) + 1,
            seed=seed + tries,
            score=round(score, 2),
            valid=not hard,
            summary=_summary(furniture, unplaced + unplaceable, soft, satisfied, missed, score),
            furniture=furniture,
            unplaced=unplaced + unplaceable,
            violations=soft,
            satisfied=satisfied,
            missed=missed,
        )
        tries += 1
        if hard or not furniture:
            continue
        if any(not _distinct(candidate, existing, required) for existing in proposals):
            continue
        proposals.append(candidate)
    proposals.sort(key=lambda p: -p.score)
    for index, proposal in enumerate(proposals, start=1):
        proposal.index = index
    if not proposals:
        warnings.append(
            "Aucune variante valide : la pièce est trop petite pour ce brief, ou les meubles "
            "indispensables ne tiennent pas hors des portes et sous les pentes."
        )
    elif len(proposals) < body.variants:
        warnings.append(
            f"{len(proposals)} variante(s) distincte(s) trouvée(s) sur {body.variants} demandée(s)."
        )
    run = LayoutRun(
        run_id=rid,
        level_id=level_id,
        room_id=ctx.room.id if ctx.room else None,
        brief=body.brief,
        proposals=proposals,
        result=StepResult(
            step=STEP,
            confidence=MAX_BRIEF_CONFIDENCE if proposals else 0.0,
            assumptions=[
                Assumption(
                    text="Dispositions calculées par un moteur géométrique à partir du brief et du "
                    "plan : les règles dures sont respectées, les souhaits et les règles souples "
                    "font le score. Rien n'est appliqué sans votre choix.",
                    impact="medium",
                )
            ],
            warnings=warnings,
            duration_s=time.perf_counter() - started,
        ),
    )
    return run, ctx.night_gap > 0


def layout_axis(ctx: Context) -> tuple[float, float]:
    """Axe des baies : le faîtage (le long de l'égout) sous pente, sinon le grand axe."""
    if ctx.roofs:
        a, b = ctx.roofs[0].eave
        return (b.x - a.x, b.y - a.y)
    return long_axis(ctx.room_polygon)


class ZoneBriefBody(StrictModel):
    room_id: UUID | None = None


def brief_from_intent(project: Project, level_id: UUID, body: ZoneBriefBody) -> LayoutBrief:
    """Brief par zones (V1) d'après l'intention de la pièce (déduite si absente) : zones,
    kits, densité cible ; rien n'est placé."""
    ctx = build_context(project, level_id, body.room_id)
    room = ctx.room
    intent = (room.intent if room else None) or DesignIntent(
        room_type=room.room_type if room else None
    )
    room_type = intent.room_type or (room.room_type if room else None)
    brief = zone_brief(
        intent,
        room_type,
        ctx.room_polygon.area,
        sloped=bool(ctx.roofs),
        usable_area=_usable_area(ctx),
    )
    wished = {w.category for w in intent.furniture}
    if ctx.roofs and not wished & {BUILT_IN_BED, *BED_CATEGORIES}:
        brief = _dense_built_in_beds(brief, ctx)
    return brief


BED_CATEGORIES = ("bed_double", "bed_single", "bed_bunk")
EAVE_PER_BED_M = 3.0
"""Combles (H5) : un lit encastré pour 3 m de jambette, 2 à 4 par grenier (références 12 et 14)."""


def built_in_bed_count(ctx: Context) -> int:
    """Combien de lits encastrés la bande basse permet : la longueur cumulée des égouts, à 3 m
    par lit ; 1 seul sous 4 m de jambette, 4 au plus."""
    eaves = sum(
        math.dist((r.eave[0].x, r.eave[0].y), (r.eave[1].x, r.eave[1].y)) for r in ctx.roofs
    )
    if eaves < 4.0:
        return 1
    return max(2, min(4, int(eaves / EAVE_PER_BED_M)))


def _dense_built_in_beds(brief: LayoutBrief, ctx: Context) -> LayoutBrief:
    """Densité des combles (H5) : le coin nuit devient une rangée de lits encastrés de 90, tête
    vers la jambette, comme dans les références ; un seul lit garde son 140."""
    count = built_in_bed_count(ctx)
    if count <= 1:
        return brief
    items = [
        item.model_copy(update={"count": count, "dimensions_m": ItemDimensions(width=0.9)})
        if item.category == BUILT_IN_BED
        else item
        for item in brief.items
    ]
    return brief.model_copy(update={"items": items})


def _usable_area(ctx: Context, height: float = USABLE_HEIGHT_M, step: float = 0.25) -> float:
    """Surface de la pièce à plus de `height` sous les pentes (échantillonnage tous les
    25 cm) ; toute la surface sans pente."""
    polygon = ctx.room_polygon
    if not ctx.roofs:
        return float(polygon.area)
    minx, miny, maxx, maxy = polygon.bounds
    count = 0
    y = miny + step / 2.0
    while y < maxy:
        x = minx + step / 2.0
        while x < maxx:
            if (
                polygon.contains(Point(x, y))
                and clearance_at((x, y), ctx.roofs, ctx.level) >= height
            ):
                count += 1
            x += step
        y += step
    return count * step * step


def apply_layout(
    project: Project, level_id: UUID, proposal: LayoutProposal, *, actor: str = USER_ACTOR
) -> Project:
    """« Appliquer cette variante » : les meubles entrent dans la scène en source `generated`,
    par un geste de l'utilisateur (acteur `user`, ou `user:<nom>` par jeton), donc annulable."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if not proposal.furniture:
        msg = "cette variante ne contient aucun meuble"
        raise DetectionError(msg)
    fresh = [
        f.model_copy(update={"id": uuid4()}) if f.id in level.elements else f
        for f in proposal.furniture
    ]
    after = _with_furniture(project, level, fresh)
    return history.commit(
        project,
        after,
        actor=actor,
        label=f"Appliquer la variante {proposal.index} ({len(fresh)} meubles)",
    )


# ------------------------------------------------------------------ relecture et révision (3.5)
@dataclass
class Assessment:
    """Une liste de meubles jugée dans la pièce : scores, règles, souhaits tenus / manqués."""

    placed: list[Placed]
    violations: list[Violation]
    """Toutes les violations (dures et souples) qui touchent ces meubles."""
    misses: list[tuple[Furniture, str, Place]]
    """Souhaits du brief non tenus, par meuble : (meuble, "near" | "away", repère)."""
    satisfied: list[str]
    missed: list[str]
    score: float

    @property
    def soft(self) -> list[Violation]:
        return [v for v in self.violations if v.severity != "high"]

    @property
    def hard(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "high"]


def want_for(furniture: Furniture, brief: LayoutBrief | None, ctx: Context) -> Want:
    """Le `Want` d'un meuble déjà placé : asset du catalogue, dimensions du meuble, préférences
    de la ligne du brief de même catégorie (aucune si le brief ne la contient pas)."""
    asset = load_catalog().get(furniture.asset_id) or ctx.catalog_by_category.get(
        furniture.category
    )
    if asset is None:
        msg = f"meuble hors catalogue : {furniture.name or furniture.category}"
        raise DetectionError(msg)
    items = brief.items if brief is not None else []
    item = next((i for i in items if i.category == furniture.category), None) or BriefItem(
        category=furniture.category
    )
    return Want(
        item,
        asset,
        furniture.bbox.width,
        furniture.bbox.depth,
        furniture.bbox.height,
        furniture.name or asset.label,
    )


def _placed_for(furniture: Furniture, want: Want) -> Placed:
    x, y = furniture.transform.position.x, furniture.transform.position.y
    yaw = furniture.transform.yaw
    return Placed(want, x, y, yaw, _polygon(x, y, yaw, want), 0.0)


def assess(
    project: Project, ctx: Context, furniture: list[Furniture], brief: LayoutBrief | None
) -> Assessment:
    """Juge des meubles tels quels (une variante, révisée ou non) : même score que le
    générateur, violations recalculées dans la scène d'essai, souhaits manqués par meuble."""
    placed = [_placed_for(f, want_for(f, brief, ctx)) for f in furniture]
    satisfied: list[str] = []
    missed: list[str] = []
    misses: list[tuple[Furniture, str, Place]] = []
    for index, current in enumerate(placed):
        others = placed[:index] + placed[index + 1 :]
        score, s, m = score_candidate(
            current.x, current.y, current.yaw, current.polygon, current.want, ctx, others
        )
        current.score = score
        satisfied.extend(s)
        missed.extend(m)
        for place in current.want.item.near:
            if not ctx.landmarks.near(place, current.polygon):
                misses.append((furniture[index], "near", place))
        for place in current.want.item.away_from:
            if not ctx.landmarks.far(place, current.polygon):
                misses.append((furniture[index], "away", place))
    trial = _with_furniture(project, ctx.level, furniture)
    ids = {f.id for f in furniture}
    violations = [v for v in check_layout(trial) if ids & set(v.element_ids)]
    soft = [v for v in violations if v.severity != "high"]
    score = sum(p.score for p in placed) - 2.0 * len(soft)
    return Assessment(placed, violations, misses, satisfied, missed, score)


def _moved(
    target: Furniture,
    action: CritiqueAction,
    others: list[Furniture],
    body: ReviseBody,
    ctx: Context,
) -> Furniture:
    """Replace un meuble au mieux avec la préférence ajoutée, les autres fixes."""
    place = action.target
    if place is None:
        msg = "repère manquant pour un déplacement"
        raise DetectionError(msg)
    want = want_for(target, body.brief, ctx)
    near = [p for p in want.item.near if p != place]
    away = [p for p in want.item.away_from if p != place]
    if action.kind == "move_near":
        near.append(place)
    else:
        away.append(place)
    want.item = want.item.model_copy(update={"near": near, "away_from": away})
    fixed = [_placed_for(f, want_for(f, body.brief, ctx)) for f in others]
    best = _best_position(want, ctx, fixed, random.Random(body.proposal.seed), {})
    verb = f"{'près' if action.kind == 'move_near' else 'loin'} {PLACE_OF[place]}"
    ok = best is not None and (
        ctx.landmarks.near(place, best.polygon)
        if action.kind == "move_near"
        else ctx.landmarks.far(place, best.polygon)
    )
    if best is None or not ok:
        msg = (
            f"aucune position de {want.label} {verb} sans collision, hors des portes et sous la "
            "hauteur libre : la variante reste inchangée"
        )
        raise DetectionError(msg)
    return _furniture(best, target.provenance).model_copy(
        update={"id": target.id, "name": target.name}
    )


def revise_proposal(project: Project, level_id: UUID, body: ReviseBody) -> LayoutProposal:
    """Une action de remarque → une variante révisée, à relire (rien n'entre dans la scène) :
    « retirer » enlève le meuble ; « déplacer près / loin de » replace ce seul meuble au mieux
    avec la préférence ajoutée, les autres fixes ; score, phrase et remarques recalculés."""
    ctx = build_context(project, level_id, body.room_id)
    proposal, action = body.proposal, body.action
    target = next((f for f in proposal.furniture if f.id == action.furniture_id), None)
    if target is None:
        msg = "ce meuble n'est pas dans la variante"
        raise DetectionError(msg)
    others = [f for f in proposal.furniture if f.id != target.id]
    if action.kind == "remove":
        furniture = others
    else:
        moved = _moved(target, action, others, body, ctx)
        furniture = [moved if f.id == target.id else f for f in proposal.furniture]
    if not furniture:
        msg = "la variante serait vide : retirer plutôt la variante entière"
        raise DetectionError(msg)
    judged = assess(project, ctx, furniture, body.brief)
    score = judged.score - 5.0 * len(proposal.unplaced)
    return LayoutProposal(
        index=proposal.index,
        seed=proposal.seed,
        score=round(score, 2),
        valid=not judged.hard,
        summary=_summary(
            furniture, proposal.unplaced, judged.soft, judged.satisfied, judged.missed, score
        ),
        furniture=furniture,
        unplaced=proposal.unplaced,
        violations=judged.soft,
        satisfied=judged.satisfied,
        missed=judged.missed,
    )


# ------------------------------------------------------------------ déplacement en direct (3.6)
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
OUTSIDE_ROOM = "furniture_outside_room"


class FurnitureMoveBody(StrictModel):
    """Position et lacet essayés pendant un glisser dans le plan ; largeur et profondeur
    (mètres, à l'échelle du meuble) essayées pendant un redimensionnement (E2)."""

    position: Point2
    yaw: float = 0.0
    width: float | None = Field(default=None, gt=0.0)
    depth: float | None = Field(default=None, gt=0.0)


class FurniturePlaceBody(StrictModel):
    """Meuble du catalogue pas encore posé, essayé à cette position (bibliothèque, F3)."""

    asset_id: str
    position: Point2
    yaw: float = 0.0


class FurnitureMoveCheck(StrictModel):
    valid: bool
    violations: list[Violation] = Field(default_factory=list)
    """Règles touchées par ce meuble à cette position, les dures d'abord."""
    message: str = ""
    """Règle dure en deux mots (« fenêtre masquée ») ; vide si tout va bien."""


def _outside_room(level: Level, furniture: Furniture) -> Violation | None:
    """Hors de toute pièce fermée (le meuble ne touche aucun mur, il est simplement dehors)."""
    graph = WallGraph.from_level(level)
    polygons: list[Polygon] = []
    for room in (e for e in level.elements.values() if isinstance(e, Room)):
        try:
            polygon = graph.room_polygon(room.id)
        except GeometryError:
            continue
        if not polygon.is_empty:
            polygons.append(polygon)
    if not polygons:
        return None
    t, b = furniture.transform, furniture.bbox
    footprint = Polygon(
        footprint_corners(t.position.x, t.position.y, t.yaw, b.width * t.scale, b.depth * t.scale)
    )
    if any(p.buffer(1e-3).contains(footprint) for p in polygons):
        return None
    if any(p.intersects(footprint) for p in polygons):
        return None  # à cheval sur un mur : c'est « meuble dans un mur » qui le dira
    return Violation(
        type=OUTSIDE_ROOM,
        severity="high",
        element_ids=[furniture.id],
        message=f"« {furniture.name or furniture.category} » est hors de la pièce.",
    )


def check_furniture_move(
    project: Project, level_id: UUID, element_id: UUID, body: FurnitureMoveBody
) -> FurnitureMoveCheck:
    """Les règles d'aménagement sur ce meuble **à la position essayée**, sans rien modifier :
    le plan rougit le meuble et dit la règle dure en deux mots pendant le glisser."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    furniture = level.elements.get(element_id)
    if not isinstance(furniture, Furniture):
        msg = f"meuble inconnu : {element_id}"
        raise DetectionError(msg)
    moved = furniture.model_copy(
        update={
            "transform": furniture.transform.model_copy(
                update={
                    "position": Point3(
                        x=body.position.x, y=body.position.y, z=furniture.transform.position.z
                    ),
                    "yaw": body.yaw,
                }
            ),
            "bbox": furniture.bbox.model_copy(
                update={
                    "width": (body.width or furniture.bbox.width * furniture.transform.scale)
                    / furniture.transform.scale,
                    "depth": (body.depth or furniture.bbox.depth * furniture.transform.scale)
                    / furniture.transform.scale,
                }
            ),
        }
    )
    return _check_trial(project, level, moved)


def check_furniture_place(
    project: Project, level_id: UUID, body: FurniturePlaceBody
) -> FurnitureMoveCheck:
    """Bibliothèque (F3) : les mêmes règles que le glisser, pour un meuble du catalogue qui
    n'est pas encore dans la scène ; rien n'est posé."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    trial = furniture_from_asset(catalog_asset(body.asset_id), body.position, body.yaw)
    return _check_trial(project, level, trial)


def _check_trial(project: Project, level: Level, moved: Furniture) -> FurnitureMoveCheck:
    trial = _with_furniture(project, level, [moved])
    violations = [v for v in check_layout(trial) if moved.id in v.element_ids]
    outside = _outside_room(level, moved)
    if outside is not None:
        violations.append(outside)
    violations.sort(key=lambda v: _SEVERITY_RANK.get(v.severity, 9))
    hard = [v for v in violations if v.severity == "high"]
    labels = {**RULE_LABELS, OUTSIDE_ROOM: "hors de la pièce"}
    return FurnitureMoveCheck(
        valid=not hard,
        violations=violations,
        message=labels.get(hard[0].type, hard[0].type) if hard else "",
    )


class ElementTrialBody(StrictModel):
    """Essai sur le plan (E4) : un escalier tourné (`yaw`) ou déplacé (`position`), une porte
    retournée (`swing` : gonds et sens d'ouverture)."""

    position: Point2 | None = None
    yaw: float | None = None
    width: float | None = Field(default=None, ge=0.70, le=1.20)
    """Largeur d'emmarchement essayée (escalier élargi par sa poignée, E2)."""
    swing: Swing | None = None


def check_element_trial(
    project: Project, level_id: UUID, element_id: UUID, body: ElementTrialBody
) -> FurnitureMoveCheck:
    """Les règles sur cet escalier ou cette porte **dans l'état essayé**, sans rien modifier :
    même réponse que le glisser d'un meuble (règle dure en deux mots, toutes les règles)."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    element = level.elements.get(element_id)
    update: dict[str, object] = {}
    if isinstance(element, Stairs):
        if body.yaw is not None:
            update["yaw"] = body.yaw
        if body.position is not None:
            update["position"] = body.position
        if body.width is not None:
            update["width"] = body.width
    elif isinstance(element, Opening) and element.kind not in (
        OpeningKind.WINDOW,
        OpeningKind.PASSAGE,
    ):
        if body.swing is not None:
            update["swing"] = body.swing
    else:
        msg = f"ni escalier ni porte : {element_id}"
        raise DetectionError(msg)
    trial = element.model_copy(update=update)
    new_level = level.model_copy(update={"elements": {**level.elements, trial.id: trial}})
    project_trial = project.model_copy(update={"levels": {**project.levels, level.id: new_level}})
    violations = [v for v in check_layout(project_trial) if trial.id in v.element_ids]
    violations.sort(key=lambda v: _SEVERITY_RANK.get(v.severity, 9))
    hard = [v for v in violations if v.severity == "high"]
    return FurnitureMoveCheck(
        valid=not hard,
        violations=violations,
        message=RULE_LABELS.get(hard[0].type, hard[0].type) if hard else "",
    )


def write_layout_artifact(run: LayoutRun, runs_dir: Path) -> Path:
    out_dir = runs_dir / str(run.run_id) / "layout"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "layouts.json"
    path.write_text(run.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path
