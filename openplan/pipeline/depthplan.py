"""Étape « floorplan.depth » (roadmap 2.3) : profondeur → plans → polygone de pièce.

Le sol est le plan dominant dont la normale regarde vers le haut de l'image (comme pour les
combles) : il donne la verticale et la hauteur de prise de vue. Les murs sont les plans
verticaux ; le repère du plan est celui qui aligne le plus de points de murs (KAN-87). Chaque
mur vu devient une droite quelconque du plan (normale, distance, étendue le long de la droite),
rattachée à l'axe le plus proche (x = a ou y = b, étendue le long de l'autre axe).

Le polygone : l'emprise des murs extrêmes, puis, quand deux murs parallèles du même côté sont
vus à des distances différentes sur des étendues disjointes (un renfoncement, une pièce en L),
le rectangle entre les deux est retiré. Un mur vu de biais (au moins 4° de son axe) coupe la
pièce par son demi-plan s'il est assez soutenu (points, longueur, hauteur), assez proche pour
que la profondeur apprise ne l'ait pas courbé (2,5 hauteurs de prise de vue) et qu'aucun autre
mur n'est vu derrière lui (un meuble ne déforme pas la pièce) ; sinon il est posé sur son axe
(hypothèse Manhattan, comme avant) ou écarté. Les côtés qu'aucun mur ne ferme sont `inferred`,
placés comme dans la proposition mono-photo (derrière la caméra) ; les murs vus sont
`estimated`, avec un sigma d'échelle relative tant que la hauteur de prise de vue n'est pas
saisie. La hauteur saisie recale tout (l'échelle du modèle de profondeur est peu fiable).
"""

from __future__ import annotations

import itertools
import json
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Point, Polygon, box
from shapely.geometry.base import BaseGeometry

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import DepthEstimator
from openplan.geom.planes import FittedPlane, Float, backproject, fit_planes, rotate_plane
from openplan.pipeline.attic import (
    SCALE_SIGMA,
    SLOPE_MAX_TILT,
    STRIDE,
    attic_axes,
    find_floor,
    lower_floor,
)
from openplan.pipeline.floorplan import (
    ACTOR,
    HIDDEN_WALL_MARGIN_M,
    INFERRED_CONFIDENCE,
    UNKNOWN_HALF_WIDTH_M,
    DetectionError,
)
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.scene import history
from openplan.scene.ops import detect_rooms
from openplan.schemas.common import Point2, Point3, Provenance, Source, StrictModel, Uncertainty
from openplan.schemas.elements import Camera, Vertex, Wall
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "floorplan.depth"
MODEL_ID = "depth_planes@0.1"
AXIS_ALIGNED = 0.9
"""|composante| minimale de la normale d'un mur pour compter comme aligné sur un axe."""
SPAN_BIN_M = 0.10
SPAN_MIN_DENSITY = 0.10
"""Étendue d'un mur : tranches de 10 cm peuplées à au moins 10 % de la plus peuplée."""
SAME_WALL_OVERLAP = 0.3
"""Deux murs parallèles du même côté dont les étendues se recouvrent plus que cette fraction de
la plus petite sont le même mur vu deux fois (le mieux soutenu gagne), pas un renfoncement."""
MIN_NOTCH_M = 0.25
"""Renfoncement plus petit que cela : bruit, ignoré."""
WALL_CONFIDENCE = 0.5
MIN_OBLIQUE_DEG = 4.0
"""Mur à moins de 4° de son axe : posé sur l'axe (Manhattan), comme avant."""
OBLIQUE_MIN_SHARE = 0.05
"""Mur oblique gardé en biais : au moins 5 % des points de murs…"""
OBLIQUE_MIN_LENGTH_M = 0.5
"""… au moins 50 cm de long…"""
OBLIQUE_MIN_HEIGHT_M = 1.2
"""… vu sur au moins 1,20 m de haut (un lit, un bureau, une commode sont plus bas)…"""
OBLIQUE_MAX_HEIGHTS = 2.5
"""… à au plus 2,5 hauteurs de prise de vue de la caméra : plus loin, la profondeur apprise
courbe les murs de quelques degrés (pièce en L rendue par Blender : mur droit vu à 7,7° à
3,3 hauteurs), son angle ne se juge pas…"""
OBLIQUE_BEHIND_TOL_M = 0.3
"""… et aucun autre mur vu à plus de 30 cm derrière lui : c'est alors un meuble ou une cloison
basse devant le vrai mur, pas le bord de la pièce."""
FAR_M = 100.0
"""Borne provisoire d'un côté fermé par un mur oblique, avant la coupe par son demi-plan."""
Axis = Literal["x", "y"]


@dataclass(frozen=True)
class WallObservation:
    """Un mur vu : droite `axis` = `offset` dans le repère de la pièce (caméra à l'origine),
    étendue de ses points le long de l'autre axe, soutien et résidu. `normal` (unitaire, vers
    l'extérieur) et `distance` donnent la droite vraie `normal · p = distance`, `along` son
    étendue le long de la tangente (−ny, nx) et `height_m` la hauteur vue ; `normal` None :
    mur posé sur son axe."""

    axis: Axis
    offset: float
    span: tuple[float, float]
    count: int
    rms_m: float
    normal: tuple[float, float] | None = None
    distance: float = 0.0
    along: tuple[float, float] = (0.0, 0.0)
    height_m: float = 0.0
    heights: float = 0.0
    """Distance à la caméra en hauteurs de prise de vue (sans unité : l'échelle n'y change rien)."""

    @property
    def side(self) -> int:
        """+1 : devant/à droite de la caméra (coordonnée positive), −1 : derrière/à gauche."""
        return 1 if self.offset >= 0 else -1

    @property
    def tilt_deg(self) -> float:
        """Écart de la droite vraie à son axe, en degrés (0 pour un mur posé sur l'axe)."""
        if self.normal is None:
            return 0.0
        c = abs(self.normal[0] if self.axis == "x" else self.normal[1])
        return math.degrees(math.acos(min(1.0, c)))

    @property
    def aligned(self) -> bool:
        """Assez proche de son axe pour y être posé (Manhattan) s'il n'est pas gardé en biais."""
        return self.normal is None or math.cos(math.radians(self.tilt_deg)) >= AXIS_ALIGNED

    def scaled(self, k: float) -> WallObservation:
        return replace(
            self,
            offset=self.offset * k,
            span=(self.span[0] * k, self.span[1] * k),
            distance=self.distance * k,
            along=(self.along[0] * k, self.along[1] * k),
            height_m=self.height_m * k,
        )

    def shifted(self, dx: float, dy: float) -> WallObservation:
        """Même mur, origine déplacée en (−dx, −dy) (le point p devient p + (dx, dy))."""
        x, y = (dx, dy) if self.axis == "x" else (dy, dx)
        moved = replace(self, offset=self.offset + x, span=(self.span[0] + y, self.span[1] + y))
        if self.normal is None:
            return moved
        nx, ny = self.normal
        t = -ny * dx + nx * dy
        return replace(
            moved,
            distance=self.distance + nx * dx + ny * dy,
            along=(self.along[0] + t, self.along[1] + t),
        )

    def posed(self) -> WallObservation:
        """Le mur posé sur son axe (droite vraie oubliée)."""
        return replace(self, normal=None)

    def segment(self) -> tuple[tuple[float, float], tuple[float, float]]:
        """Extrémités vues du mur."""
        if self.normal is None:
            if self.axis == "x":
                return (self.offset, self.span[0]), (self.offset, self.span[1])
            return (self.span[0], self.offset), (self.span[1], self.offset)
        nx, ny = self.normal
        ends = [(nx * self.distance - ny * s, ny * self.distance + nx * s) for s in self.along]
        return ends[0], ends[1]


@dataclass
class RoomObservation:
    camera_height_m: float
    axes: Float
    """Lignes = X, Y, haut, exprimés dans le repère caméra."""
    walls: list[WallObservation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def observe_room(
    depth: NDArray[np.float32] | Float,
    focal_px: float,
    principal_point: tuple[float, float],
    *,
    seed: int = 0,
    stride: int = STRIDE,
) -> tuple[RoomObservation | None, list[FittedPlane]]:
    """Sol, repère et murs vus dans une carte de profondeur ; None sans sol. `stride` (KAN-95) :
    un pixel sur `stride`, plus petit pour une profondeur gardée réduite."""
    points, pixels = backproject(depth, focal_px, principal_point, stride=stride)
    h, w = depth.shape
    planes = fit_planes(points, pixels, (w, h), seed=seed)
    floor_idx = find_floor(planes)
    if floor_idx is None:
        return None, planes
    planes, floor_idx = lower_floor(points, pixels, planes, floor_idx, h)  # KAN-70
    up = planes[floor_idx].plane.normal
    axes = room_axes(up, planes, floor_idx)
    obs = RoomObservation(camera_height_m=planes[floor_idx].plane.distance, axes=axes)
    world_points = points @ axes.T
    for i, fitted in enumerate(planes):
        if i == floor_idx:
            continue
        world = rotate_plane(fitted.plane, axes)
        nx, ny, nz = (float(v) for v in world.normal)
        if math.acos(min(1.0, abs(nz))) <= SLOPE_MAX_TILT:
            continue  # plafond, pente, autre : pas un mur
        # Droite vraie n · p = d (d > 0 : la caméra est du côté intérieur).
        flat = math.hypot(nx, ny)
        n2, d = (nx / flat, ny / flat), -float(world.distance) / flat
        if d < 0:
            n2, d = (-n2[0], -n2[1]), -d
        inliers = world_points[fitted.inliers]
        along = dense_span(inliers[:, :2] @ np.array([-n2[1], n2[0]]))
        z = inliers[:, 2]
        height = float(np.percentile(z, 95) - np.percentile(z, 5)) if z.size else 0.0
        axis: Axis = "x" if abs(nx) >= abs(ny) else "y"
        wall = WallObservation(
            axis,
            0.0,
            (0.0, 0.0),
            fitted.count,
            fitted.rms_m,
            n2,
            d,
            along,
            height,
            d / obs.camera_height_m if obs.camera_height_m > 0 else math.inf,
        )
        if wall.aligned:  # comme avant KAN-polygone : droite prise sur l'axe de la caméra
            other = 1 if axis == "x" else 0
            offset = -world.distance / (nx if axis == "x" else ny)
            span = dense_span(inliers[:, other])
        else:  # repli sur l'axe : le milieu de l'étendue vue
            (ax, ay), (bx, by) = wall.segment()
            offset = (ax + bx) / 2 if axis == "x" else (ay + by) / 2
            ends = sorted((ay, by) if axis == "x" else (ax, bx))
            span = (ends[0], ends[1])
        obs.walls.append(replace(wall, offset=float(offset), span=span))
    return obs, planes


def _aligned(normal: Float) -> bool:
    return abs(float(normal[0])) >= AXIS_ALIGNED or abs(float(normal[1])) >= AXIS_ALIGNED


def room_axes(up: Float, planes: list[FittedPlane], floor_idx: int) -> Float:
    """Repère de la pièce pour les murs (KAN-87) : celui qui aligne le plus de points de murs.
    Candidats : le repère des combles (`attic_axes`, aligné sur la pente la mieux soutenue,
    sinon sur le plus grand mur), puis celui de chaque plan vertical. Dans une chambre, une
    couette ou un meuble incliné passait pour une « pente » : le repère s'alignait dessus, tous
    les vrais murs devenaient obliques et étaient écartés, et le plan retombait sur les lignes
    de fuite (un couloir de 0,83 m). À soutien égal, le repère des combles est gardé (combles :
    les murs y sont parallèles au faîtage)."""
    walls = [
        i
        for i, p in enumerate(planes)
        if i != floor_idx and math.acos(min(1.0, abs(float(p.plane.normal @ up)))) > SLOPE_MAX_TILT
    ]

    def support(axes: Float) -> int:
        return sum(
            planes[i].count for i in walls if _aligned(rotate_plane(planes[i].plane, axes).normal)
        )

    best = attic_axes(up, planes, floor_idx)
    best_support = support(best)
    for i in sorted(walls, key=lambda i: -planes[i].count):
        x = planes[i].plane.normal - float(planes[i].plane.normal @ up) * up
        if np.linalg.norm(x) < 1e-6:
            continue
        x = x / np.linalg.norm(x)
        axes: Float = np.vstack([x, np.cross(up, x), up])
        found = support(axes)
        if found > best_support:
            best, best_support = axes, found
    return best


def dense_span(
    along: Float, bin_m: float = SPAN_BIN_M, fraction: float = SPAN_MIN_DENSITY
) -> tuple[float, float]:
    """Étendue du mur le long de son axe : la plus longue suite de tranches de `bin_m` assez
    peuplées (≥ `fraction` de la tranche la plus peuplée). Un plan vertical ramasse aussi une
    bande fine de points du plafond ou du sol le long de sa trace : peu de points par tranche,
    ils n'étirent plus l'étendue comme le feraient des percentiles."""
    if along.size == 0:
        return (0.0, 0.0)
    lo, hi = float(along.min()), float(along.max())
    bins = max(1, math.ceil((hi - lo) / bin_m))
    counts, edges = np.histogram(along, bins=bins, range=(lo, hi + 1e-9))
    keep = counts >= fraction * counts.max()
    best: tuple[int, int] | None = None
    start: int | None = None
    for i, ok in enumerate([*keep.tolist(), False]):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if best is None or i - start > best[1] - best[0]:
                best = (start, i)
            start = None
    assert best is not None
    return float(edges[best[0]]), float(edges[best[1]])


def _dedupe(walls: list[WallObservation]) -> list[WallObservation]:
    """Même axe, même côté, étendues qui se recouvrent : un seul mur (le mieux soutenu)."""
    kept: list[WallObservation] = []
    for wall in sorted(walls, key=lambda w: -w.count):
        duplicate = False
        for other in kept:
            if other.axis != wall.axis or other.side != wall.side:
                continue
            overlap = min(other.span[1], wall.span[1]) - max(other.span[0], wall.span[0])
            smallest = min(other.span[1] - other.span[0], wall.span[1] - wall.span[0])
            if smallest <= 0 or overlap > SAME_WALL_OVERLAP * smallest:
                duplicate = True
                break
        if not duplicate:
            kept.append(wall)
    return kept


def _bounds(
    walls: list[WallObservation], axis: Axis, label: str, assumptions: list[Assumption]
) -> tuple[tuple[float, float], tuple[WallObservation | None, WallObservation | None]]:
    """(min, max) sur un axe : mur le plus lointain de chaque côté, sinon supposé."""
    negative = [w for w in walls if w.axis == axis and w.side < 0]
    positive = [w for w in walls if w.axis == axis and w.side > 0]
    lo = min(negative, key=lambda w: w.offset) if negative else None
    hi = max(positive, key=lambda w: w.offset) if positive else None
    if lo is None and hi is None:
        assumptions.append(
            Assumption(
                text=f"Aucun mur {label} vu dans la profondeur : largeur supposée "
                f"{metres(2 * UNKNOWN_HALF_WIDTH_M, 1)} centrée sur la caméra.",
                impact="high",
            )
        )
        return (-UNKNOWN_HALF_WIDTH_M, UNKNOWN_HALF_WIDTH_M), (None, None)
    if lo is None or hi is None:
        assumptions.append(
            Assumption(
                text=f"Mur {label} opposé non vu : placé à {metres(HIDDEN_WALL_MARGIN_M, 1)} "
                "derrière la caméra.",
                impact="high",
            )
        )
    low = lo.offset if lo is not None else -HIDDEN_WALL_MARGIN_M
    high = hi.offset if hi is not None else HIDDEN_WALL_MARGIN_M
    return (low, high), (lo, hi)


def _oblique_refusal(wall: WallObservation, walls: list[WallObservation]) -> str | None:
    """Pourquoi un mur vu de biais ne coupe pas la pièce (None : il la coupe)."""
    if wall.count < OBLIQUE_MIN_SHARE * sum(w.count for w in walls):
        return "trop peu de points"
    if wall.along[1] - wall.along[0] < OBLIQUE_MIN_LENGTH_M:
        return "trop court"
    if wall.height_m < OBLIQUE_MIN_HEIGHT_M:
        return "trop bas, sans doute un meuble"
    if wall.heights > OBLIQUE_MAX_HEIGHTS:
        return "trop loin pour juger son angle dans la profondeur"
    assert wall.normal is not None
    nx, ny = wall.normal
    for other in walls:
        if other is not wall and any(
            nx * x + ny * y - wall.distance > OBLIQUE_BEHIND_TOL_M for x, y in other.segment()
        ):
            return "un autre mur est vu derrière lui, sans doute un meuble"
    return None


def _half_plane(wall: WallObservation) -> Polygon:
    """Côté intérieur (celui de la caméra) de la droite du mur, borné à `FAR_M`."""
    assert wall.normal is not None
    nx, ny = wall.normal
    px, py = nx * wall.distance, ny * wall.distance
    tx, ty = -ny * FAR_M, nx * FAR_M
    bx, by = -2 * nx * FAR_M, -2 * ny * FAR_M
    return Polygon(
        [
            (px + tx, py + ty),
            (px - tx, py - ty),
            (px - tx + bx, py - ty + by),
            (px + tx + bx, py + ty + by),
        ]
    )


def _shape(
    walls: list[WallObservation], assumptions: list[Assumption]
) -> tuple[BaseGeometry, tuple[float, float], tuple[float, float]]:
    """Emprise des murs extrêmes, coupée par les murs gardés en biais (`normal` non None),
    moins les renfoncements."""
    oblique = [w for w in walls if w.normal is not None]
    posed = [w.posed() for w in walls]
    xs, _ = _bounds(posed, "x", "latéral", assumptions)
    ys, _ = _bounds(posed, "y", "de face", assumptions)
    # Un côté fermé par un mur en biais : borne lointaine, la droite du mur la remplace.
    far = {(w.axis, w.side) for w in oblique}
    xs_cut = (-FAR_M if ("x", -1) in far else xs[0], FAR_M if ("x", 1) in far else xs[1])
    ys_cut = (-FAR_M if ("y", -1) in far else ys[0], FAR_M if ("y", 1) in far else ys[1])
    shape: BaseGeometry = box(xs_cut[0], ys_cut[0], xs_cut[1], ys_cut[1])
    for wall in oblique:
        shape = shape.intersection(_half_plane(wall))
    if oblique:
        minx, miny, maxx, maxy = shape.bounds
        xs, ys = (minx, maxx), (miny, maxy)
    # Renfoncements : deux murs parallèles du même côté, étendues disjointes, le plus proche
    # ferme la pièce sur son étendue seulement. Le plus proche est posé sur son axe (un mur vu
    # derrière lui l'empêche d'être gardé en biais) ; le lointain en biais va jusqu'à sa droite.
    for axis in ("x", "y"):
        for side in (-1, 1):
            same = sorted(
                [w for w in walls if w.axis == axis and w.side == side],
                key=lambda w: abs(w.offset),
            )
            bounds_lo, bounds_hi = ys if axis == "x" else xs
            for near, far_wall in itertools.pairwise(same):
                if near.normal is not None or abs(far_wall.offset - near.offset) < MIN_NOTCH_M:
                    continue
                # Le renfoncement est la part de la largeur que le mur lointain ne couvre pas,
                # du côté du mur proche : borné par le bord de l'emprise et le début (ou la
                # fin) de l'étendue du mur lointain, bord net vu sans occultation.
                near_mid = (near.span[0] + near.span[1]) / 2
                far_mid = (far_wall.span[0] + far_wall.span[1]) / 2
                lo, hi = (
                    (bounds_lo, far_wall.span[0])
                    if near_mid < far_mid
                    else (far_wall.span[1], bounds_hi)
                )
                if hi - lo < MIN_NOTCH_M:
                    continue
                reach = side * FAR_M if far_wall.normal is not None else far_wall.offset
                a, b = min(near.offset, reach), max(near.offset, reach)
                notch = box(a, lo, b, hi) if axis == "x" else box(lo, a, hi, b)
                candidate = shape.difference(notch)
                if isinstance(candidate, Polygon) and candidate.area > 0.5 * shape.area:
                    shape = candidate
                    assumptions.append(
                        Assumption(
                            text=f"Renfoncement vu sur l'axe {axis} : pièce non rectangulaire.",
                            impact="medium",
                        )
                    )
    return shape, xs, ys


def room_polygon(
    walls: list[WallObservation], assumptions: list[Assumption]
) -> tuple[Polygon, list[WallObservation]]:
    """Polygone (caméra à l'origine) et murs retenus après dédoublonnage : `normal` non None
    pour un mur gardé en biais, None pour un mur posé sur son axe."""
    walls = _dedupe(walls)
    kept: list[WallObservation] = []
    notes: list[Assumption] = []
    for wall in walls:
        why = None
        if wall.normal is not None and wall.tilt_deg >= MIN_OBLIQUE_DEG:
            why = _oblique_refusal(wall, walls)
            if why is None:
                kept.append(wall)
                continue
            if wall.aligned:
                notes.append(
                    Assumption(
                        text=f"Mur vu à {wall.tilt_deg:.0f}° du repère, posé d'équerre : {why}.",
                        impact="low",
                    )
                )
        if wall.aligned:
            kept.append(wall.posed())
        else:
            notes.append(
                Assumption(
                    text=f"Mur oblique ({wall.tilt_deg:.0f}° du repère) ignoré : {why}.",
                    impact="low",
                )
            )
    if any(w.normal is not None for w in kept):
        tried: list[Assumption] = []
        shape, _, _ = _shape(kept, tried)
        rectangle = box(*_shape([w.posed() for w in kept], [])[0].bounds)
        if (
            isinstance(shape, Polygon)
            and shape.contains(Point(0.0, 0.0))
            and shape.area >= 0.5 * rectangle.area
            and max(abs(v) for v in shape.bounds) < FAR_M / 2
        ):
            angles = ", ".join(f"{w.tilt_deg:.0f}°" for w in kept if w.normal is not None)
            assumptions.extend(notes)
            assumptions.append(
                Assumption(
                    text=f"Mur vu de biais ({angles} du repère) : pièce non rectangulaire.",
                    impact="medium",
                )
            )
            assumptions.extend(tried)
            return shape.simplify(1e-6), kept
        notes.append(
            Assumption(
                text="Murs vus de biais incohérents avec le reste de la pièce : posés d'équerre.",
                impact="low",
            )
        )
        kept = [k.posed() for k in kept if k.aligned]
    assumptions.extend(notes)
    shape, xs, ys = _shape(kept, assumptions)
    if not isinstance(shape, Polygon):
        shape = box(xs[0], ys[0], xs[1], ys[1])
    return shape.simplify(1e-6), kept


def _matches(a: Point2, b: Point2, wall: WallObservation, tol: float) -> bool:
    if wall.normal is not None:
        nx, ny = wall.normal
        return all(abs(nx * p.x + ny * p.y - wall.distance) < tol for p in (a, b))
    if wall.axis == "x":
        return abs(a.x - wall.offset) < tol and abs(b.x - wall.offset) < tol
    return abs(a.y - wall.offset) < tol and abs(b.y - wall.offset) < tol


def propose_floorplan_from_depth(
    project: Project,
    level_id: UUID,
    observation: RoomObservation,
    *,
    image_id: UUID,
    camera_height_m: float | None = None,
    camera_height_entered: bool = True,
    yaw: float = 0.0,
    pitch: float = 0.0,
    focal_px: float | None = None,
    run_id: UUID | None = None,
) -> tuple[Project, StepResult]:
    """Ajoute au niveau vide le polygone de pièce vu dans la profondeur et la caméra."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = "le niveau contient déjà des murs : la proposition ne s'applique qu'à un niveau vide"
        raise DetectionError(msg)
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise DetectionError(msg)
    if not observation.walls:
        return project, StepResult(
            step=STEP,
            confidence=0.0,
            warnings=[
                *observation.warnings,
                "Aucun mur vertical dans la profondeur : pas de plan.",
            ],
            duration_s=time.perf_counter() - started,
            model_ids=[MODEL_ID],
        )
    scale = 1.0
    assumptions: list[Assumption] = []
    if camera_height_m is not None and observation.camera_height_m > 0:
        scale = camera_height_m / observation.camera_height_m
        assumptions.append(
            Assumption(
                # KAN-62 : la hauteur par défaut de l'app n'est pas une saisie.
                text=f"Distances recalées sur la hauteur de prise de vue "
                f"{'saisie' if camera_height_entered else 'supposée (par défaut)'} "
                f"({metres(camera_height_m)}) : facteur {decimal(scale)} sur l'échelle du modèle.",
                impact="high",
            )
        )
    else:
        assumptions.append(
            Assumption(
                text=f"Échelle du modèle de profondeur gardée (hauteur de caméra trouvée "
                f"{metres(observation.camera_height_m)}) : ±{SCALE_SIGMA:.0%} attendu.",
                impact="high",
            )
        )
    polygon, kept = room_polygon([w.scaled(scale) for w in observation.walls], assumptions)
    if not kept:
        return project, StepResult(
            step=STEP,
            confidence=0.0,
            assumptions=assumptions,
            warnings=[*observation.warnings, "Aucun mur retenu dans la profondeur : pas de plan."],
            duration_s=time.perf_counter() - started,
            model_ids=[MODEL_ID],
        )
    assumptions.insert(
        0,
        Assumption(
            text="Murs supposés perpendiculaires (Manhattan)"
            + (", sauf ceux vus de biais." if any(w.normal for w in kept) else "."),
            impact="medium",
        ),
    )
    minx, miny, _, _ = polygon.bounds
    shift = (-minx, -miny)
    ring = [Point2(x=x + shift[0], y=y + shift[1]) for x, y in list(polygon.exterior.coords)[:-1]]
    if polygon.exterior.is_ccw is False:
        ring.reverse()
    seen = [w.shifted(*shift) for w in kept]
    tol = 1e-3
    provenance = Provenance(image_ids=[image_id], step=STEP, model=MODEL_ID, run_id=run_id)
    vertices: list[Vertex] = []
    walls: list[Wall] = []
    n = len(ring)
    # Sonar S1244 : égalité voulue, 1,0 exactement est « échelle non recalée » (valeur par défaut,
    # jamais calculée) ; tout autre facteur vient d'une mesure.
    unscaled = scale == 1.0  # NOSONAR
    supports: list[WallObservation | None] = []
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        supports.append(next((w for w in seen if _matches(a, b, w, tol)), None))
    for i, point in enumerate(ring):
        touching = [s for s in (supports[i - 1], supports[i]) if s is not None]
        vertices.append(
            Vertex(
                position=point,
                source=Source.ESTIMATED if touching else Source.INFERRED,
                confidence=WALL_CONFIDENCE if touching else INFERRED_CONFIDENCE,
                uncertainty=Uncertainty(sigma=_sigma(touching, not unscaled)),
            )
        )
    for i in range(n):
        support = supports[i]
        distance = _wall_distance(ring[i], ring[(i + 1) % n], shift)
        walls.append(
            Wall(
                name=_wall_name(ring[i], ring[(i + 1) % n], polygon, shift),
                start_vertex=vertices[i].id,
                end_vertex=vertices[(i + 1) % n].id,
                source=Source.ESTIMATED if support else Source.INFERRED,
                confidence=WALL_CONFIDENCE if support else INFERRED_CONFIDENCE,
                uncertainty=Uncertainty(
                    sigma=(
                        (SCALE_SIGMA * distance if unscaled else 0.05) + support.rms_m
                        if support
                        else None
                    )
                ),
                provenance=provenance,
            )
        )
    camera = Camera(
        name="Photo",
        position=Point3(x=shift[0], y=shift[1], z=(camera_height_m or observation.camera_height_m)),
        yaw=yaw,
        pitch=pitch,
        focal_px=focal_px,
        image_id=image_id,
        source=Source.ESTIMATED,
        confidence=WALL_CONFIDENCE,
        provenance=provenance,
    )
    new_level = level.model_copy(
        update={
            "vertices": {**level.vertices, **{v.id: v for v in vertices}},
            "elements": {**level.elements, **{w.id: w for w in walls}, camera.id: camera},
        }
    )
    new_level = detect_rooms(new_level)
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project, after, actor=ACTOR, label="Proposer un plan depuis la profondeur", run_id=run_id
    )
    estimated = [w for w in walls if w.source is Source.ESTIMATED]
    return after, StepResult(
        step=STEP,
        patch=after.history[-1].patch,
        confidence=min((w.confidence for w in estimated), default=0.0),
        assumptions=assumptions,
        warnings=[
            *observation.warnings,
            f"{len(estimated)} mur(s) vus dans la profondeur, {n - len(estimated)} supposé(s).",
        ],
        duration_s=time.perf_counter() - started,
        model_ids=[MODEL_ID],
    )


class DepthPlanRun(StrictModel):
    run_id: UUID
    project: Project
    camera_height_m: float | None
    """Hauteur de prise de vue trouvée dans la profondeur (échelle du modèle), None sans sol."""
    walls_seen: int
    result: StepResult
    reach: float | None = None
    """KAN-91 : portée de la photo, `depth_reach` ; None sans sol."""


def depth_reach(depth: NDArray[np.float32] | Float, camera_height_m: float) -> float | None:
    """Portée de la photo (KAN-91) : 90ᵉ centile de la profondeur, en hauteurs de prise de vue
    (sans unité : l'échelle du modèle s'annule). Une photo de la pièce voit loin (chambre 2,0,
    combles 2,7, pièce en L 3,5) ; un gros plan sur un meuble, non (bureau 1,2)."""
    valid = depth[np.isfinite(depth) & (depth > 0)]
    if valid.size == 0 or camera_height_m <= 0:
        return None
    return float(np.percentile(valid, 90)) / camera_height_m


def run_depthplan(
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    *,
    estimator: DepthEstimator,
    camera_height_m: float | None = None,
    camera_height_entered: bool = True,
    run_id: UUID | None = None,
) -> DepthPlanRun:
    """Photo → profondeur → plans → polygone proposé sur le niveau vide. La focale et le
    tangage viennent des points de fuite quand ils existent (sinon EXIF ou défaut) ; la
    profondeur est écrite dans les artefacts du run pour contrôle."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    camera = VanishingPointCameraEstimator().analyse(image)
    depth = estimator.predict(image).depth.astype(np.float32)
    observation, _planes = observe_room(
        depth, camera.camera.focal_px, camera.camera.principal_point
    )
    out_dir = runs_dir / str(rid) / "depthplan"
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "depth.npy", depth)
    if observation is None:
        return DepthPlanRun(
            run_id=rid,
            project=project,
            camera_height_m=None,
            walls_seen=0,
            result=StepResult(
                step=STEP,
                confidence=0.0,
                warnings=["Aucun plan de sol dans la profondeur : pas de plan proposé."],
                duration_s=time.perf_counter() - started,
                model_ids=[MODEL_ID],
            ),
        )
    after, result = propose_floorplan_from_depth(
        project,
        level_id,
        observation,
        image_id=image_id,
        camera_height_m=camera_height_m,
        camera_height_entered=camera_height_entered,
        yaw=camera.camera.yaw_hint or 0.0,
        pitch=camera.camera.pitch,
        focal_px=camera.camera.focal_px,
        run_id=rid,
    )
    (out_dir / "walls.json").write_text(
        json.dumps(
            [
                {
                    "axis": w.axis,
                    "offset_m": w.offset,
                    "span_m": list(w.span),
                    "count": w.count,
                    "tilt_deg": round(w.tilt_deg, 1),
                    "height_m": round(w.height_m, 2),
                }
                for w in observation.walls
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return DepthPlanRun(
        run_id=rid,
        project=after,
        camera_height_m=observation.camera_height_m,
        walls_seen=len(observation.walls),
        result=result,
        reach=depth_reach(depth, observation.camera_height_m),
    )


def _sigma(touching: list[WallObservation], rescaled: bool) -> float | None:
    if not touching:
        return None
    base = 0.05 if rescaled else SCALE_SIGMA * max(abs(w.offset) for w in touching)
    return base + max(w.rms_m for w in touching)


def _wall_distance(a: Point2, b: Point2, shift: tuple[float, float]) -> float:
    """Distance de la caméra (à `shift`) à la droite du mur, en mètres (mur oblique compris)."""
    dx, dy = b.x - a.x, b.y - a.y
    return abs(dx * (shift[1] - a.y) - dy * (shift[0] - a.x)) / math.hypot(dx, dy)


def _wall_name(a: Point2, b: Point2, polygon: Polygon, shift: tuple[float, float]) -> str:
    """« Mur haut/bas/gauche/droit » d'après la position par rapport au centre (un mur oblique
    est rangé d'après sa direction dominante) ; les murs d'un renfoncement sont numérotés."""
    cx, cy = polygon.centroid.x + shift[0], polygon.centroid.y + shift[1]
    mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
    if abs(a.x - b.x) < abs(a.y - b.y):
        return "Mur gauche" if mx < cx else "Mur droit"
    return "Mur bas" if my < cy else "Mur haut"
