"""KAN-137 : une vue calculée par pièce du plan, choisie pour en montrer le maximum.

Pour chaque pièce, les candidats sont ses entrées (porte ou passage, 0,4 m à l'intérieur) et ses
angles (0,5 m le long de la bissectrice), poussés vers l'intérieur jusqu'à une place libre (à
0,3 m des murs, 0,2 m des meubles, hors trémie : `views._room`). La caméra est à hauteur d'œil
(1,55 m), abaissée sous un rampant avec 0,25 m de marge ; sous 1,50 m le candidat ne sert qu'en
dernier recours (1,40 m au moins, et la vue le dit).

Focale : 26 mm en équivalent 24 × 36 rapporté au grand côté ; 24 mm si la pièce fait moins de
3 m de profondeur depuis le candidat, 28 mm au-delà de 6 m (KAN-136 : le rendu garde l'échelle
entre 24 et 28 mm ; jamais plus large, pas de déformation de type fish-eye).

Orientation de 5° en 5°. Chaque couple (place, orientation) est noté sur ce que le cadre voit
vraiment, vu de dessus : des rayons tous les 2°, arrêtés par les murs et les meubles hauts,
donnent la part du sol de la pièce dans le champ horizontal, les murs vus et la fenêtre. (Le
sol tout proche, sous le bas de l'image, n'est pas retiré : à 1,55 m et 26 mm, il s'étend
jusqu'à 2,5 m, et aucune vue d'une chambre de 10 m² n'atteindrait alors 60 %.) Pénalités : un
mur à moins de 1,5 m sur le tiers du cadre, le dos à la fenêtre, un meuble haut à moins de 1 m
devant l'objectif (écarté). Même plan, même vue : rien d'aléatoire.

KAN-160 (décision d'augus, 7 nov.) : c'est le point de vue par défaut de « Rendre ». Le score
devient une part de 0 à 1 : sol vu, périmètre vu, ouvertures vues, éléments notables vus ; moins
le mur proche (continu), l'« écran » (cadre bouché alors qu'on voit plus loin ailleurs), le dos
à la lumière et l'œil bas. Les places : entrées, angles, milieux de murs, plus une grille de
0,5 m. Une 2e vue, seulement si la 1re ne voit pas assez (L, pièce longue, combles cloisonnés),
montre ce que la 1re ne voit pas.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import numpy as np
import shapely
from pydantic import JsonValue
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry

from openplan.geom.roof import ridge_line
from openplan.geom.stairs import stair_layout
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.compose import RUGS
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.mainroom import rooms_of
from openplan.pipeline.naming import ROOM_NAMES
from openplan.pipeline.render import NoCameraError
from openplan.pipeline.views import (
    DISTINCT_POS_M,
    DISTINCT_YAW_DEG,
    HEADROOM_M,
    SIZES,
    TALL_FURNITURE_M,
    _footprint,
    _light_points,
    _Room,
    _room,
    focal_px,
)
from openplan.scene import history
from openplan.schemas.common import Point3, Provenance, Source, StrictModel
from openplan.schemas.elements import DOOR_KINDS, Camera, Opening, Room, Stairs
from openplan.schemas.scene import Level, Project

STEP = "views.rooms"
ROOM_KEY = "room_view"
"""Métadonnée d'une caméra « vue de pièce » : l'identifiant de sa pièce."""
ROOM_NAME_KEY = "room_name"
AREA_KEY = "area_m2"
FLOOR_KEY = "floor_seen"
ON_KEY = "room_view_on"
"""La case du node Rendus : la pièce part dans « Rendre toutes les pièces »."""
ADDED_KEY = "room_view_added"
"""Vue ajoutée par « Ajouter une vue » : gardée quand on recalcule."""

EYE_M = 1.55
EYE_MIN_M = 1.50
EYE_LOW_M = 1.40
"""Dernier recours sous un rampant bas : la vue le dit."""
FOCAL_MM = 26.0
NEAR_FOCAL_MM = 24.0
FAR_FOCAL_MM = 28.0
FOCAL_MIN_MM = 24
FOCAL_MAX_MM = 35
"""KAN-134 : bornes de la focale demandée (« Réglages des rendus »), celles de KAN-141."""
NEAR_DEPTH_M = 3.0
FAR_DEPTH_M = 6.0
PITCH_DEG = 4.0
"""Légèrement vers le bas (3 à 6°) : le sol se lit, les verticales restent droites."""
ATTIC_PITCH_DEG = 6.0
ENTRY_INSET_M = 0.4
CORNER_INSET_M = 0.5
NUDGE_STEP_M = 0.1
NUDGE_MAX_M = 1.2
MID_REACH_M = 3.0
"""Depuis le milieu d'un mur, on avance jusqu'à 3 m pour trouver la hauteur d'œil (combles)."""
YAW_STEP_DEG = 5
RAY_STEP_DEG = 2
RAY_REACH_M = 40.0
WALL_RAYS = 3
"""Un mur compte comme vu s'il arrête au moins trois rayons (6° du cadre)."""
CLOSE_WALL_M = 1.5
CLOSE_WALL_SHARE = 1.0 / 3.0
TALL_NEAR_M = 1.0
BACK_TO_LIGHT_DEG = 135.0
SMALL_ROOM_M2 = 3.0
"""Sous 3 m², la pièce est listée mais pas cochée d'office (augus, 6 nov.)."""
ASPECT = "4:3"

# KAN-160 : score de visibilité (0 à 1), puis pénalités. Poids et seuils fixés au banc des
# scènes de test (rapport KAN-160) ; une scène tenue à part les a validés.
W_FLOOR = 0.55
"""Part du sol libre de la pièce dans le champ horizontal (vu de dessus)."""
W_WALLS = 0.17
"""Part du périmètre de la pièce vue, pondérée par la longueur (un long mur pèse plus)."""
W_OPENINGS = 0.16
"""Part des ouvertures de la pièce vues (dans le champ, sans écran), pondérée : une fenêtre ou
une fenêtre de toit compte 1, une porte `DOOR_WEIGHT` (la lumière fait le rendu, pas la porte)."""
DOOR_WEIGHT = 0.25
W_ITEMS = 0.12
"""Part des éléments notables vus : escalier, meubles de plus de 1 m² au sol, faîtage."""
NEAR_PENALTY = 0.5
"""× la part du cadre où un mur (ou une cloison) est à moins de 1,5 m."""
SCREEN_PENALTY = 0.25
"""« Écran » (combles d'augus) : la profondeur médiane du cadre est courte alors que, depuis la
même place, on voit plus loin ailleurs : × (1 − médiane / min(4 m, plus loin possible))."""
SCREEN_REACH_M = 4.0
BACK_TO_LIGHT_PENALTY = 0.05
LOW_PENALTY = 0.15
ENTRY_TIE = 0.01
"""À score presque égal, l'entrée : la vue naturelle d'un visiteur."""
NOTABLE_AREA_M2 = 1.0
GRID_STEP_M = 0.5
"""En plus des entrées et des angles, une grille de places sur la zone où l'on se tient (pièces
en L, combles, pièces longues : le meilleur point n'est ni une porte ni un angle)."""
SECOND_COVERAGE = 0.55
"""2e vue cherchée si la 1re voit moins que cette part du sol, ou pièce en L ou longue…"""
SECOND_GAIN = 0.15
"""… et gardée seulement si elle ajoute au moins cette part de sol nouveau."""
LONG_RATIO = 2.2
L_FILL = 0.85
"""Pièce « en L » : sa surface remplit moins de 85 % de son plus petit rectangle englobant."""
SECOND_TOP_YAWS = 6
RANK_KEY = "room_view_rank"
"""KAN-160 : 1 pour la vue calculée d'une pièce, 2 pour sa 2e vue calculée."""


@dataclass(frozen=True)
class RoomView:
    """Le point de vue retenu pour une pièce, et ce qu'il montre."""

    room_id: str
    room_name: str
    area_m2: float
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    focal_px: float
    focal_mm: float
    floor_seen: float
    """Part du sol de la pièce dans le champ, vue de dessus (0 à 1)."""
    walls_seen: int
    light_in_view: bool
    where: str
    """« depuis l'entrée », « depuis un angle »…"""
    low: bool = False
    """Œil sous 1,50 m (rampant bas) : dit dans la note."""
    score: float = 0.0
    """KAN-160 : le score de visibilité retenu (après pénalités)."""
    rank: int = 1
    of: int = 1
    """KAN-160 : vue 1 sur 1, ou 1 et 2 sur 2 quand une 2e vue est utile."""

    @property
    def note(self) -> str:
        eye = f"{self.z:.2f}".replace(".", ",")
        # KAN-160 : l'interface dit d'où vient chaque vue (« Vue calculée », « 2/2 »).
        origin = "Vue calculée" + (f" {self.rank}/{self.of}" if self.of > 1 else "")
        text = (
            f"{origin} · {self.where} · {eye} m · {self.focal_mm:.0f} mm · "
            f"{round(self.floor_seen * 100)} % du sol visible"
        )
        return text + (" · œil abaissé sous le rampant" if self.low else "")


@dataclass(frozen=True)
class _Spot:
    x: float
    y: float
    base: float
    """Direction vers l'intérieur de la pièce (radians)."""
    where: str
    entry: bool


def room_names(level: Level) -> dict[str, str]:
    """Le nom de chaque pièce : le sien, sinon son type (« Chambre », « Chambre 2 »…), sinon
    « Pièce 1 », « Pièce 2 »… Dans l'ordre du plan, pour que les numéros restent stables."""
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    rooms = rooms_of(level)
    for room in rooms:
        if room.name and room.name.strip():
            out[str(room.id)] = room.name.strip()
            continue
        base = ROOM_NAMES.get(room.room_type or "", "Pièce")
        counts[base] = counts.get(base, 0) + 1
    seen: dict[str, int] = {}
    for room in rooms:
        if str(room.id) in out:
            continue
        base = ROOM_NAMES.get(room.room_type or "", "Pièce")
        seen[base] = seen.get(base, 0) + 1
        alone = counts[base] == 1 and base != "Pièce"
        out[str(room.id)] = base if alone else f"{base} {seen[base]}"
    return out


def _inward(room: _Room, cx: float, cy: float, ux: float, uy: float) -> tuple[float, float] | None:
    for nx, ny in ((-uy, ux), (uy, -ux)):
        if room.polygon.contains(Point(cx + nx * 0.5, cy + ny * 0.5)):
            return nx, ny
    return None


def _settle(
    room: _Room, x: float, y: float, dx: float, dy: float, reach: float = NUDGE_MAX_M
) -> tuple[float, float] | None:
    """La première place libre en avançant dans le sens (dx, dy), au plus de `reach`, où l'œil
    tient à 1,50 m sous le rampant ; à défaut, la première place libre où il tient à 1,40 m."""
    steps = round(reach / NUDGE_STEP_M)
    low: tuple[float, float] | None = None
    for k in range(steps + 1):
        px, py = x + dx * k * NUDGE_STEP_M, y + dy * k * NUDGE_STEP_M
        if not room.stand.contains(Point(px, py)):
            continue
        eye = room.clearance(px, py) - HEADROOM_M
        if eye >= EYE_MIN_M - 1e-9:
            return px, py
        if low is None and eye >= EYE_LOW_M - 1e-9:
            low = (px, py)
    return low


def _spots(level: Level, target: Room, room: _Room) -> list[_Spot]:
    """Entrées (escalier, portes et passages de la pièce), puis angles intérieurs et milieux
    des murs ; KAN-160 : puis une grille de places sur la zone où l'on se tient."""
    return [
        *_stair_spots(level, room),
        *_door_spots(level, target, room),
        *_corner_spots(room),
        *_grid_spots(room),
    ]


def _grid_spots(room: _Room) -> list[_Spot]:
    """KAN-160 : une place tous les 0,5 m sur la zone où l'on se tient (dans l'ordre des
    lignes, donc déterministe), là où l'œil tient au moins à 1,40 m sous le rampant."""
    if room.stand.is_empty:
        return []
    x0, y0, x1, y1 = room.stand.bounds
    out: list[_Spot] = []
    ny = int((y1 - y0) / GRID_STEP_M) + 1
    nx = int((x1 - x0) / GRID_STEP_M) + 1
    for j in range(ny):
        for i in range(nx):
            x, y = x0 + GRID_STEP_M * (i + 0.5), y0 + GRID_STEP_M * (j + 0.5)
            if not room.stand.contains(Point(x, y)):
                continue
            if room.clearance(x, y) - HEADROOM_M < EYE_LOW_M - 1e-9:
                continue
            out.append(_Spot(x, y, 0.0, "depuis la pièce", False))
    return out


def _stair_spots(level: Level, room: _Room) -> list[_Spot]:
    out: list[_Spot] = []
    for e in level.elements.values():
        if not (isinstance(e, Stairs) and e.visible):
            continue
        walk = stair_layout(e).walk
        (x0, y0), (x1, y1) = walk[-2], walk[-1]
        if not room.polygon.buffer(0.3).contains(Point(x1, y1)):
            continue
        n = math.hypot(x1 - x0, y1 - y0) or 1.0
        dx, dy = (x1 - x0) / n, (y1 - y0) / n
        start = (x1 + dx * ENTRY_INSET_M, y1 + dy * ENTRY_INSET_M)
        if (p := _settle(room, *start, dx, dy)) is not None:
            out.append(_Spot(p[0], p[1], math.atan2(dy, dx), "en haut de l'escalier", True))
    return out


def _door_spots(level: Level, target: Room, room: _Room) -> list[_Spot]:
    out: list[_Spot] = []
    graph = WallGraph.from_level(level)
    walls = set(target.boundary_walls)
    for e in level.elements.values():
        if not (
            isinstance(e, Opening)
            and e.visible
            and e.kind in DOOR_KINDS
            and e.host_wall in walls
            and e.host_wall in graph.walls
        ):
            continue
        a, b = graph.wall_axis(e.host_wall)
        length = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        t = e.offset + e.width / 2.0
        cx, cy = a.x + ux * t, a.y + uy * t
        inward = _inward(room, cx, cy, ux, uy)
        if inward is None:
            continue
        nx, ny = inward
        start = (cx + nx * ENTRY_INSET_M, cy + ny * ENTRY_INSET_M)
        if (p := _settle(room, *start, nx, ny)) is not None:
            out.append(_Spot(p[0], p[1], math.atan2(ny, nx), "depuis l'entrée", True))
    return out


def _corner_spots(room: _Room) -> list[_Spot]:
    out: list[_Spot] = []
    ring = list(room.free.exterior.coords)[:-1] if isinstance(room.free, Polygon) else []
    n = len(ring)
    for i in range(n):
        (px, py), (qx, qy), (rx, ry) = ring[i - 1], ring[i], ring[(i + 1) % n]
        a1 = (px - qx, py - qy)
        a2 = (rx - qx, ry - qy)
        l1, l2 = math.hypot(*a1), math.hypot(*a2)
        if l1 < 0.3 or l2 < 0.3:
            continue
        bx, by = a1[0] / l1 + a2[0] / l2, a1[1] / l1 + a2[1] / l2
        lb = math.hypot(bx, by)
        if lb < 1e-6:
            continue
        bx, by = bx / lb, by / lb
        if not room.free.contains(Point(qx + bx * 0.05, qy + by * 0.05)):
            continue  # angle rentrant (pièce en L) : la bissectrice sort de la pièce
        start = (qx + bx * CORNER_INSET_M, qy + by * CORNER_INSET_M)
        if (p := _settle(room, *start, bx, by)) is not None:
            out.append(_Spot(p[0], p[1], math.atan2(by, bx), "depuis un angle", False))
        # Milieu du côté suivant, vers l'intérieur : sous des combles, c'est souvent la seule
        # place où l'on se tient debout (sous le faîtage, depuis le pignon ou depuis l'égout).
        mx, my = (qx + rx) / 2.0, (qy + ry) / 2.0
        inward = _inward(room, mx, my, a2[0] / l2, a2[1] / l2)
        if inward is not None:
            nx, ny = inward
            start = (mx + nx * CORNER_INSET_M, my + ny * CORNER_INSET_M)
            if (p := _settle(room, *start, nx, ny, MID_REACH_M)) is not None:
                out.append(_Spot(p[0], p[1], math.atan2(ny, nx), "depuis un mur", False))
    return out


def _focal_mm(depth: float) -> float:
    if depth < NEAR_DEPTH_M:
        return NEAR_FOCAL_MM
    if depth > FAR_DEPTH_M:
        return FAR_FOCAL_MM
    return FOCAL_MM


@dataclass(frozen=True)
class _Rays:
    angles: np.ndarray
    depth: np.ndarray
    hits: np.ndarray
    """Mur arrêtant chaque rayon (indice dans la liste des murs de la pièce), -1 sinon."""


def _rays(room: _Room, walls: list[Polygon], x: float, y: float) -> _Rays:
    angles = np.radians(np.arange(0, 360, RAY_STEP_DEG, dtype=np.float64))
    ends = np.column_stack([x + RAY_REACH_M * np.cos(angles), y + RAY_REACH_M * np.sin(angles)])
    lines = shapely.linestrings(np.stack([np.tile([x, y], (len(angles), 1)), ends], axis=1))
    crossings = shapely.intersection(lines, room.sight.boundary)
    start = Point(x, y)
    depth = np.asarray(shapely.distance(start, crossings), dtype=np.float64)
    depth = np.where(np.isfinite(depth), depth, RAY_REACH_M)
    hit_points = shapely.points(
        np.column_stack([x + depth * np.cos(angles), y + depth * np.sin(angles)])
    )
    hits = np.full(len(angles), -1, dtype=np.int64)
    best = np.full(len(angles), 0.12)
    for k, wall in enumerate(walls):
        gap = np.asarray(shapely.distance(wall, hit_points), dtype=np.float64)
        closer = gap < best
        hits[closer] = k
        best[closer] = gap[closer]
    return _Rays(angles, depth, hits)


def _gap(a: float, b: float) -> float:
    """Écart absolu entre deux directions, de 0 à π."""
    return abs(math.atan2(math.sin(a - b), math.cos(a - b)))


def _window(rays: _Rays, yaw: float, half: float) -> np.ndarray:
    gap = np.arctan2(np.sin(rays.angles - yaw), np.cos(rays.angles - yaw))
    return np.abs(gap) <= half


def _tall_ahead(room: _Room, x: float, y: float, yaw: float, half: float) -> bool:
    cone = Polygon(
        [
            (x, y),
            (x + TALL_NEAR_M * math.cos(yaw - half), y + TALL_NEAR_M * math.sin(yaw - half)),
            (x + TALL_NEAR_M * math.cos(yaw), y + TALL_NEAR_M * math.sin(yaw)),
            (x + TALL_NEAR_M * math.cos(yaw + half), y + TALL_NEAR_M * math.sin(yaw + half)),
        ]
    )
    return any(
        f.category not in RUGS
        and f.bbox.height * f.transform.scale > TALL_FURNITURE_M
        and _footprint(f).intersects(cone)
        for f in room.furniture
    )


@dataclass(frozen=True)
class _Target:
    """KAN-160 : ce qu'une vue doit montrer, en plan : une ouverture (porte, fenêtre, fenêtre
    de toit) ou un élément notable (escalier, grand meuble, faîtage)."""

    x: float
    y: float
    opening: bool
    light: bool = False
    weight: float = 1.0
    """Ce que la cible pèse dans sa part (une porte : `DOOR_WEIGHT`)."""
    shape: BaseGeometry | None = None
    """Pour un meuble : son emprise (le point visé est son bord le plus proche de la caméra)."""


def _targets(
    level: Level, target: Room, room: _Room, lights: list[tuple[float, float]]
) -> list[_Target]:
    out = [_Target(x, y, True, light=True) for x, y in lights]
    graph = WallGraph.from_level(level)
    walls = set(target.boundary_walls)
    for e in level.elements.values():
        if not (
            isinstance(e, Opening)
            and e.visible
            and e.kind in DOOR_KINDS
            and e.host_wall in walls
            and e.host_wall in graph.walls
        ):
            continue
        a, b = graph.wall_axis(e.host_wall)
        length = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        t = e.offset + e.width / 2.0
        cx, cy = a.x + ux * t, a.y + uy * t
        if (inward := _inward(room, cx, cy, ux, uy)) is not None:
            out.append(
                _Target(cx + inward[0] * 0.3, cy + inward[1] * 0.3, True, weight=DOOR_WEIGHT)
            )
    for e in level.elements.values():
        if isinstance(e, Stairs) and e.visible:
            c = stair_layout(e).opening.centroid
            if room.polygon.contains(c):
                out.append(_Target(c.x, c.y, False))
    for f in room.furniture:
        if f.category in RUGS:
            continue
        shape = _footprint(f)
        if shape.area >= NOTABLE_AREA_M2:
            c = shape.centroid
            out.append(_Target(c.x, c.y, False, shape=shape))
    if room.roofs and (ridge := ridge_line(room.roofs)) is not None:
        (px, py), (dx, dy) = ridge
        c = room.free.centroid
        t = (c.x - px) * dx + (c.y - py) * dy
        q = (px + dx * t, py + dy * t)
        if room.free.contains(Point(q)):
            out.append(_Target(q[0], q[1], False))
    return out


def _share(targets: list[_Target], seen: Iterable[int], among: Iterable[int]) -> float:
    """La part pondérée des cibles `among` qui sont dans `seen`."""
    pool = list(among)
    total = sum(targets[i].weight for i in pool)
    hit = set(seen)
    return sum(targets[i].weight for i in pool if i in hit) / total if total > 0 else 1.0


def _aim(t: _Target, x: float, y: float) -> tuple[float, float]:
    """Le point visé : celui de la cible, ou le bord d'un meuble le plus proche, 10 cm en deçà."""
    if t.shape is None:
        return t.x, t.y
    edge = t.shape.exterior.interpolate(t.shape.exterior.project(Point(x, y)))
    d = math.hypot(x - edge.x, y - edge.y) or 1.0
    k = min(0.1, d) / d
    return edge.x + (x - edge.x) * k, edge.y + (y - edge.y) * k


@dataclass(frozen=True)
class _Choice:
    score: float
    spot: _Spot
    z: float
    yaw: float
    pitch: float
    focal_mm: float
    floor: float
    walls: int
    light: bool
    low: bool
    perimeter: float = 0.0
    """Part du périmètre de la pièce vue (0 à 1)."""
    seen: frozenset[int] = frozenset()
    """Indices des cibles (`_targets`) vues par ce cadre."""
    near: float = 0.0
    screen: float = 0.0
    half: float = 0.0
    rays: _Rays | None = None


def _choices_for_spot(
    room: _Room,
    walls: list[Polygon],
    targets: list[_Target],
    floor_area: float,
    spot: _Spot,
    avoid: list[tuple[float, float, float]],
    wanted_mm: float | None = None,
) -> list[_Choice]:
    """Tous les cadres permis depuis cette place (une orientation tous les 5°), notés."""
    clearance = room.clearance(spot.x, spot.y)
    z = min(EYE_M, clearance - HEADROOM_M)
    if z < EYE_LOW_M - 1e-9:
        return []
    low = z < EYE_MIN_M - 1e-9
    rays = _rays(room, walls, spot.x, spot.y)
    reach = float(rays.depth.max())
    # KAN-134 : la focale demandée (« Réglages des rendus ») ; sans elle, selon la profondeur.
    focal_mm = float(wanted_mm) if wanted_mm is not None else _focal_mm(reach)
    focal = focal_px(focal_mm)
    width, _ = SIZES[ASPECT]
    half = math.atan(width / 2.0 / focal)
    pitch = -math.radians(ATTIC_PITCH_DEG if room.roofs else PITCH_DEG)
    # Part du sol dans le champ horizontal, vue de dessus : un secteur par rayon.
    slices = 0.5 * rays.depth**2 * math.radians(RAY_STEP_DEG)
    # Part du périmètre : chaque rayon arrêté par un mur de la pièce en voit ~ d·Δθ.
    perimeter = float(room.polygon.exterior.length) or 1.0
    arcs = np.where(rays.hits >= 0, rays.depth * math.radians(RAY_STEP_DEG), 0.0)
    # Les cibles : direction et visibilité calculées une fois par place.
    here = (spot.x, spot.y)
    aims = [_aim(t, *here) for t in targets]
    bearing = [math.atan2(ay - spot.y, ax - spot.x) for ax, ay in aims]
    visible = [room.sees(here, p) for p in aims]
    openings = [i for i, t in enumerate(targets) if t.opening]
    items = [i for i, t in enumerate(targets) if not t.opening]
    lights = [i for i, t in enumerate(targets) if t.light]
    out: list[_Choice] = []
    for deg in range(0, 360, YAW_STEP_DEG):
        yaw = math.radians(deg)
        if any(
            math.hypot(spot.x - ax, spot.y - ay) < DISTINCT_POS_M
            and _gap(yaw, ayaw) < math.radians(DISTINCT_YAW_DEG)
            for ax, ay, ayaw in avoid
        ):
            continue
        if _tall_ahead(room, spot.x, spot.y, yaw, half):
            continue
        inside = _window(rays, yaw, half)
        if not inside.any():
            continue
        floor = min(1.0, float(slices[inside].sum()) / floor_area) if floor_area > 0 else 0.0
        hit = rays.hits[inside]
        counts = np.bincount(hit[hit >= 0], minlength=len(walls)) if len(walls) else np.array([])
        seen_walls = int((counts >= WALL_RAYS).sum()) if len(walls) else 0
        share = min(1.0, float(arcs[inside].sum()) / perimeter)
        seen = frozenset(
            i for i in range(len(targets)) if visible[i] and _gap(bearing[i], yaw) <= half
        )
        light = any(i in seen for i in lights)
        behind = (
            bool(lights)
            and not light
            and min(_gap(bearing[i], yaw) for i in lights) > math.radians(BACK_TO_LIGHT_DEG)
        )
        depth = rays.depth[inside]
        near = float((depth < CLOSE_WALL_M).mean())
        ref = min(SCREEN_REACH_M, reach)
        screen = SCREEN_PENALTY * max(0.0, 1.0 - float(np.median(depth)) / ref) if ref > 0 else 0.0
        open_share = _share(targets, seen, openings) if openings else 1.0
        item_share = _share(targets, seen, items) if items else 1.0
        score = (
            W_FLOOR * floor
            + W_WALLS * share
            + W_OPENINGS * open_share
            + W_ITEMS * item_share
            - NEAR_PENALTY * near
            - screen
            - (BACK_TO_LIGHT_PENALTY if behind else 0.0)
            - (LOW_PENALTY if low else 0.0)
            + (ENTRY_TIE if spot.entry else 0.0)
        )
        out.append(
            _Choice(
                score,
                spot,
                z,
                yaw,
                pitch,
                focal_mm,
                floor,
                seen_walls,
                light,
                low,
                share,
                seen,
                near,
                screen,
                half,
                rays,
            )
        )
    return out


def _fan(c: _Choice, room: _Room) -> BaseGeometry:
    """Le sol réellement vu par le cadre : l'éventail des rayons du champ, coupé par la pièce."""
    assert c.rays is not None
    gap = np.arctan2(np.sin(c.rays.angles - c.yaw), np.cos(c.rays.angles - c.yaw))
    idx = np.where(np.abs(gap) <= c.half)[0]
    idx = idx[np.argsort(gap[idx])]
    pts = [(c.spot.x, c.spot.y)] + [
        (
            c.spot.x + c.rays.depth[i] * math.cos(c.rays.angles[i]),
            c.spot.y + c.rays.depth[i] * math.sin(c.rays.angles[i]),
        )
        for i in idx
    ]
    if len(pts) < 3:
        return Polygon()
    return Polygon(pts).buffer(0).intersection(room.free)


def _needs_second(room: _Room, coverage: float) -> bool:
    """2e vue cherchée : la 1re voit moins de 55 % du sol, ou la pièce est en L ou longue."""
    if coverage < SECOND_COVERAGE:
        return True
    box = room.free.minimum_rotated_rectangle
    if box.area <= 0:
        return False
    if room.free.area / box.area < L_FILL:
        return True
    xs, ys = box.exterior.coords.xy
    sides = sorted(math.hypot(xs[i + 1] - xs[i], ys[i + 1] - ys[i]) for i in range(2))
    return sides[0] > 0 and sides[1] / sides[0] > LONG_RATIO


def _second_choice(
    room: _Room,
    choices: list[_Choice],
    first: _Choice,
    targets: list[_Target],
    floor_area: float,
) -> tuple[_Choice, float] | None:
    """La 2e vue : celle qui montre le plus de ce que la 1re ne voit pas (sol, ouvertures,
    éléments), distincte d'elle (1 m plus loin ou 35° plus tournée) et ajoutant au moins 15 %
    du sol. Renvoie aussi la part de sol nouveau."""
    fan1 = _fan(first, room)
    openings = {i for i, t in enumerate(targets) if t.opening}
    items = {i for i, t in enumerate(targets) if not t.opening}
    by_spot: dict[tuple[float, float], list[_Choice]] = {}
    for c in choices:
        by_spot.setdefault((c.spot.x, c.spot.y), []).append(c)
    best: tuple[float, _Choice, float] | None = None
    for group in by_spot.values():
        for c in sorted(group, key=lambda k: -k.score)[:SECOND_TOP_YAWS]:
            moved = math.hypot(c.spot.x - first.spot.x, c.spot.y - first.spot.y)
            if moved < DISTINCT_POS_M and _gap(c.yaw, first.yaw) < math.radians(DISTINCT_YAW_DEG):
                continue
            new = float(_fan(c, room).difference(fan1).area) / floor_area if floor_area else 0.0
            if new < SECOND_GAIN:
                continue
            fresh = c.seen - first.seen
            v = (
                W_FLOOR * new
                + W_WALLS * c.perimeter * (new / max(c.floor, 1e-6))
                + (W_OPENINGS * _share(targets, fresh, openings) if openings else 0.0)
                + (W_ITEMS * _share(targets, fresh, items) if items else 0.0)
                - NEAR_PENALTY * c.near
                - c.screen
                - (LOW_PENALTY if c.low else 0.0)
            )
            if best is None or v > best[0] + 1e-9:
                best = (v, c, new)
    return (best[1], best[2]) if best is not None else None


def plan_room_view(
    level: Level,
    target: Room,
    name: str,
    area_m2: float,
    avoid: list[tuple[float, float, float]] | None = None,
    focal_mm: float | None = None,
) -> RoomView | None:
    """La meilleure vue de la pièce `target` ; `avoid` : vues déjà posées (x, y, lacet) dont
    la nouvelle doit se distinguer (« Ajouter une vue ») ; `focal_mm` : focale imposée (KAN-134),
    sinon choisie selon la profondeur de la pièce. None si aucune place ne convient."""
    views = _plan(level, target, name, area_m2, avoid or [], focal_mm, second=False)
    return views[0] if views else None


def plan_room_view_pair(
    level: Level, target: Room, name: str, area_m2: float, focal_mm: float | None = None
) -> list[RoomView]:
    """KAN-160 : la vue de la pièce, et une 2e seulement si la 1re ne voit pas assez (pièce en
    L, longue, combles cloisonnés) et qu'elle ajoute au moins 15 % du sol. Vide si aucune
    place ne convient."""
    return _plan(level, target, name, area_m2, [], focal_mm, second=True)


def _view(target: Room, name: str, area_m2: float, c: _Choice, rank: int, of: int) -> RoomView:
    return RoomView(
        room_id=str(target.id),
        room_name=name if rank == 1 else f"{name} · vue {rank}",
        area_m2=round(area_m2, 1),
        x=c.spot.x,
        y=c.spot.y,
        z=c.z,
        yaw=c.yaw,
        pitch=c.pitch,
        focal_px=focal_px(c.focal_mm),
        focal_mm=c.focal_mm,
        floor_seen=c.floor,
        walls_seen=c.walls,
        light_in_view=c.light,
        where=c.spot.where,
        low=c.low,
        score=round(c.score, 4),
        rank=rank,
        of=of,
    )


def _plan(
    level: Level,
    target: Room,
    name: str,
    area_m2: float,
    avoid: list[tuple[float, float, float]],
    focal_mm: float | None,
    *,
    second: bool,
) -> list[RoomView]:
    check_focal(focal_mm)
    room = _room(level, target)
    graph = WallGraph.from_level(level)
    walls = [graph.wall_polygon(w) for w in target.boundary_walls if w in graph.walls]
    targets = _targets(level, target, room, _light_points(room))
    floor_area = float(room.free.area)
    choices = [
        c
        for spot in _spots(level, target, room)
        for c in _choices_for_spot(room, walls, targets, floor_area, spot, avoid, focal_mm)
    ]
    if not choices:
        return []
    # Le premier meilleur dans l'ordre des places : même plan, même vue.
    best = choices[0]
    for c in choices[1:]:
        if c.score > best.score + 1e-9:
            best = c
    if not second:
        return [_view(target, name, area_m2, best, 1, 1)]
    coverage = float(_fan(best, room).area) / floor_area if floor_area else 1.0
    if not _needs_second(room, coverage):
        return [_view(target, name, area_m2, best, 1, 1)]
    pair = _second_choice(room, choices, best, targets, floor_area)
    if pair is None:
        return [_view(target, name, area_m2, best, 1, 1)]
    return [_view(target, name, area_m2, best, 1, 2), _view(target, name, area_m2, pair[0], 2, 2)]


def check_focal(focal_mm: float | None) -> None:
    """KAN-134 : une focale demandée hors de 24 à 35 mm est refusée (ValueError)."""
    if focal_mm is not None and not FOCAL_MIN_MM <= focal_mm <= FOCAL_MAX_MM:
        msg = f"focale hors bornes : {focal_mm} mm (de {FOCAL_MIN_MM} à {FOCAL_MAX_MM} mm)"
        raise ValueError(msg)


def plan_room_views(
    level: Level, focal_mm: float | None = None, *, second: bool = False
) -> tuple[list[RoomView], list[str]]:
    """Une vue par pièce du niveau, et la raison de chaque pièce sans vue ; `focal_mm` : focale
    imposée à toutes (KAN-134), sinon celle du calcul. KAN-160 : `second`, la 2e vue de chaque
    pièce qui en a besoin, juste après la 1re."""
    names = room_names(level)
    areas = compute_derived(level).rooms
    views: list[RoomView] = []
    skipped: list[str] = []
    for room in rooms_of(level):
        name = names[str(room.id)]
        area = areas[room.id].area_m2 if room.id in areas else 0.0
        try:
            found = _plan(level, room, name, area, [], focal_mm, second=second)
        except (NoCameraError, GeometryError):
            found = []
        if not found:
            skipped.append(f"{name} : aucune place pour la caméra")
        views.extend(found)
    return views, skipped


# ------------------------------------------------------------------ dans le plan
class RoomViews(StrictModel):
    """Ce que « Vues par pièce » a fait : vues posées, gardées (modifiées à la main ou
    ajoutées), pièces sans vue (et pourquoi)."""

    added: list[str]
    kept: list[str]
    skipped: list[str]


def is_room_view(e: object) -> bool:
    return isinstance(e, Camera) and isinstance(e.metadata.get(ROOM_KEY), str)


def _camera(view: RoomView, run_id: UUID, *, on: bool, added: bool = False) -> Camera:
    meta: dict[str, JsonValue] = {
        ROOM_KEY: view.room_id,
        ROOM_NAME_KEY: view.room_name,
        AREA_KEY: view.area_m2,
        FLOOR_KEY: round(view.floor_seen, 3),
        ON_KEY: on,
        RANK_KEY: view.rank,
        "aspect": ASPECT,
        "note": view.note,
    }
    if added:
        meta[ADDED_KEY] = True
    return Camera(
        name=view.room_name,
        position=Point3(x=round(view.x, 3), y=round(view.y, 3), z=round(view.z, 3)),
        yaw=round(view.yaw, 4),
        pitch=round(view.pitch, 4),
        focal_px=round(view.focal_px, 1),
        source=Source.GENERATED,
        confidence=0.8,
        provenance=Provenance(step=STEP, run_id=run_id),
        metadata=meta,
    )


def apply_room_views(
    project: Project, level_id: UUID, *, actor: str, focal_mm: float | None = None
) -> tuple[Project, RoomViews]:
    """Pose une vue calculée par pièce (un seul pas d'historique, annulable). Les vues de pièce
    calculées d'avant sont remplacées ; celles modifiées à la main ou ajoutées restent. La case
    de chaque pièce est gardée ; une pièce nouvelle est cochée dès 3 m². KAN-134 : `focal_mm`,
    la focale des « Réglages des rendus », vaut pour les vues recalculées seulement."""
    check_focal(focal_mm)
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    elements = dict(level.elements)
    rooms = {str(r.id) for r in rooms_of(level)}
    on_before: dict[str, bool] = {}
    kept_rooms: set[str] = set()
    kept: list[str] = []
    for e in level.elements.values():
        if not (isinstance(e, Camera) and is_room_view(e)):
            continue
        room_id = str(e.metadata[ROOM_KEY])
        on_before.setdefault(room_id, bool(e.metadata.get(ON_KEY, True)))
        if room_id not in rooms:
            del elements[e.id]  # la pièce n'existe plus
        elif e.source is Source.USER_DEFINED:
            kept_rooms.add(room_id)  # sa vue à la main tient lieu de vue calculée
            kept.append(f"{e.name} : modifiée à la main, gardée")
        elif e.metadata.get(ADDED_KEY):
            kept.append(f"{e.name} : vue ajoutée, gardée")
        else:
            del elements[e.id]
    # KAN-160 : la 2e vue d'une pièce qui en a besoin, cochée comme sa pièce.
    views, skipped = plan_room_views(level, focal_mm, second=True)
    run_id = uuid4()
    added: list[str] = []
    for view in views:
        if view.room_id in kept_rooms:
            continue
        on = on_before.get(view.room_id, view.area_m2 >= SMALL_ROOM_M2)
        camera = _camera(view, run_id, on=on)
        elements[camera.id] = camera
        added.append(str(camera.id))
    new_level = level.model_copy(update={"elements": elements})
    after = project.model_copy(update={"levels": {**project.levels, level.id: new_level}})
    committed = history.commit(project, after, actor=actor, label=f"Vues par pièce ({len(added)})")
    return committed, RoomViews(added=added, kept=kept, skipped=skipped)


def add_room_view(
    project: Project, level_id: UUID, room_id: UUID, *, actor: str
) -> tuple[Project, Camera]:
    """« Ajouter une vue » : la meilleure vue de la pièce qui se distingue de ses vues déjà
    posées (au moins 1 m plus loin ou 35° plus tournée). NoCameraError s'il n'y en a pas."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    room = next((r for r in rooms_of(level) if r.id == room_id), None)
    if room is None:
        msg = f"pièce inconnue : {room_id}"
        raise NoCameraError(msg)
    taken = [
        e
        for e in level.elements.values()
        if isinstance(e, Camera) and is_room_view(e) and e.metadata[ROOM_KEY] == str(room.id)
    ]
    name = room_names(level)[str(room.id)]
    areas = compute_derived(level).rooms
    view = plan_room_view(
        level,
        room,
        name,
        areas[room.id].area_m2 if room.id in areas else 0.0,
        avoid=[(c.position.x, c.position.y, c.yaw) for c in taken],
    )
    if view is None:
        msg = f"{name} : aucune autre place pour une vue"
        raise NoCameraError(msg)
    view = replace(view, room_name=f"{name} · vue {len(taken) + 1}" if taken else name)
    camera = _camera(view, uuid4(), on=True, added=True)
    new_level = level.model_copy(update={"elements": {**level.elements, camera.id: camera}})
    after = project.model_copy(update={"levels": {**project.levels, level.id: new_level}})
    return history.commit(project, after, actor=actor, label=f"Ajouter une vue : {name}"), camera


def reset_room_view(
    project: Project, level_id: UUID, camera_id: UUID, *, actor: str
) -> tuple[Project, Camera]:
    """KAN-141 : « Recalculer » une vue de pièce modifiée à la main (maquette 2fb2691) : elle
    reprend la vue calculée (une vue ajoutée, la meilleure qui se distingue des autres vues de
    sa pièce) et redevient `generated` ; sa case, son nom et son statut de vue ajoutée restent.
    Un seul pas d'historique. NoCameraError si ce n'est pas une vue de pièce."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    camera = level.elements.get(camera_id)
    if not (isinstance(camera, Camera) and is_room_view(camera)):
        msg = f"vue de pièce inconnue : {camera_id}"
        raise NoCameraError(msg)
    room_id = str(camera.metadata[ROOM_KEY])
    room = next((r for r in rooms_of(level) if str(r.id) == room_id), None)
    if room is None:
        msg = f"pièce inconnue : {room_id}"
        raise NoCameraError(msg)
    others = [
        e
        for e in level.elements.values()
        if isinstance(e, Camera)
        and is_room_view(e)
        and e.metadata[ROOM_KEY] == room_id
        and e.id != camera.id
    ]
    added = bool(camera.metadata.get(ADDED_KEY))
    name = room_names(level)[room_id]
    areas = compute_derived(level).rooms
    area = areas[room.id].area_m2 if room.id in areas else 0.0
    view: RoomView | None
    if not added and camera.metadata.get(RANK_KEY) == 2:
        # KAN-160 : une 2e vue calculée reprend la 2e vue (sinon, la meilleure qui se distingue
        # des autres vues de sa pièce, jamais une copie de la 1re).
        pair = plan_room_view_pair(level, room, name, area)
        view = (
            pair[1]
            if len(pair) == 2
            else plan_room_view(
                level, room, name, area, avoid=[(c.position.x, c.position.y, c.yaw) for c in others]
            )
        )
    else:
        view = plan_room_view(
            level,
            room,
            name,
            area,
            avoid=[(c.position.x, c.position.y, c.yaw) for c in others] if added else None,
        )
    if view is None:
        msg = f"{camera.name or name} : aucune place pour la caméra"
        raise NoCameraError(msg)
    fresh = _camera(view, uuid4(), on=bool(camera.metadata.get(ON_KEY, True)), added=added)
    restored = fresh.model_copy(
        update={
            "id": camera.id,
            "name": camera.name,
            "metadata": {**fresh.metadata, ROOM_NAME_KEY: camera.metadata.get(ROOM_NAME_KEY)},
        }
    )
    new_level = level.model_copy(update={"elements": {**level.elements, camera.id: restored}})
    after = project.model_copy(update={"levels": {**project.levels, level.id: new_level}})
    label = f"Recalculer la vue : {camera.name or name}"
    return history.commit(project, after, actor=actor, label=label), restored
