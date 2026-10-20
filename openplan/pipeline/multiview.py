"""Étape « add_view » (roadmap 2.5a) : une photo de plus sur un niveau qui a déjà des murs.

Une seule scène : la nouvelle photo n'ajoute qu'une caméra « Photo n ». Ce qu'elle observe
(distance aux murs vus, largeur entre deux murs opposés vus) est confronté aux murs existants :

- un mur `inferred` (placé arbitrairement faute de le voir) que cette photo voit avec son
  vis-à-vis est déplacé et passe `estimated`, avec cette photo pour provenance ;
- un mur déjà estimé, ou posé par l'utilisateur, n'est jamais déplacé : l'écart est consigné
  dans les métadonnées de la caméra et signalé par la violation `inconsistent_views`
  (`validation.consistency`) ; l'utilisateur tranche.

Orientation : le repère Manhattan d'une photo n'est connu qu'à un quart de tour près. Les
quatre orientations sont essayées ; on retient celle qui place la caméra dans la pièce avec
le moindre écart aux murs (un écart à un mur inféré compte moins : ce mur n'est qu'une
supposition), et l'ambiguïté restante (pièce symétrique) est dite en hypothèse.

Limite assumée : tant qu'un mur reste inféré, une photo qui contredit les murs estimés (par
exemple prise à une autre hauteur que celle saisie) peut être lue comme « la pièce est plus
longue sur l'autre axe » et déplacer ce mur inféré au lieu d'être signalée ; la géométrie
seule ne distingue pas ces deux lectures. Une fois les quatre murs estimés, ou quand
l'utilisateur fixe l'orientation (« Tourner la vue », `rotate_view`), la contradiction est
signalée.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from pydantic import JsonValue

from openplan.adapters.heuristic.box_detector import BoxAnalysis, FloorEdge
from openplan.pipeline.floorplan import (
    ACTOR,
    DEFAULT_CAMERA_HEIGHT_M,
    ESTIMATED_SIGMA_M,
    DetectionError,
)
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.schemas.common import (
    USER_AUTHORITATIVE,
    Point3,
    Provenance,
    Source,
    Uncertainty,
    source_rank,
)
from openplan.schemas.elements import Camera, Vertex, Wall
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import Level, Project
from openplan.validation.consistency import (
    DISTANCES_KEY,
    EXTENTS_KEY,
    check_views,
    observed_extents,
    side_wall,
    wall_pair_label,
    walls_box,
)

STEP = "floorplan.add_view"
ADOPT_STEP = "floorplan.adopt_view"
ROTATE_STEP = "floorplan.rotate_view"
ORIENTATION_KEY = "orientation_quarter_turns"
AMBIGUOUS_KEY = "orientation_ambiguous"
USER_ORIENTED_KEY = "orientation_user_fixed"
"""Métadonnée de caméra : True quand l'utilisateur a tourné la vue lui-même (2.5c)."""
MODEL_ID = "heuristic_box@0.1"
OUTSIDE_MARGIN_M = 0.3
"""Une orientation qui met la caméra hors de la pièce (au-delà de cette marge) est écartée."""
INFERRED_PRIOR_WEIGHT = 0.3
"""Poids de l'écart à un mur inféré dans le choix de l'orientation : le mur n'est qu'une
supposition, mais il départage un axe long d'un axe court."""
OUTSIDE_PENALTY = 10.0
TIE_TOLERANCE = 1e-6
SceneAxis = Literal["x", "y"]
Side = Literal["min", "max"]


@dataclass(frozen=True)
class Observation:
    """Sur un axe : coordonnée (caméra à l'origine, en mètres) du mur derrière (`lo` < 0) et du
    mur devant (`hi` > 0), avec l'arête qui les soutient ; None quand ce côté n'est pas vu."""

    lo: float | None = None
    hi: float | None = None
    lo_edge: FloorEdge | None = None
    hi_edge: FloorEdge | None = None

    @property
    def extent(self) -> float | None:
        return None if self.lo is None or self.hi is None else self.hi - self.lo

    def flipped(self) -> Observation:
        return Observation(
            lo=None if self.hi is None else -self.hi,
            hi=None if self.lo is None else -self.lo,
            lo_edge=self.hi_edge,
            hi_edge=self.lo_edge,
        )


@dataclass
class AxisFit:
    position: float
    residual: float = 0.0
    moves: dict[Side, tuple[float, FloorEdge]] = field(default_factory=dict)
    """Murs inférés à déplacer : côté → (nouvelle coordonnée, arête qui l'a vue)."""


@dataclass
class Placement:
    quarter_turns: int
    fits: dict[SceneAxis, AxisFit]
    observations: dict[SceneAxis, Observation]

    @property
    def score(self) -> float:
        return sum(f.residual for f in self.fits.values())


def observe(edges: list[FloorEdge], h: float) -> Observation:
    """Même lecture que la proposition mono-photo : les deux arêtes extrêmes si elles
    encadrent la caméra, sinon la plus lointaine du côté vu."""
    if not edges:
        return Observation()
    scaled = sorted(((e.offset * h, e) for e in edges), key=lambda t: t[0])
    if len(scaled) >= 2 and scaled[0][0] < 0 < scaled[-1][0]:
        return Observation(scaled[0][0], scaled[-1][0], scaled[0][1], scaled[-1][1])
    offset, edge = scaled[-1] if scaled[-1][0] > 0 else scaled[0]
    if offset > 0:
        return Observation(hi=offset, hi_edge=edge)
    return Observation(lo=offset, lo_edge=edge)


def _rotate(obs_x: Observation, obs_y: Observation, k: int) -> dict[SceneAxis, Observation]:
    """Observations exprimées dans les axes de la scène après k quarts de tour antihoraires
    du repère de la photo : X→Y, Y→−X à chaque quart."""
    if k == 0:
        return {"x": obs_x, "y": obs_y}
    if k == 1:
        return {"x": obs_y.flipped(), "y": obs_x}
    if k == 2:
        return {"x": obs_x.flipped(), "y": obs_y.flipped()}
    return {"x": obs_y, "y": obs_x.flipped()}


def _adjustable(wall: Wall | None, level: Level) -> bool:
    """Un mur que le logiciel a le droit de déplacer : ni verrouillé, ni saisi ou mesuré par
    l'utilisateur (mur et sommets)."""
    if wall is None or wall.locked or wall.source in USER_AUTHORITATIVE:
        return False
    for vid in (wall.start_vertex, wall.end_vertex):
        vertex = level.vertices[vid]
        if vertex.locked or vertex.source in USER_AUTHORITATIVE:
            return False
    return True


def _movable(wall: Wall | None, level: Level) -> bool:
    """Mur inféré (jamais vu) que la confrontation automatique peut déplacer."""
    return wall is not None and wall.source is Source.INFERRED and _adjustable(wall, level)


def _moved_wall(
    level: Level,
    vertices: dict[UUID, Vertex],
    wall: Wall,
    axis: SceneAxis,
    coordinate: float,
    confidence: float,
    provenance: Provenance,
) -> Wall:
    """Déplace les deux sommets du mur sur la droite axis = coordinate ; le mur et ses
    sommets deviennent `estimated` avec la provenance donnée (modifie `vertices`)."""
    for vid in (wall.start_vertex, wall.end_vertex):
        old = vertices[vid]
        vertices[vid] = old.model_copy(
            update={
                "position": old.position.model_copy(update={axis: coordinate}),
                "source": Source.ESTIMATED,
                "confidence": confidence,
                "uncertainty": Uncertainty(sigma=ESTIMATED_SIGMA_M),
            }
        )
    del level  # signature commune ; le niveau n'est pas relu ici
    return wall.model_copy(
        update={
            "source": Source.ESTIMATED,
            "confidence": confidence,
            "uncertainty": Uncertainty(sigma=ESTIMATED_SIGMA_M),
            "provenance": provenance,
        }
    )


def adopt_view(project: Project, level_id: UUID, camera_id: UUID) -> tuple[Project, StepResult]:
    """« Garder cette photo » : sur chaque axe où la photo a vu deux murs opposés, le mur le
    moins sûr (rang de source le plus bas, puis confiance la plus faible) est déplacé pour que
    le plan suive la distance vue. Décision de l'utilisateur, donc acteur `user` ; refus clair
    si les deux murs font autorité (saisis, mesurés ou verrouillés)."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    camera = level.elements.get(camera_id)
    if not isinstance(camera, Camera) or camera.image_id is None:
        msg = "cette vue n'est pas la caméra d'une photo"
        raise DetectionError(msg)
    extents = observed_extents(camera)
    box = walls_box(level)
    if not extents or box is None:
        msg = "cette photo n'a pas vu deux murs opposés : rien à suivre"
        raise DetectionError(msg)
    bounds = {"x": (box[0], box[2]), "y": (box[1], box[3])}
    vertices = dict(level.vertices)
    elements = dict(level.elements)
    provenance = Provenance(image_ids=[camera.image_id], step=ADOPT_STEP, model=MODEL_ID)
    moved: list[str] = []
    for raw_axis, observed in extents.items():
        axis: SceneAxis = "x" if raw_axis == "x" else "y"
        lo, hi = bounds[axis]
        if abs((hi - lo) - observed) < 1e-3:
            continue
        candidates = [
            (side, side_wall(level, axis, value)) for side, value in (("min", lo), ("max", hi))
        ]
        adjustable = [(s, w) for s, w in candidates if w is not None and _adjustable(w, level)]
        if not adjustable:
            msg = (
                f"{wall_pair_label(level, axis)} font autorité (saisis, mesurés ou "
                "verrouillés) : déplacer un mur à la main ou écarter la photo"
            )
            raise DetectionError(msg)
        side, wall = min(adjustable, key=lambda t: (source_rank(t[1].source), t[1].confidence))
        coordinate = lo + observed if side == "max" else hi - observed
        elements[wall.id] = _moved_wall(
            level, vertices, wall, axis, coordinate, camera.confidence, provenance
        )
        moved.append(wall.name or str(wall.id))
    if not moved:
        return project, StepResult(
            step=ADOPT_STEP,
            confidence=camera.confidence,
            warnings=["Le plan suit déjà cette photo : rien à déplacer."],
            duration_s=time.perf_counter() - started,
        )
    new_level = level.model_copy(update={"vertices": vertices, "elements": elements})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project, after, actor=USER_ACTOR, label=f"Suivre la photo « {camera.name} »"
    )
    return after, StepResult(
        step=ADOPT_STEP,
        patch=after.history[-1].patch,
        confidence=camera.confidence,
        warnings=[f"Murs déplacés pour suivre la photo : {', '.join(moved)}."],
        duration_s=time.perf_counter() - started,
    )


def observations_from_metadata(camera: Camera) -> dict[SceneAxis, Observation]:
    """Ce que la photo a vu, relu depuis `observed_distances_m` (axes de la scène, caméra à
    l'origine) ; sans arêtes, donc sans pouvoir déplacer de mur."""
    raw = camera.metadata.get(DISTANCES_KEY)
    distances = raw if isinstance(raw, dict) else {}

    def value(key: str) -> float | None:
        v = distances.get(key)
        return float(v) if isinstance(v, int | float) and v > 0 else None

    out: dict[SceneAxis, Observation] = {}
    for axis in ("x", "y"):
        lo, hi = value(f"{axis}-"), value(f"{axis}+")
        out[axis] = Observation(lo=None if lo is None else -lo, hi=hi)
    return out


def rotate_view(
    project: Project, level_id: UUID, camera_id: UUID, quarter_turns: int = 1
) -> tuple[Project, StepResult]:
    """« Tourner la vue » : l'utilisateur corrige l'orientation retenue pour une photo (repère
    connu à un quart de tour près, pièce symétrique). Les observations de la caméra tournent
    d'autant, la caméra est replacée par rapport aux murs existants (aucun mur ne bouge), sa
    visée tourne, et la cohérence est recontrôlée : une orientation fausse se voit alors à la
    violation `inconsistent_views`. Décision de l'utilisateur : acteur `user`."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    camera = level.elements.get(camera_id)
    if not isinstance(camera, Camera) or camera.image_id is None:
        msg = "cette vue n'est pas la caméra d'une photo"
        raise DetectionError(msg)
    observed = observations_from_metadata(camera)
    if all(o.lo is None and o.hi is None for o in observed.values()):
        msg = (
            "cette photo n'a pas d'observation à tourner (c'est elle qui a placé les murs, "
            "ou elle n'a vu aucun mur)"
        )
        raise DetectionError(msg)
    box = walls_box(level)
    if box is None:
        msg = "le niveau n'a pas de murs"
        raise DetectionError(msg)
    k = quarter_turns % 4
    if k == 0:
        msg = "un nombre de quarts de tour multiple de 4 ne change rien"
        raise DetectionError(msg)
    rotated = _rotate(observed["x"], observed["y"], k)
    bounds = {"x": (box[0], box[2]), "y": (box[1], box[3])}
    fits = {
        axis: fit_axis(rotated[axis], *bounds[axis], movable_min=False, movable_max=False)
        for axis in ("x", "y")
    }
    yaw = camera.yaw + k * math.pi / 2.0
    yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    previous = camera.metadata.get(ORIENTATION_KEY)
    turns = (int(previous) if isinstance(previous, int) else 0) + k
    placement = Placement(turns % 4, fits, rotated)  # type: ignore[arg-type]
    metadata: dict[str, JsonValue] = {
        **camera.metadata,
        **_metadata(placement, camera.position.z, camera.metadata.get(AMBIGUOUS_KEY) is True),
        USER_ORIENTED_KEY: True,
    }
    turned = camera.model_copy(
        update={
            "position": Point3(x=fits["x"].position, y=fits["y"].position, z=camera.position.z),
            "yaw": yaw,
            "metadata": metadata,
        }
    )
    new_level = level.model_copy(update={"elements": {**level.elements, camera.id: turned}})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project, after, actor=USER_ACTOR, label=f"Tourner la vue « {camera.name} »"
    )
    warnings = [
        f"Vue tournée de {k} quart{'s' if k > 1 else ''} de tour : caméra en "
        f"({decimal(turned.position.x)} ; {decimal(turned.position.y)}), regarde vers "
        f"{math.degrees(yaw):.0f}°."
    ]
    if any(f.residual >= OUTSIDE_PENALTY for f in fits.values()):
        warnings.append(
            "Dans cette orientation, la caméra se retrouve hors de la pièce : orientation "
            "improbable, tourner encore."
        )
    warnings.extend(v.message for v in check_views(after) if camera.id in v.element_ids)
    return after, StepResult(
        step=ROTATE_STEP,
        patch=after.history[-1].patch,
        confidence=camera.confidence,
        warnings=warnings,
        duration_s=time.perf_counter() - started,
    )


def fit_axis(
    obs: Observation,
    box_min: float,
    box_max: float,
    *,
    movable_min: bool,
    movable_max: bool,
) -> AxisFit:
    """Position de la caméra sur l'axe et murs inférés à déplacer, d'après ce qui est vu."""
    width = max(box_max - box_min, 1e-9)
    center = (box_min + box_max) / 2.0
    fit: AxisFit
    if obs.lo is not None and obs.hi is not None and obs.lo_edge and obs.hi_edge:
        extent = obs.hi - obs.lo
        mismatch = abs(extent - width) / width
        if movable_min and not movable_max:
            fit = AxisFit(box_max - obs.hi, INFERRED_PRIOR_WEIGHT * mismatch)
            fit.moves["min"] = (fit.position + obs.lo, obs.lo_edge)
        elif movable_max and not movable_min:
            fit = AxisFit(box_min - obs.lo, INFERRED_PRIOR_WEIGHT * mismatch)
            fit.moves["max"] = (fit.position + obs.hi, obs.hi_edge)
        elif movable_min and movable_max:
            fit = AxisFit(center - (obs.lo + obs.hi) / 2.0, INFERRED_PRIOR_WEIGHT * mismatch)
            fit.moves["min"] = (fit.position + obs.lo, obs.lo_edge)
            fit.moves["max"] = (fit.position + obs.hi, obs.hi_edge)
        else:
            fit = AxisFit(((box_min - obs.lo) + (box_max - obs.hi)) / 2.0, mismatch)
    elif obs.lo is not None and obs.hi is not None:
        # Deux murs vus mais sans arêtes (observation relue depuis les métadonnées d'une
        # caméra) : la caméra est placée au mieux entre les deux, rien n'est déplacé.
        mismatch = abs((obs.hi - obs.lo) - width) / width
        fit = AxisFit(((box_min - obs.lo) + (box_max - obs.hi)) / 2.0, mismatch)
    elif obs.hi is not None:
        fit = AxisFit(box_max - obs.hi)
    elif obs.lo is not None:
        fit = AxisFit(box_min - obs.lo)
    else:
        fit = AxisFit(center)
    if fit.position < box_min - OUTSIDE_MARGIN_M or fit.position > box_max + OUTSIDE_MARGIN_M:
        fit.residual += OUTSIDE_PENALTY
    return fit


def best_placement(level: Level, obs_x: Observation, obs_y: Observation) -> tuple[Placement, bool]:
    """Meilleure des quatre orientations ; le booléen dit si une autre fait aussi bien."""
    box = walls_box(level)
    assert box is not None
    bounds = {"x": (box[0], box[2]), "y": (box[1], box[3])}
    placements: list[Placement] = []
    for k in range(4):
        rotated = _rotate(obs_x, obs_y, k)
        fits: dict[SceneAxis, AxisFit] = {}
        for axis in ("x", "y"):
            lo, hi = bounds[axis]
            fits[axis] = fit_axis(
                rotated[axis],
                lo,
                hi,
                movable_min=_movable(side_wall(level, axis, lo), level),
                movable_max=_movable(side_wall(level, axis, hi), level),
            )
        placements.append(Placement(k, fits, rotated))
    best = min(placements, key=lambda p: p.score)
    ambiguous = sum(1 for p in placements if p.score - best.score < TIE_TOLERANCE) > 1
    return best, ambiguous


def add_view(
    project: Project,
    level_id: UUID,
    analysis: BoxAnalysis,
    *,
    image_id: UUID,
    camera_height_m: float = DEFAULT_CAMERA_HEIGHT_M,
    run_id: UUID | None = None,
) -> tuple[Project, StepResult]:
    """Confronte une photo à un niveau qui a déjà des murs (voir le module)."""
    started = time.perf_counter()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if walls_box(level) is None:
        msg = "le niveau n'a pas de murs : proposer d'abord un plan depuis une photo"
        raise DetectionError(msg)
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise DetectionError(msg)
    model_ids = [analysis.camera.model_id]
    if not analysis.floor_edges:
        return project, StepResult(
            step=STEP,
            confidence=0.0,
            warnings=[*analysis.warnings, "Aucune arête sol-mur : rien à confronter."],
            duration_s=time.perf_counter() - started,
            model_ids=model_ids,
        )
    model_ids.append(MODEL_ID)
    h = camera_height_m
    # Les arêtes « y » (parallèles à Y) donnent la coordonnée X des murs, et inversement.
    placement, ambiguous = best_placement(
        level, observe(analysis.edges("y"), h), observe(analysis.edges("x"), h)
    )
    assumptions = [
        Assumption(
            text=f"Hauteur de caméra supposée {metres(h)} : toutes les distances en dépendent.",
            impact="high",
        ),
        Assumption(text="Pièce supposée rectangulaire (hypothèse Manhattan).", impact="medium"),
    ]
    if ambiguous:
        assumptions.append(
            Assumption(
                text="Orientation de la photo ambiguë (pièce symétrique) : une des orientations "
                "équivalentes a été retenue ; tourner la vue si la caméra est du mauvais côté.",
                impact="medium",
            )
        )
    provenance = Provenance(image_ids=[image_id], step=STEP, model=MODEL_ID, run_id=run_id)
    vertices = dict(level.vertices)
    elements = dict(level.elements)
    box = walls_box(level)
    assert box is not None
    bounds = {"x": (box[0], box[2]), "y": (box[1], box[3])}
    confirmed: list[str] = []
    for axis, fit in placement.fits.items():
        for side, (coordinate, edge) in fit.moves.items():
            wall = side_wall(level, axis, bounds[axis][0 if side == "min" else 1])
            assert wall is not None
            for vid in (wall.start_vertex, wall.end_vertex):
                old = vertices[vid]
                position = old.position.model_copy(update={axis: coordinate})
                vertices[vid] = old.model_copy(
                    update={
                        "position": position,
                        "source": Source.ESTIMATED,
                        "confidence": edge.score,
                        "uncertainty": Uncertainty(sigma=ESTIMATED_SIGMA_M),
                    }
                )
            elements[wall.id] = wall.model_copy(
                update={
                    "source": Source.ESTIMATED,
                    "confidence": edge.score,
                    "uncertainty": Uncertainty(sigma=ESTIMATED_SIGMA_M),
                    "provenance": provenance,
                }
            )
            confirmed.append(wall.name or str(wall.id))
    index = 1 + sum(
        1 for e in level.elements.values() if isinstance(e, Camera) and e.image_id is not None
    )
    yaw_hint = analysis.camera.yaw_hint
    if yaw_hint is None:
        assumptions.append(
            Assumption(
                text="Lacet de la photo inconnu : caméra orientée selon l'axe X.", impact="low"
            )
        )
    yaw = (yaw_hint or 0.0) + placement.quarter_turns * math.pi / 2.0
    yaw = math.atan2(math.sin(yaw), math.cos(yaw))
    camera = Camera(
        name=f"Photo {index}",
        position=Point3(x=placement.fits["x"].position, y=placement.fits["y"].position, z=h),
        yaw=yaw,
        pitch=analysis.camera.pitch,
        roll=analysis.camera.roll,
        focal_px=analysis.camera.focal_px,
        image_id=image_id,
        source=Source.ESTIMATED,
        confidence=analysis.camera.confidence,
        provenance=provenance,
        metadata=_metadata(placement, h, ambiguous),
    )
    elements[camera.id] = camera
    new_level = level.model_copy(update={"vertices": vertices, "elements": elements})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project, after, actor=ACTOR, label=f"Confronter la photo {index} à la scène", run_id=run_id
    )
    warnings = list(analysis.warnings)
    warnings.extend(v.message for v in check_views(after) if camera.id in v.element_ids)
    if confirmed:
        warnings.append(f"Murs confirmés par cette photo : {', '.join(confirmed)}.")
    return after, StepResult(
        step=STEP,
        patch=after.history[-1].patch,
        confidence=analysis.camera.confidence,
        assumptions=assumptions,
        warnings=warnings,
        duration_s=time.perf_counter() - started,
        model_ids=model_ids,
    )


def _metadata(placement: Placement, h: float, ambiguous: bool) -> dict[str, JsonValue]:
    extents: dict[str, JsonValue] = {}
    distances: dict[str, JsonValue] = {}
    for axis, obs in placement.observations.items():
        if obs.extent is not None:
            extents[axis] = round(obs.extent, 4)
        if obs.lo is not None:
            distances[f"{axis}-"] = round(-obs.lo, 4)
        if obs.hi is not None:
            distances[f"{axis}+"] = round(obs.hi, 4)
    return {
        EXTENTS_KEY: extents,
        DISTANCES_KEY: distances,
        "camera_height_m": h,
        ORIENTATION_KEY: placement.quarter_turns,
        AMBIGUOUS_KEY: ambiguous,
    }
