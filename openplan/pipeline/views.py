"""Trois vues automatiques (V1, note 10, point 4) : ensemble, zone, détail.

- **ensemble** : depuis l'arrivée dans la pièce (haut de l'escalier, sinon la porte), à hauteur
  d'œil, grand angle, tournée vers la plus grande profondeur visible ;
- **zone** : la zone meublée principale (détente, lecture, bureau, sinon couchage), à 3 m, à
  1,20 m de haut, focale de 26 mm, de trois quarts face au meuble principal ;
- **détail** : lit et chevet (sinon fauteuil et lampadaire, bureau et chaise…), à 2 m, focale
  de 35 mm, du côté du chevet.

Les focales sont des équivalents 24 × 36 rapportés au grand côté de l'image (1280 px). Chaque
vue est vérifiée : dans la pièce, hors des meubles et de la trémie, sous la pente avec de la
marge, sans mur entre la caméra et ce qu'elle cadre. Les vues sont des points de vue ordinaires
(`Camera`), `generated`, marquées `metadata["auto_view"]` ; les reposer remplace les vues
automatiques d'avant, sauf celles que l'utilisateur a modifiées à la main (`user_defined`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID, uuid4

from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from openplan.geom.roof import derived_roofs, roof_window_footprint
from openplan.geom.stairs import stair_layout
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.compose import BEDS, RUGS, SOFAS
from openplan.pipeline.layouts import ZONE_KEY
from openplan.pipeline.mainroom import (
    GENERATED_KEY,
    ZONES_KEY,
    is_generated,
    main_room,
    principal_bedroom,
    rooms_of,
)
from openplan.pipeline.render import (
    ARCHITECT_FOCAL_PX,
    NoCameraError,
    _room_shape,
    architect_view,
)
from openplan.scene import history
from openplan.scene.history import is_user
from openplan.schemas.common import Point3, Provenance, Source, StrictModel
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import (
    DOOR_KINDS,
    GLAZED_KINDS,
    Camera,
    Furniture,
    Opening,
    RoofWindow,
    Room,
    Stairs,
)
from openplan.schemas.intent import DesignIntent, ViewsIntent, merge_intent
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import clearance_at

STEP = "views.auto"
AUTO_KEY = "auto_view"
ASPECT_KEY = "aspect"
Role = Literal["overall", "zone", "detail"]
ROLES: tuple[Role, ...] = ("overall", "zone", "detail")

LONG_SIDE_PX = 1280
SIZES: dict[str, tuple[int, int]] = {"4:3": (1280, 960), "4:5": (1024, 1280)}
"""Taille du rendu selon le format : paysage 4:3, portrait 4:5 (Pinterest)."""


def focal_px(mm: float) -> float:
    """Focale en pixels d'un objectif `mm` (équivalent 24 × 36, grand côté de 1280 px)."""
    return LONG_SIDE_PX * mm / 36.0


EYE_M = 1.5
ZONE_DISTANCE_M = 3.0
ZONE_HEIGHT_M = 1.2
ZONE_FOCAL_MM = 26.0
ZONE_ANGLE_DEG = 25.0
DETAIL_DISTANCE_M = 2.0
DETAIL_HEIGHT_M = 1.0
DETAIL_FOCAL_MM = 35.0
DETAIL_ANGLE_DEG = 40.0
HEADROOM_M = 0.25
"""Marge entre la caméra et la pente au-dessus d'elle."""
WALL_MARGIN_M = 0.3
FURNITURE_MARGIN_M = 0.2
TALL_FURNITURE_M = 0.9
"""Un meuble plus haut que ça (lampadaire, armoire) bouche la vue d'ensemble."""
CLEAR_FRAME = 0.85
"""Vue de zone ou de détail : pas de mur dans le cadre avant 85 % de la distance visée."""

ZONE_ANCHORS: list[tuple[str, tuple[str, ...]]] = [
    ("détente", SOFAS),
    ("lecture", ("armchair",)),
    ("bureau", ("desk",)),
    ("couchage", BEDS),
]
"""Zone cadrée par la vue de zone, dans l'ordre : le lit a déjà sa vue de détail."""
DETAIL_PAIRS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    # Lit encastré (H1) : sa tête et sa planche-chevet suffisent, sans meuble voisin.
    ("lit encastré", ("bed_builtin",), ()),
    ("lit et chevet", BEDS, ("nightstand",)),
    ("fauteuil et lampadaire", ("armchair",), ("floor_lamp", "side_table")),
    ("bureau et chaise", ("desk",), ("chair",)),
    ("canapé et table basse", SOFAS, ("coffee_table",)),
    ("cuisine", ("stove", "kitchen_sink"), ("kitchen_sink", "stove", "kitchen_base", "fridge")),
    ("table et chaises", ("table", "round_table"), ("dining_chair",)),
]


@dataclass(frozen=True)
class ViewPlan:
    role: Role
    name: str
    x: float
    y: float
    z: float
    yaw: float
    pitch: float
    focal_px: float
    note: str


class AutoViews(StrictModel):
    """Ce que « Vues automatiques » a fait : vues posées, gardées, manquées (et pourquoi)."""

    added: list[UUID]
    kept: list[str]
    """Vues modifiées à la main, laissées telles quelles."""
    skipped: list[str]
    """Vues non posées, avec la raison (« pas de lit ni de fauteuil… »)."""
    aspect: str
    notes: list[str]


# ------------------------------------------------------------------ la pièce, vue par la caméra
@dataclass
class _Room:
    level: Level
    polygon: Polygon
    free: BaseGeometry
    """Où l'on voit : la pièce moins les murs (cloisons comprises)."""
    stand: BaseGeometry
    """Où l'on peut poser la caméra : loin des murs, hors trémie et hors meubles."""
    roofs: list[DerivedRoof]
    furniture: list[Furniture]

    def clearance(self, x: float, y: float) -> float:
        if not self.roofs:
            return self.level.height
        return clearance_at((x, y), self.roofs, self.level)

    def height_at(self, x: float, y: float, wanted: float) -> float | None:
        """Hauteur de caméra possible en (x, y) : `wanted`, abaissée sous une pente basse ;
        None si l'on n'y tient pas (moins de 1 m de caméra)."""
        if not self.stand.contains(Point(x, y)):
            return None
        z = min(wanted, self.clearance(x, y) - HEADROOM_M)
        return z if z >= 1.0 - 1e-9 else None

    sight: BaseGeometry
    """Comme `free`, moins les meubles hauts (lampadaire, armoire) qui bouchent la vue."""

    def sees(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        return bool(self.free.buffer(0.05).contains(LineString([a, b])))

    def ray(self, x: float, y: float, angle: float, *, furniture: bool = False) -> float:
        """Profondeur visible depuis (x, y) dans la direction `angle`, jusqu'au premier mur
        (et au premier meuble haut si `furniture`)."""
        far = (x + 40.0 * math.cos(angle), y + 40.0 * math.sin(angle))
        area = self.sight if furniture else self.free
        seen = LineString([(x, y), far]).intersection(area)
        parts = getattr(seen, "geoms", [seen])
        start = Point(x, y)
        return max((p.length for p in parts if p.distance(start) < 0.05), default=0.0)

    def cone(self, x: float, y: float, yaw: float, half: float, *, furniture: bool) -> list[float]:
        """Profondeurs visibles sur la largeur du cadre (cinq rayons, bords à 85 %)."""
        return [
            self.ray(x, y, yaw + k * 0.85 * half, furniture=furniture)
            for k in (-1.0, -0.5, 0.0, 0.5, 1.0)
        ]


def _footprint(f: Furniture, margin: float = 0.0) -> Polygon:
    t = f.transform
    w, d = f.bbox.width * t.scale / 2.0 + margin, f.bbox.depth * t.scale / 2.0 + margin
    c, s = math.cos(t.yaw), math.sin(t.yaw)
    return Polygon(
        [
            (t.position.x + c * dx - s * dy, t.position.y + s * dx + c * dy)
            for dx, dy in ((-w, -d), (w, -d), (w, d), (-w, d))
        ]
    )


def _room(level: Level, target: Room | None = None) -> _Room:
    """La pièce vue par la caméra : la plus grande (une pièce seule), ou `target` (plan
    généré, L3) avec seulement ses meubles."""
    polygon: Polygon | None
    if target is None:
        polygon, _ = _room_shape(level)
    else:
        try:
            polygon = WallGraph.from_level(level).room_polygon(target.id)
        except GeometryError:
            polygon = None
    if polygon is None:
        msg = "tracez d'abord les murs et détectez la pièce : les vues automatiques la cadrent"
        raise NoCameraError(msg)
    graph = WallGraph.from_level(level)
    walls = graph.walls_union() if graph.walls else Polygon()
    free = polygon.difference(walls)
    furniture = [e for e in level.elements.values() if isinstance(e, Furniture) and e.visible]
    if target is not None:
        furniture = [
            f
            for f in furniture
            if polygon.contains(Point(f.transform.position.x, f.transform.position.y))
        ]
    blocked = [_footprint(f, FURNITURE_MARGIN_M) for f in furniture if f.category not in RUGS]
    blocked += [
        stair_layout(e).opening.buffer(0.15)
        for e in level.elements.values()
        if isinstance(e, Stairs) and e.visible
    ]
    stand = free.buffer(-WALL_MARGIN_M)
    if blocked:
        stand = stand.difference(unary_union(blocked))
    roofs = list(derived_roofs(level, graph).values())
    tall = [
        _footprint(f, 0.05)
        for f in furniture
        if f.bbox.height * f.transform.scale > TALL_FURNITURE_M
    ]
    sight = free.difference(unary_union(tall)) if tall else free
    return _Room(level, polygon, free, stand, roofs, furniture, sight)


def _front(f: Furniture) -> tuple[float, float]:
    return -math.sin(f.transform.yaw), math.cos(f.transform.yaw)


def _rotate(v: tuple[float, float], deg: float) -> tuple[float, float]:
    a = math.radians(deg)
    return v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a)


# ------------------------------------------------------------------ ensemble
def _arrival(level: Level, room: _Room) -> tuple[float, float, float, float, str] | None:
    """Où l'on entre dans la pièce et dans quel sens : haut de l'escalier (sens de la montée),
    sinon la porte (vers l'intérieur)."""
    for e in level.elements.values():
        if not isinstance(e, Stairs) or not e.visible:
            continue
        walk = stair_layout(e).walk
        (x0, y0), (x1, y1) = walk[-2], walk[-1]
        n = math.hypot(x1 - x0, y1 - y0) or 1.0
        return x1, y1, (x1 - x0) / n, (y1 - y0) / n, "en haut de l'escalier"
    graph = WallGraph.from_level(level)
    for e in level.elements.values():
        if not (isinstance(e, Opening) and e.kind in DOOR_KINDS and e.host_wall in graph.walls):
            continue
        a, b = graph.wall_axis(e.host_wall)
        length = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        t = e.offset + e.width / 2.0
        cx, cy = a.x + ux * t, a.y + uy * t
        for nx, ny in ((-uy, ux), (uy, -ux)):
            if room.polygon.contains(Point(cx + nx * 0.5, cy + ny * 0.5)):
                return cx, cy, nx, ny, "à la porte"
    return None


OVERALL_HALF_FOV = math.atan(640.0 / ARCHITECT_FOCAL_PX)
FOREGROUND_M = 1.3
"""Un meuble plus proche que ça de l'objectif, dans le cadre, est coupé par le bas de l'image."""
FOREGROUND_PENALTY = 12.0
"""Plus fort que l'écart de profondeur entre deux places voisines : on avance d'un pas plutôt
que de garder un fauteuil coupé par le bas du cadre (photo 2, H1b)."""
BUILT_IN_IN_VIEW_BONUS = 2.0


def _foreground(room: _Room, x: float, y: float, yaw: float) -> int:
    """Meubles (hors tapis) dont l'emprise entre dans le cadre à moins de 1,30 m."""
    half = OVERALL_HALF_FOV
    near = Polygon(
        [
            (x, y),
            (x + FOREGROUND_M * math.cos(yaw - half), y + FOREGROUND_M * math.sin(yaw - half)),
            (x + FOREGROUND_M * math.cos(yaw), y + FOREGROUND_M * math.sin(yaw)),
            (x + FOREGROUND_M * math.cos(yaw + half), y + FOREGROUND_M * math.sin(yaw + half)),
        ]
    )
    return sum(
        1 for f in room.furniture if f.category not in RUGS and _footprint(f).intersects(near)
    )


EDGE_CUT_PENALTY = 3.0
EDGE_REACH_M = 5.0
"""H5 : un meuble coupé par un bord latéral du cadre, à moins de 5 m, se lit comme une erreur
de cadrage (lit coupé à droite, fauteuil à gauche)."""


def _edge_cut(room: _Room, x: float, y: float, yaw: float) -> int:
    """Meubles (hors tapis) que les bords gauche et droit du cadre traversent."""
    edges = [
        LineString(
            [
                (x, y),
                (
                    x + EDGE_REACH_M * math.cos(yaw + s * OVERALL_HALF_FOV),
                    y + EDGE_REACH_M * math.sin(yaw + s * OVERALL_HALF_FOV),
                ),
            ]
        )
        for s in (-1.0, 1.0)
    ]
    return sum(
        1
        for f in room.furniture
        if f.category not in RUGS and any(_footprint(f).intersects(e) for e in edges)
    )


def _built_in_in_view(room: _Room, x: float, y: float, yaw: float) -> int:
    """Lits encastrés dont le centre est dans le cadre et visible depuis la caméra."""
    count = 0
    for f in room.furniture:
        if f.category != "bed_builtin":
            continue
        fx, fy = f.transform.position.x, f.transform.position.y
        if _in_frame(x, y, yaw, OVERALL_HALF_FOV * 0.8, (fx, fy)) and room.sees((x, y), (fx, fy)):
            count += 1
    return count


ARRIVAL_STEPS_M = [0.5 + 0.25 * k for k in range(11)]
"""Reculs essayés depuis le point d'arrivée (de 0,5 à 3 m dans le sens de l'entrée)."""


def _overall(project: Project, level: Level, room: _Room, *, arrival: bool = True) -> ViewPlan:
    entry = _arrival(level, room) if arrival else None
    if entry is None:
        # Ni escalier ni porte : la vue d'architecte (au bout de l'axe de la pièce) en tient lieu.
        op = architect_view(project, level.id)
        return ViewPlan(
            role="overall",
            name="Vue d'ensemble",
            x=op.position.x,
            y=op.position.y,
            z=op.height,
            yaw=op.yaw,
            pitch=op.pitch,
            focal_px=op.focal_px or ARCHITECT_FOCAL_PX,
            note="dans l'axe de la pièce (ni escalier ni porte dessinés), grand angle",
        )
    bx, by, dx, dy, where = entry
    base = math.atan2(dy, dx)
    # Près de l'arrivée, la place et le sens qui montrent le plus de pièce : profondeur sur
    # toute la largeur du cadre (un mur proche ou un lampadaire devant l'objectif la coupent),
    # un peu de préférence pour le sens de l'entrée et pour rester près de l'arrivée.
    best: tuple[float, float, float, float] | None = None
    for k in ARRIVAL_STEPS_M:
        x, y = bx + dx * k, by + dy * k
        if room.height_at(x, y, EYE_M) is None:
            continue
        for deg in range(-180, 180, 10):
            a = base + math.radians(deg)
            depth = room.cone(x, y, a, OVERALL_HALF_FOV, furniture=True)
            score = (sum(depth) / 5.0 + min(depth)) * (0.85 + 0.15 * math.cos(a - base))
            score -= 0.3 * k
            # H1b : pas de meuble coupé au premier plan ; le lit encastré dans le cadre.
            score -= FOREGROUND_PENALTY * _foreground(room, x, y, a)
            score -= EDGE_CUT_PENALTY * _edge_cut(room, x, y, a)
            score += BUILT_IN_IN_VIEW_BONUS * _built_in_in_view(room, x, y, a)
            if best is None or score > best[0]:
                best = (score, x, y, a)
    if best is None:
        return _overall(project, level, room, arrival=False)
    _, x, y, yaw = best
    z = room.height_at(x, y, EYE_M) or EYE_M
    return ViewPlan(
        role="overall",
        name="Vue d'ensemble",
        x=x,
        y=y,
        z=z,
        yaw=yaw,
        pitch=math.radians(3.0) if room.roofs else 0.0,
        focal_px=ARCHITECT_FOCAL_PX,
        note=f"depuis l'arrivée ({where}), grand angle",
    )


# ------------------------------------------------------------------ zone et détail
def _aim(
    room: _Room,
    target: tuple[float, float],
    front: tuple[float, float],
    distance: float,
    height: float,
    ideal_deg: float,
    target_z: float,
    half: float,
    keep: list[Polygon],
) -> tuple[float, float, float, float, float] | None:
    """Meilleure place de caméra devant `front`, vers `distance` de la cible, à `ideal_deg` de
    l'axe (trois quarts) : (x, y, z, yaw, pitch), ou None si aucune place ne voit la cible
    avec un cadre dégagé (pas de mur ni de cloison devant l'objectif). `half` : demi-angle
    horizontal du cadre. Les emprises `keep` doivent tenir entières dans le cadre : on recule
    jusqu'à deux fois et demie la distance ; si rien n'y arrive, le meilleur cadre partiel."""
    corners = [p for shape in keep for p in shape.exterior.coords]
    best: tuple[float, tuple[float, float, float, float, float]] | None = None
    fallback: tuple[float, tuple[float, float, float, float, float]] | None = None
    for factor in AIM_FACTORS:
        d = distance * factor
        for off in range(-90, 91, 10):
            vx, vy = _rotate(front, off)
            x, y = target[0] + vx * d, target[1] + vy * d
            z = room.height_at(x, y, height)
            if z is None or not room.sees((x, y), target):
                continue
            yaw = math.atan2(target[1] - y, target[0] - x)
            if min(room.cone(x, y, yaw, half, furniture=False)) < CLEAR_FRAME * d:
                continue
            score = -abs(off - ideal_deg) / 10.0 - abs(factor - 1.0) * 3.0
            # H5 : pas de meuble étranger au sujet coupé au premier plan.
            score -= 2.0 * _near_others(room, x, y, yaw, keep)
            pose = (x, y, z, yaw, -math.atan2(z - target_z, d))
            fits = all(_in_frame(x, y, yaw, half * FRAME_MARGIN, p) for p in corners)
            if fits and (best is None or score > best[0]):
                best = (score, pose)
            if fallback is None or score > fallback[0]:
                fallback = (score, pose)
    chosen = best or fallback
    return None if chosen is None else chosen[1]


def _near_others(room: _Room, x: float, y: float, yaw: float, keep: list[Polygon]) -> int:
    """Meubles hors du sujet (ni dans `keep`, ni tapis) à moins de 1 m devant l'objectif."""
    reach = 1.0
    half = math.radians(35.0)
    cone = Polygon(
        [
            (x, y),
            (x + reach * math.cos(yaw - half), y + reach * math.sin(yaw - half)),
            (x + reach * math.cos(yaw + half), y + reach * math.sin(yaw + half)),
        ]
    )
    subject = unary_union(keep) if keep else None
    count = 0
    for f in room.furniture:
        if f.category in RUGS:
            continue
        shape = _footprint(f)
        if subject is not None and shape.intersects(subject):
            continue
        if shape.intersects(cone):
            count += 1
    return count


AIM_FACTORS = (1.0, 1.1, 1.2, 1.35, 1.5, 1.7, 1.9, 2.2, 2.5, 0.9, 0.8)
"""Distances essayées, en fraction de la distance visée : on recule avant d'avancer."""
FRAME_MARGIN = 0.95


def _in_frame(x: float, y: float, yaw: float, half: float, p: tuple[float, float]) -> bool:
    a = math.atan2(p[1] - y, p[0] - x) - yaw
    return abs(math.atan2(math.sin(a), math.cos(a))) <= half


def _half_fov(focal: float, aspect: str) -> float:
    width, _ = SIZES.get(aspect, SIZES["4:3"])
    return math.atan(width / 2.0 / focal)


ZONE_NAMES: dict[str, str] = {
    "detente": "détente",
    "lecture": "lecture",
    "bureau": "bureau",
    "couchage": "couchage",
}
"""Zones cadrées par la vue de zone, dans l'ordre (le rangement n'a pas de vue)."""


def _zone_groups(room: _Room) -> list[tuple[str, Furniture, list[Furniture]]]:
    """(nom, meuble principal, meubles) de chaque zone : d'après la zone écrite dans les
    meubles posés par le brief, sinon par proximité autour d'un meuble principal."""
    out: list[tuple[str, Furniture, list[Furniture]]] = []
    for zone, label in ZONE_NAMES.items():
        members = [f for f in room.furniture if f.metadata.get(ZONE_KEY) == zone]
        if not members:
            continue
        mains = next(c for n, c in ZONE_ANCHORS if n == label)
        anchor = next(
            (f for f in members if f.category in mains),
            max(members, key=lambda f: f.bbox.width * f.bbox.depth),
        )
        out.append((label, anchor, members))
    if out:
        return out
    for label, categories in ZONE_ANCHORS:
        for anchor in (f for f in room.furniture if f.category in categories):
            ax, ay = anchor.transform.position.x, anchor.transform.position.y
            group = [
                f
                for f in room.furniture
                if math.hypot(f.transform.position.x - ax, f.transform.position.y - ay) <= 2.0
            ]
            out.append((label, anchor, group))
    return out


def _zone(room: _Room, aspect: str, prefer: str | None = None) -> ViewPlan | None:
    half = _half_fov(focal_px(ZONE_FOCAL_MM), aspect)
    groups = sorted(_zone_groups(room), key=lambda g: g[0] != prefer)
    for label, anchor, members in groups:
        solid = [_footprint(f) for f in members if f.category not in RUGS] or [
            _footprint(f) for f in members
        ]
        c = unary_union(solid).centroid
        target = (c.x, c.y)
        aimed = _aim(
            room,
            target,
            _front(anchor),
            ZONE_DISTANCE_M,
            ZONE_HEIGHT_M,
            ZONE_ANGLE_DEG,
            0.55,
            half,
            solid,
        )
        if aimed is not None:
            x, y, z, yaw, pitch = aimed
            return ViewPlan(
                role="zone",
                name=f"Vue de zone : {label}",
                x=x,
                y=y,
                z=z,
                yaw=yaw,
                pitch=pitch,
                focal_px=focal_px(ZONE_FOCAL_MM),
                note=f"zone {label}, à {math.hypot(c.x - x, c.y - y):.1f} m, "
                f"{ZONE_FOCAL_MM:.0f} mm",
            )
    return None


def _head(main: Furniture) -> Polygon:
    """Ce que le détail doit montrer en entier du meuble principal : la tête d'un lit (son
    premier mètre, oreillers et tête de lit), sinon tout le meuble."""
    if main.category not in BEDS:
        return _footprint(main)
    t = main.transform
    w, d = main.bbox.width * t.scale / 2.0, main.bbox.depth * t.scale / 2.0
    c, s = math.cos(t.yaw), math.sin(t.yaw)
    near = min(2.0 * d, DETAIL_HEAD_M)
    return Polygon(
        [
            (t.position.x + c * dx - s * dy, t.position.y + s * dx + c * dy)
            for dx, dy in ((-w, -d), (w, -d), (w, -d + near), (-w, -d + near))
        ]
    )


BUILT_IN_DISTANCE_M = 2.5
BUILT_IN_HEIGHT_M = 1.2
BUILT_IN_ANGLE_DEG = 50.0
BUILT_IN_FOCAL_MM = 28.0


def _built_in_detail(room: _Room, bed: Furniture, aspect: str) -> ViewPlan | None:
    """Détail du lit encastré (H1b) : de trois quarts depuis le côté, à 2,5 m et 1,20 m de haut,
    pour lire à la fois le coffre et ses tiroirs, le matelas et la pente au-dessus de la tête
    (références 13 et 14)."""
    half = _half_fov(focal_px(BUILT_IN_FOCAL_MM), aspect)
    shape = _footprint(bed)
    c = shape.centroid
    for side in (1.0, -1.0):
        aimed = _aim(
            room,
            (c.x, c.y),
            _front(bed),
            BUILT_IN_DISTANCE_M,
            BUILT_IN_HEIGHT_M,
            side * BUILT_IN_ANGLE_DEG,
            0.65,
            half,
            [shape],
        )
        if aimed is None:
            continue
        x, y, z, yaw, pitch = aimed
        return ViewPlan(
            role="detail",
            name="Détail : lit encastré",
            x=x,
            y=y,
            z=z,
            yaw=yaw,
            pitch=pitch,
            focal_px=focal_px(BUILT_IN_FOCAL_MM),
            note=f"lit encastré de trois quarts, à {math.hypot(c.x - x, c.y - y):.1f} m, "
            f"{BUILT_IN_FOCAL_MM:.0f} mm",
        )
    return None


DETAIL_HEAD_M = 1.0
KITCHEN_REACH_M = 2.2
"""Un plan de cuisine est plus long qu'un lit et son chevet : évier et cuisson à 2,2 m au plus."""


def _detail(room: _Room, aspect: str, prefer: str | None = None) -> ViewPlan | None:
    half = _half_fov(focal_px(DETAIL_FOCAL_MM), aspect)
    pairs = sorted(DETAIL_PAIRS, key=lambda p: p[0] != prefer)
    for label, mains, companions in pairs:
        for main in (f for f in room.furniture if f.category in mains):
            if label == "lit encastré":
                view = _built_in_detail(room, main, aspect)
                if view is not None:
                    return view
                continue
            mx, my = main.transform.position.x, main.transform.position.y
            fx, fy = _front(main)
            depth = main.bbox.depth * main.transform.scale
            # Lit : la tête (oreillers) plutôt que le milieu ; sinon le centre du meuble.
            if main.category in BEDS:
                hx, hy = mx - fx * (depth / 2.0 - 0.45), my - fy * (depth / 2.0 - 0.45)
            else:
                hx, hy = mx, my
            near = [
                f
                for f in room.furniture
                if f.category in companions
                and f.id != main.id
                and math.hypot(f.transform.position.x - hx, f.transform.position.y - hy)
                <= (KITCHEN_REACH_M if label == "cuisine" else 1.6)
            ]
            if not near and companions:
                continue
            if near:
                mate = min(
                    near,
                    key=lambda f: math.hypot(
                        f.transform.position.x - hx, f.transform.position.y - hy
                    ),
                )
                nx, ny = mate.transform.position.x, mate.transform.position.y
                # Cadre : toute la tête du lit et le chevet, centrés.
                shapes = [_head(main), _footprint(mate)]
            else:
                nx, ny = mx + math.cos(main.transform.yaw), my + math.sin(main.transform.yaw)
                shapes = [_head(main)]
            c = unary_union(shapes).envelope.centroid
            target = (c.x, c.y)
            lateral = (math.cos(main.transform.yaw), math.sin(main.transform.yaw))
            side = 1.0 if (nx - mx) * lateral[0] + (ny - my) * lateral[1] >= 0 else -1.0
            aimed = _aim(
                room,
                target,
                (fx, fy),
                DETAIL_DISTANCE_M,
                DETAIL_HEIGHT_M,
                -side * DETAIL_ANGLE_DEG,
                0.5,
                half,
                shapes,
            )
            if aimed is None:
                continue
            x, y, z, yaw, pitch = aimed
            return ViewPlan(
                role="detail",
                name=f"Détail : {label}",
                x=x,
                y=y,
                z=z,
                yaw=yaw,
                pitch=pitch,
                focal_px=focal_px(DETAIL_FOCAL_MM),
                note=f"{label}, à {math.hypot(target[0] - x, target[1] - y):.1f} m, "
                f"{DETAIL_FOCAL_MM:.0f} mm",
            )
    return None


# ------------------------------------------------------------------ trois vues distinctes
DISTINCT_YAW_DEG = 35.0
DISTINCT_POS_M = 1.0
"""Critère du PO (16 octobre) : deux vues automatiques diffèrent d'au moins 35° d'orientation
ou de 1,0 m de position ; sinon elles donneraient deux images quasi identiques."""
MIN_VIEWS = 3
ALT_STEP_M = 0.5
ALT_STEP_DEG = 15
ALT_FOCAL_MM = 24.0
ALT_MIN_DEPTH_M = 1.5
"""Une vue de secours ne bute pas sur un mur proche : au moins 1,5 m visible sur tout le cadre."""
CONTRECHAMP_COS = 0.7
"""Au-delà de ~135° de la vue d'ensemble, la vue de secours est un contrechamp."""


def _yaw_gap_deg(a: float, b: float) -> float:
    d = a - b
    return abs(math.degrees(math.atan2(math.sin(d), math.cos(d))))


def distinct(a: ViewPlan, b: ViewPlan) -> bool:
    """Deux vues assez différentes pour ne pas donner la même image (critère du PO)."""
    return (
        _yaw_gap_deg(a.yaw, b.yaw) >= DISTINCT_YAW_DEG
        or math.hypot(a.x - b.x, a.y - b.y) >= DISTINCT_POS_M
    )


def _light_points(room: _Room) -> list[tuple[float, float]]:
    """Au sol, devant chaque fenêtre (à 0,3 m du mur, côté pièce) et sous chaque velux."""
    level = room.level
    graph = WallGraph.from_level(level)
    out: list[tuple[float, float]] = []
    for e in level.elements.values():
        if not (
            isinstance(e, Opening)
            and e.visible
            and e.kind in GLAZED_KINDS
            and e.host_wall in graph.walls
        ):
            continue
        a, b = graph.wall_axis(e.host_wall)
        length = math.hypot(b.x - a.x, b.y - a.y) or 1.0
        ux, uy = (b.x - a.x) / length, (b.y - a.y) / length
        t = e.offset + e.width / 2.0
        cx, cy = a.x + ux * t, a.y + uy * t
        for nx, ny in ((-uy, ux), (uy, -ux)):
            p = (cx + nx * 0.3, cy + ny * 0.3)
            if room.free.contains(Point(p)):
                out.append(p)
                break
    roofs = derived_roofs(level, graph)
    for e in level.elements.values():
        if not (isinstance(e, RoofWindow) and e.visible and e.roof in roofs):
            continue
        r = roofs[e.roof]
        ea, eb = r.eave
        corners = roof_window_footprint(
            (ea.x, ea.y),
            (eb.x, eb.y),
            (r.inward.x, r.inward.y),
            r.pitch,
            e.offset,
            e.distance,
            e.width,
            e.length,
        )
        if corners:
            out.append(
                (
                    sum(c[0] for c in corners) / len(corners),
                    sum(c[1] for c in corners) / len(corners),
                )
            )
    return out


def _alternate(room: _Room, taken: list[ViewPlan], aspect: str, role: Role) -> ViewPlan | None:
    """Vue de secours quand la zone ou le détail manque, ou se confond avec une autre vue (petit
    volume, une seule zone) : à hauteur d'œil, au moins 35° de chaque vue déjà posée, de
    préférence en contrechamp de la vue d'ensemble (depuis le fond de la pièce), sinon vers une
    fenêtre ou un velux ; jamais face à un mur proche ni avec un meuble coupé au premier plan."""
    if not taken or room.stand.is_empty:
        return None
    half = _half_fov(focal_px(ALT_FOCAL_MM), aspect)
    lights = _light_points(room)
    ref = taken[0]
    minx, miny, maxx, maxy = room.stand.bounds
    nx = int((maxx - minx) / ALT_STEP_M) + 1
    ny = int((maxy - miny) / ALT_STEP_M) + 1
    best: tuple[float, float, float, float, float, int] | None = None
    for i in range(nx):
        for j in range(ny):
            x, y = minx + i * ALT_STEP_M, miny + j * ALT_STEP_M
            if room.height_at(x, y, EYE_M) is None:
                continue
            for deg in range(0, 360, ALT_STEP_DEG):
                a = math.radians(deg)
                if any(_yaw_gap_deg(a, t.yaw) < DISTINCT_YAW_DEG for t in taken):
                    continue
                depth = room.cone(x, y, a, half, furniture=True)
                if min(depth) < ALT_MIN_DEPTH_M:
                    continue
                back = max(0.0, -math.cos(a - ref.yaw))
                lit = sum(
                    1 for p in lights if _in_frame(x, y, a, half * 0.8, p) and room.sees((x, y), p)
                )
                score = sum(depth) / 5.0 + min(depth) + 2.0 * back + 1.5 * min(lit, 2)
                score -= FOREGROUND_PENALTY * _foreground(room, x, y, a)
                score -= EDGE_CUT_PENALTY * _edge_cut(room, x, y, a)
                if best is None or score > best[0]:
                    best = (score, x, y, a, back, lit)
    if best is None:
        return None
    _, x, y, yaw, back, lit = best
    if back >= CONTRECHAMP_COS:
        name, note = "Contrechamp", "depuis le fond de la pièce, vers l'arrivée"
    elif lit:
        name, note = "Vers la lumière", "vers la fenêtre ou le velux"
    else:
        name, note = "Autre angle", "angle opposé, à hauteur d'œil"
    return ViewPlan(
        role=role,
        name=name,
        x=x,
        y=y,
        z=room.height_at(x, y, EYE_M) or EYE_M,
        yaw=yaw,
        pitch=math.radians(3.0) if room.roofs else 0.0,
        focal_px=focal_px(ALT_FOCAL_MM),
        note=f"{note}, {ALT_FOCAL_MM:.0f} mm (remplace une vue absente ou trop proche)",
    )


_SKIP_PREFIX: dict[str, str] = {"zone": "vue de zone", "detail": "détail"}


def _ensure_distinct(
    room: _Room,
    views: list[ViewPlan],
    skipped: list[str],
    aspect: str,
    wanted: ViewsIntent,
) -> tuple[list[ViewPlan], list[str]]:
    """Au moins trois vues deux à deux distinctes : une vue trop proche d'une précédente est
    retirée, puis chaque vue voulue qui manque (zone, détail) reçoit une vue de secours."""
    kept: list[ViewPlan] = []
    for v in views:
        twin = next((k for k in kept if not distinct(v, k)), None)
        if twin is not None:
            skipped.append(f"{v.name} : trop proche de « {twin.name} », remplacée")
            continue
        kept.append(v)
    for role in ("zone", "detail"):
        if len(kept) >= MIN_VIEWS:
            break
        if not getattr(wanted, role) or any(v.role == role for v in kept):
            continue
        alt = _alternate(room, kept, aspect, role)
        if alt is None:
            skipped.append("autre point de vue : aucune place assez différente des autres vues")
            break
        kept.append(alt)
        skipped = [s for s in skipped if not s.startswith(_SKIP_PREFIX[role])]
    kept.sort(key=lambda v: ROLES.index(v.role))
    return kept, skipped


MAX_RENDER_ALL = 8


def views_to_render(level: Level) -> list[Camera]:
    """« Rendre toutes les vues » : les vues automatiques (ensemble, zone, détail), puis les
    autres points de vue libres ; pas les caméras des photos ; huit au plus."""
    free = [e for e in level.elements.values() if isinstance(e, Camera) and e.image_id is None]
    auto = sorted(
        (c for c in free if c.metadata.get(AUTO_KEY) in ROLES),
        key=lambda c: ROLES.index(c.metadata[AUTO_KEY]),
    )
    others = [c for c in free if c.metadata.get(AUTO_KEY) not in ROLES]
    return [*auto, *others][:MAX_RENDER_ALL]


def _main_room(level: Level) -> Room | None:
    return main_room(level)


def _most_wanted(level: Level, main: Room) -> tuple[Room, str, str]:
    """(pièce, détail, zone) de la zone la plus demandée d'un plan généré : bureau, puis
    cuisine, puis lit (une autre chambre que la principale si possible)."""
    rooms = rooms_of(level)
    meta = main.metadata.get(GENERATED_KEY)
    programme = meta.get("programme") if isinstance(meta, dict) else None
    programme = programme if isinstance(programme, dict) else {}

    def with_zone(zone: str) -> Room | None:
        return next(
            (r for r in rooms if zone in (r.metadata.get(ZONES_KEY) or [])),  # type: ignore[operator]
            None,
        )

    office = next((r for r in rooms if r.room_type == "office"), None)
    kitchen = next((r for r in rooms if r.room_type == "kitchen"), None)
    if programme.get("office") == "piece" and office is not None:
        return office, "bureau et chaise", "bureau"
    if programme.get("office") == "coin" and (room := with_zone("bureau")) is not None:
        return room, "bureau et chaise", "bureau"
    if programme.get("kitchen") == "ouverte" and programme.get("kind") != "studio":
        return main, "cuisine", "cuisine"
    if kitchen is not None:
        return kitchen, "cuisine", "cuisine"
    beds = [r for r in rooms if r.room_type == "bedroom"]
    principal = principal_bedroom(level)
    other = next((r for r in beds if principal is None or r.id != principal.id), None)
    return other or principal or main, "lit et chevet", "couchage"


def _programme_views(
    project: Project, level: Level, aspect: str
) -> tuple[list[ViewPlan], list[str]]:
    """Plan généré (L3) : l'ensemble du séjour, une vue de la chambre principale, le détail
    de la zone la plus demandée ; une vue au plus par pièce au-delà du séjour."""
    main = main_room(level)
    assert main is not None
    out = [_overall(project, level, _room(level, main))]
    skipped: list[str] = []
    target, pair, zone = _most_wanted(level, main)
    bedroom = principal_bedroom(level)
    if bedroom is not None:
        seen = _room(level, bedroom)
        if target.id == bedroom.id:
            # la zone demandée est dans la chambre : sa vue la montre, le détail va au séjour
            view = _zone(seen, aspect, prefer=ZONE_NAMES.get(zone, zone))
            shown = view.name.split(" : ")[-1] if view is not None else zone
            name = f"{bedroom.name} : coin {shown}"
            target, pair = main, "cuisine"
        else:
            view = _overall(project, level, seen)
            name = f"{bedroom.name} : vue d'ensemble"
        if view is not None:
            out.append(replace(view, role="zone", name=name))
        else:
            skipped.append(f"{bedroom.name} : aucune place pour la caméra")
    elif (view := _zone(_room(level, main), aspect)) is not None:
        # studio : pas de chambre, la vue de zone reste dans la pièce de vie
        out.append(view)
    detail = _detail(_room(level, target), aspect, prefer=pair)
    if detail is None and target.id != main.id:
        detail = _detail(_room(level, main), aspect)
    if detail is not None:
        out.append(detail)
    else:
        skipped.append("détail : aucun meuble principal avec son voisin à cadrer")
    return out, skipped


def plan_views(
    project: Project,
    level_id: UUID,
    wanted: ViewsIntent | None = None,
    aspect: str | None = None,
) -> tuple[list[ViewPlan], list[str]]:
    """Les vues automatiques possibles dans la pièce, et la raison de chaque vue manquée ;
    `aspect` (sinon celui de `wanted`) règle la largeur du cadre à remplir."""
    wanted = wanted or ViewsIntent()
    aspect = aspect or wanted.aspect
    level = project.levels[level_id]
    if is_generated(level) and len(rooms_of(level)) > 1:
        main = main_room(level)
        views, skipped = _programme_views(project, level, aspect)
        if main is None:
            return views, skipped
        return _ensure_distinct(_room(level, main), views, skipped, aspect, wanted)
    room = _room(level)
    views, skipped = _single_room_views(project, level, room, wanted, aspect)
    return _ensure_distinct(room, views, skipped, aspect, wanted)


def _single_room_views(
    project: Project, level: Level, room: _Room, wanted: ViewsIntent, aspect: str
) -> tuple[list[ViewPlan], list[str]]:
    """Ensemble, zone et détail d'une pièce seule, tels que voulus par l'intention."""
    out: list[ViewPlan] = []
    skipped: list[str] = []
    if wanted.overall:
        out.append(_overall(project, level, room))
    if wanted.zone:
        if (v := _zone(room, aspect)) is not None:
            out.append(v)
        else:
            skipped.append("vue de zone : aucun canapé, fauteuil, bureau ni lit à cadrer")
    if wanted.detail:
        if (v := _detail(room, aspect)) is not None:
            out.append(v)
        else:
            skipped.append("détail : aucun lit avec chevet (ni fauteuil et lampadaire…)")
    return out, skipped


def apply_auto_views(
    project: Project, level_id: UUID, *, actor: str, aspect: str | None = None
) -> tuple[Project, AutoViews]:
    """Pose les vues automatiques (un seul pas d'historique, annulable). Les vues automatiques
    d'avant sont remplacées, sauf celles modifiées à la main ; `aspect` (4:3 ou 4:5) est
    d'abord écrit dans l'intention de la pièce."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise NoCameraError(msg)
    elements = dict(level.elements)
    room = _main_room(level)
    intent = room.intent if room is not None and room.intent is not None else DesignIntent()
    if aspect is not None and room is not None and room.intent is not None:
        views = intent.views.model_copy(update={"aspect": aspect}).model_dump(mode="json")
        intent, _ = merge_intent(intent, {"views": views}, by_user=is_user(actor))
        elements[room.id] = room.model_copy(update={"intent": intent})
    # Pièce sans intention (projet d'avant V1) : le format reste sur les vues, sans créer
    # d'intention (qui imposerait les matières du style).
    chosen = intent.views.aspect if aspect is None else aspect
    kept: list[str] = []
    for e in level.elements.values():
        if isinstance(e, Camera) and e.metadata.get(AUTO_KEY) in ROLES:
            if e.source is Source.USER_DEFINED:
                kept.append(str(e.metadata[AUTO_KEY]))
            else:
                del elements[e.id]
    plans, skipped = plan_views(project, level_id, intent.views, chosen)
    run_id = uuid4()
    added: list[UUID] = []
    for plan in plans:
        if plan.role in kept:
            continue
        camera = Camera(
            name=plan.name,
            position=Point3(x=round(plan.x, 3), y=round(plan.y, 3), z=round(plan.z, 3)),
            yaw=round(plan.yaw, 4),
            pitch=round(plan.pitch, 4),
            focal_px=round(plan.focal_px, 1),
            source=Source.GENERATED,
            confidence=0.8,
            provenance=Provenance(step=STEP, run_id=run_id),
            metadata={AUTO_KEY: plan.role, ASPECT_KEY: chosen, "note": plan.note},
        )
        elements[camera.id] = camera
        added.append(camera.id)
    new_level = level.model_copy(update={"elements": elements})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    committed = history.commit(
        project, after, actor=actor, label=f"Vues automatiques ({len(added)})"
    )
    names = {"overall": "vue d'ensemble", "zone": "vue de zone", "detail": "détail"}
    return committed, AutoViews(
        added=added,
        kept=[f"{names[k]} : modifiée à la main, gardée" for k in kept],
        skipped=skipped,
        aspect=chosen,
        notes=[f"{p.name} : {p.note}" for p in plans if p.role not in kept],
    )
