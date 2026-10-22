"""Étape « floorplan.depth » (roadmap 2.3) : profondeur → plans → polygone de pièce.

Le sol est le plan dominant dont la normale regarde vers le haut de l'image (comme pour les
combles) : il donne la verticale et la hauteur de prise de vue. Les murs sont les plans
verticaux ; le repère du plan est aligné sur le plus grand d'entre eux (hypothèse Manhattan :
murs perpendiculaires entre eux). Chaque mur vu devient une droite du plan (x = a ou y = b)
avec l'étendue de ses points le long de l'autre axe.

Le polygone : l'emprise des murs extrêmes, puis, quand deux murs parallèles du même côté sont
vus à des distances différentes sur des étendues disjointes (un renfoncement, une pièce en L),
le rectangle entre les deux est retiré. Les côtés qu'aucun mur ne ferme sont `inferred`,
placés comme dans la proposition mono-photo (derrière la caméra) ; les murs vus sont
`estimated`, avec un sigma d'échelle relative tant que la hauteur de prise de vue n'est pas
saisie. La hauteur saisie recale tout (l'échelle du modèle de profondeur est peu fiable).
"""

from __future__ import annotations

import itertools
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import Polygon, box
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
Axis = Literal["x", "y"]


@dataclass(frozen=True)
class WallObservation:
    """Un mur vu : droite `axis` = `offset` dans le repère de la pièce (caméra à l'origine),
    étendue de ses points le long de l'autre axe, soutien et résidu."""

    axis: Axis
    offset: float
    span: tuple[float, float]
    count: int
    rms_m: float

    @property
    def side(self) -> int:
        """+1 : devant/à droite de la caméra (coordonnée positive), −1 : derrière/à gauche."""
        return 1 if self.offset >= 0 else -1


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
) -> tuple[RoomObservation | None, list[FittedPlane]]:
    """Sol, repère et murs vus dans une carte de profondeur ; None sans sol."""
    points, pixels = backproject(depth, focal_px, principal_point, stride=STRIDE)
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
        if abs(nx) >= AXIS_ALIGNED:
            axis: Axis = "x"
            offset, other = -world.distance / nx, 1
        elif abs(ny) >= AXIS_ALIGNED:
            axis, offset, other = "y", -world.distance / ny, 0
        else:
            obs.warnings.append(
                f"Mur oblique ignoré ({math.degrees(math.atan2(ny, nx)):.0f}° du repère)."
            )
            continue
        lo, hi = dense_span(world_points[fitted.inliers, other])
        obs.walls.append(WallObservation(axis, float(offset), (lo, hi), fitted.count, fitted.rms_m))
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


def room_polygon(
    walls: list[WallObservation], assumptions: list[Assumption]
) -> tuple[Polygon, list[WallObservation]]:
    """Polygone rectiligne (caméra à l'origine) et murs retenus après dédoublonnage."""
    walls = _dedupe(walls)
    xs, _ = _bounds(walls, "x", "latéral", assumptions)
    ys, _ = _bounds(walls, "y", "de face", assumptions)
    shape: BaseGeometry = box(xs[0], ys[0], xs[1], ys[1])
    # Renfoncements : deux murs parallèles du même côté, étendues disjointes, le plus proche
    # ferme la pièce sur son étendue seulement.
    for axis in ("x", "y"):
        for side in (-1, 1):
            same = sorted(
                [w for w in walls if w.axis == axis and w.side == side],
                key=lambda w: abs(w.offset),
            )
            bounds_lo, bounds_hi = ys if axis == "x" else xs
            for near, far in itertools.pairwise(same):
                if abs(far.offset - near.offset) < MIN_NOTCH_M:
                    continue
                # Le renfoncement est la part de la largeur que le mur lointain ne couvre pas,
                # du côté du mur proche : borné par le bord de l'emprise et le début (ou la
                # fin) de l'étendue du mur lointain, bord net vu sans occultation.
                near_mid = (near.span[0] + near.span[1]) / 2
                far_mid = (far.span[0] + far.span[1]) / 2
                lo, hi = (
                    (bounds_lo, far.span[0]) if near_mid < far_mid else (far.span[1], bounds_hi)
                )
                if hi - lo < MIN_NOTCH_M:
                    continue
                a, b = min(near.offset, far.offset), max(near.offset, far.offset)
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
    if not isinstance(shape, Polygon):
        shape = box(xs[0], ys[0], xs[1], ys[1])
    return shape.simplify(1e-6), walls


def _matches(a: Point2, b: Point2, wall: WallObservation, tol: float) -> bool:
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
    assumptions = [Assumption(text="Murs supposés perpendiculaires (Manhattan).", impact="medium")]
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
    scaled = [
        WallObservation(
            w.axis, w.offset * scale, (w.span[0] * scale, w.span[1] * scale), w.count, w.rms_m
        )
        for w in observation.walls
    ]
    polygon, kept = room_polygon(scaled, assumptions)
    minx, miny, _, _ = polygon.bounds
    shift = (-minx, -miny)
    ring = [Point2(x=x + shift[0], y=y + shift[1]) for x, y in list(polygon.exterior.coords)[:-1]]
    if polygon.exterior.is_ccw is False:
        ring.reverse()
    seen = [
        WallObservation(
            w.axis, w.offset + (shift[0] if w.axis == "x" else shift[1]), w.span, w.count, w.rms_m
        )
        for w in kept
    ]
    tol = 1e-3
    provenance = Provenance(image_ids=[image_id], step=STEP, model=MODEL_ID, run_id=run_id)
    vertices: list[Vertex] = []
    walls: list[Wall] = []
    n = len(ring)
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
                uncertainty=Uncertainty(sigma=_sigma(touching, scale != 1.0)),
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
                        (SCALE_SIGMA * distance if scale == 1.0 else 0.05) + support.rms_m
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
                {"axis": w.axis, "offset_m": w.offset, "span_m": list(w.span), "count": w.count}
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
    )


def _sigma(touching: list[WallObservation], rescaled: bool) -> float | None:
    if not touching:
        return None
    base = 0.05 if rescaled else SCALE_SIGMA * max(abs(w.offset) for w in touching)
    return base + max(w.rms_m for w in touching)


def _wall_distance(a: Point2, b: Point2, shift: tuple[float, float]) -> float:
    """Distance de la caméra (à `shift`) à la droite du mur, en mètres."""
    if abs(a.x - b.x) < 1e-9:
        return abs(a.x - shift[0])
    return abs(a.y - shift[1])


def _wall_name(a: Point2, b: Point2, polygon: Polygon, shift: tuple[float, float]) -> str:
    """« Mur haut/bas/gauche/droit » d'après la position par rapport au centre ; les murs
    d'un renfoncement sont numérotés."""
    cx, cy = polygon.centroid.x + shift[0], polygon.centroid.y + shift[1]
    mx, my = (a.x + b.x) / 2, (a.y + b.y) / 2
    if abs(a.x - b.x) < 1e-9:
        return "Mur gauche" if mx < cx else "Mur droit"
    return "Mur bas" if my < cy else "Mur haut"
