"""Étape « openings.detect » (roadmap 2.4b) : boîtes image → ouvertures sur le mur hôte.

Chaque boîte (porte, fenêtre) rendue par le détecteur est projetée sur les murs du plan par
la caméra de la photo (élément `Camera` créé par la proposition de plan : position, lacet,
tangage, focale). Les quatre coins de la boîte deviennent des rayons ; le mur hôte est le
premier mur rencontré par le rayon du milieu du bord bas ; l'intersection des rayons avec le
plan vertical de ce mur donne la position le long du mur, la largeur, la hauteur d'appui et la
hauteur de la baie, à l'échelle du plan (donc avec son incertitude).

Filtres géométriques, chacun expliqué dans le rapport : largeur hors de [0,4 ; 3,0] m,
hauteur < 0,4 m, baie qui dépasse le plafond, « fenêtre » dont le haut est sous 1 m (tableau,
plinthe), boîte déjà couverte par une ouverture existante du même mur. Une « porte » dont le
bas est nettement au-dessus du sol est reclassée fenêtre (une porte part du sol). Ce qui
passe devient un `Opening` de source `observed`, confiance = score du détecteur, corrigeable
et supprimable comme tout élément ; rien n'est imposé.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from pydantic import Field

from openplan.adapters.protocols import DetectedElement, RoomElementDetector
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.perception import _overlay
from openplan.scene import history
from openplan.schemas.common import Provenance, Source, StrictModel, Uncertainty
from openplan.schemas.elements import Camera, Opening, OpeningKind, Wall
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Level, Project

STEP = "openings.detect"
ACTOR = "pipeline:openings"
WIDTH_RANGE_M = (0.4, 3.0)
MIN_HEIGHT_M = 0.4
DOOR_MAX_SILL_M = 0.3
"""Une « porte » dont le bas est plus haut que cela au-dessus du sol est une fenêtre."""
WINDOW_MIN_TOP_M = 1.0
"""Une « fenêtre » dont le haut est sous 1 m : tableau, miroir, plinthe, pas une baie."""
CEILING_SLACK_M = 0.3
OVERLAP_EXISTING = 0.5
WALL_END_SLACK_M = 0.1
Filtered = Literal[
    "hors_champ",
    "aucun_mur",
    "largeur_implausible",
    "hauteur_implausible",
    "depasse_plafond",
    "trop_bas_pour_une_fenetre",
    "deja_present",
]


class ObservedOpening(StrictModel):
    """Ce qui a été projeté pour une boîte, gardé ou non, et pourquoi."""

    category: Literal["door", "window"]
    kind: Literal["door", "window"] | None = None
    """Nature retenue après filtres (une porte trop haute devient fenêtre) ; None si écartée."""
    bbox_px: tuple[int, int, int, int]
    score: float
    host_wall: UUID | None = None
    host_wall_name: str | None = None
    offset_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    sill_m: float | None = None
    filtered: Filtered | None = None
    reason: str = ""
    element_id: UUID | None = None


class OpeningsRun(StrictModel):
    run_id: UUID
    image_id: UUID
    project: Project
    observations: list[ObservedOpening] = Field(default_factory=list)
    result: StepResult


@dataclass(frozen=True)
class CameraRays:
    """Rayons de la photo dans le repère du plan, depuis l'élément Camera de la scène."""

    origin: np.ndarray
    right: np.ndarray
    up: np.ndarray
    forward: np.ndarray
    focal_px: float
    principal_point: tuple[float, float]

    @classmethod
    def from_camera(cls, camera: Camera, width_px: int, height_px: int) -> CameraRays:
        """Conventions de `Camera`/`RenderCamera` : lacet depuis X antihoraire, tangage
        positif vers le haut, roulis autour de la visée (horizon antihoraire positif)."""
        yaw, p, r = camera.yaw, camera.pitch, camera.roll
        forward = np.array([math.cos(yaw) * math.cos(p), math.sin(yaw) * math.cos(p), math.sin(p)])
        right = np.array([math.sin(yaw), -math.cos(yaw), 0.0])
        up = np.array([-math.cos(yaw) * math.sin(p), -math.sin(yaw) * math.sin(p), math.cos(p)])
        if abs(r) > 1e-9:
            cr, sr = math.cos(r), math.sin(r)
            right, up = cr * right + sr * up, -sr * right + cr * up
        focal = camera.focal_px or 0.8 * max(width_px, height_px)
        return cls(
            origin=np.array([camera.position.x, camera.position.y, camera.position.z]),
            right=right,
            up=up,
            forward=forward,
            focal_px=focal,
            principal_point=(width_px / 2.0, height_px / 2.0),
        )

    def direction(self, u: float, v: float) -> np.ndarray:
        cx, cy = self.principal_point
        d: np.ndarray = (
            ((u - cx) / self.focal_px) * self.right
            - ((v - cy) / self.focal_px) * self.up
            + self.forward
        )
        return d


@dataclass(frozen=True)
class WallPlane:
    wall: Wall
    start: np.ndarray
    unit: np.ndarray
    normal: np.ndarray
    length: float

    def hit(self, rays: CameraRays, u: float, v: float) -> tuple[float, float, float] | None:
        """(t, s, z) : distance le long du rayon, abscisse le long du mur, hauteur ; None si
        le rayon ne rencontre pas le plan devant la caméra."""
        d = rays.direction(u, v)
        denom = float(d[0] * self.normal[0] + d[1] * self.normal[1])
        if abs(denom) < 1e-9:
            return None
        rel = self.start - rays.origin[:2]
        t = float(rel[0] * self.normal[0] + rel[1] * self.normal[1]) / denom
        if t <= 1e-6:
            return None
        point = rays.origin + t * d
        s = float(
            (point[0] - self.start[0]) * self.unit[0] + (point[1] - self.start[1]) * self.unit[1]
        )
        return t, s, float(point[2])


def wall_planes(graph: WallGraph, origin_xy: np.ndarray) -> list[WallPlane]:
    """Un plan vertical par mur visible, posé sur la face du mur tournée vers la caméra (pas
    sur son axe : à 2 m d'un mur de 15 cm, l'axe grossirait la baie de 4 %)."""
    out: list[WallPlane] = []
    for wid, wall in graph.walls.items():
        if not wall.visible:
            continue
        try:
            ux, uy = graph.wall_direction(wid)
            a, _ = graph.wall_axis(wid)
            length = graph.wall_length(wid)
        except GeometryError:
            continue
        normal = np.array([-uy, ux])
        axis = np.array([a.x, a.y])
        side = 1.0 if float((origin_xy - axis) @ normal) >= 0 else -1.0
        out.append(
            WallPlane(
                wall=wall,
                start=axis + side * (wall.thickness / 2.0) * normal,
                unit=np.array([ux, uy]),
                normal=normal,
                length=length,
            )
        )
    return out


def _host(
    planes: list[WallPlane], rays: CameraRays, u: float, v: float
) -> tuple[WallPlane, float] | None:
    """Premier mur (plus petit t) rencontré par le rayon, dans les limites du segment."""
    best: tuple[WallPlane, float] | None = None
    for plane in planes:
        hit = plane.hit(rays, u, v)
        if hit is None:
            continue
        t, s, _ = hit
        if s < -WALL_END_SLACK_M or s > plane.length + WALL_END_SLACK_M:
            continue
        if best is None or t < best[1]:
            best = (plane, t)
    return best


def project_opening(  # noqa: PLR0911 - un retour par motif de filtrage, tous expliqués
    element: DetectedElement,
    rays: CameraRays,
    planes: list[WallPlane],
    level: Level,
    existing: list[Opening],
) -> ObservedOpening:
    x0, y0, x1, y1 = element.bbox_px
    category: Literal["door", "window"] = "door" if element.category == "door" else "window"
    obs = ObservedOpening(category=category, bbox_px=element.bbox_px, score=element.score)
    found = _host(planes, rays, (x0 + x1) / 2.0, y1)
    if found is None:
        return obs.model_copy(
            update={"filtered": "aucun_mur", "reason": "aucun mur du plan derrière cette boîte"}
        )
    plane, _ = found
    corners = [plane.hit(rays, u, v) for u, v in ((x0, y1), (x1, y1), (x0, y0), (x1, y0))]
    if any(c is None for c in corners):
        return obs.model_copy(
            update={
                "filtered": "hors_champ",
                "reason": "un coin de la boîte ne rencontre pas le mur",
            }
        )
    (_, s_bl, z_bl), (_, s_br, z_br), (_, s_tl, z_tl), (_, s_tr, z_tr) = corners  # type: ignore[misc]
    # La boîte est le rectangle image englobant un quadrilatère : ses coins ne sont pas les
    # coins de la baie. Les verticales convergent vers le nadir quand la caméra plonge (les
    # bords gauche/droit de la boîte sont donc atteints en haut) et vers le zénith quand elle
    # se lève ; le bord haut de la boîte est fixé par le coin le plus proche (au-dessus de
    # l'œil) et le bord bas par le coin le plus proche (sous l'œil) : d'où min/max.
    floor = level.elevation
    s_a, s_b = (s_tl, s_tr) if rays.forward[2] < 0 else (s_bl, s_br)
    s_lo, s_hi = sorted((s_a, s_b))
    width = s_hi - s_lo
    bottom = max(z_bl, z_br) - floor
    top = min(z_tl, z_tr) - floor
    sill = max(0.0, bottom)
    height = top - sill
    base = obs.model_copy(
        update={
            "host_wall": plane.wall.id,
            "host_wall_name": plane.wall.name,
            "offset_m": s_lo,
            "width_m": width,
            "height_m": height,
            "sill_m": sill,
        }
    )
    if not WIDTH_RANGE_M[0] <= width <= WIDTH_RANGE_M[1]:
        return base.model_copy(
            update={
                "filtered": "largeur_implausible",
                "reason": (
                    f"largeur projetée {width:.2f} m hors de "
                    f"{WIDTH_RANGE_M[0]:.1f}–{WIDTH_RANGE_M[1]:.1f} m"
                ),
            }
        )
    if height < MIN_HEIGHT_M:
        return base.model_copy(
            update={"filtered": "hauteur_implausible", "reason": f"hauteur projetée {height:.2f} m"}
        )
    if top > level.height + CEILING_SLACK_M:
        return base.model_copy(
            update={
                "filtered": "depasse_plafond",
                "reason": f"haut de la baie à {top:.2f} m pour un plafond à {level.height:.2f} m",
            }
        )
    kind: Literal["door", "window"] = category
    if category == "door" and bottom > DOOR_MAX_SILL_M:
        kind = "window"
    if kind == "window" and top < WINDOW_MIN_TOP_M:
        return base.model_copy(
            update={
                "filtered": "trop_bas_pour_une_fenetre",
                "reason": f"haut à {top:.2f} m : tableau, miroir ou plinthe plutôt qu'une baie",
            }
        )
    if kind == "door":
        sill = 0.0
        height = top
    for other in existing:
        if other.host_wall != plane.wall.id:
            continue
        overlap = min(other.offset + other.width, s_hi) - max(other.offset, s_lo)
        if overlap > OVERLAP_EXISTING * min(other.width, width):
            return base.model_copy(
                update={
                    "kind": kind,
                    "filtered": "deja_present",
                    "reason": f"recouvre « {other.name or other.kind} » déjà sur ce mur",
                }
            )
    return base.model_copy(update={"kind": kind, "sill_m": sill, "height_m": height})


def apply_openings(
    project: Project,
    level_id: UUID,
    image_id: UUID,
    elements: list[DetectedElement],
    *,
    run_id: UUID | None = None,
) -> tuple[Project, list[ObservedOpening], list[str]]:
    """Projette les boîtes par la caméra de la photo et ajoute les ouvertures retenues
    (source `observed`) ; renvoie le projet, le détail par boîte et des avertissements."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    camera = next(
        (e for e in level.elements.values() if isinstance(e, Camera) and e.image_id == image_id),
        None,
    )
    if camera is None:
        msg = (
            "la caméra de cette photo n'est pas dans le plan : proposer d'abord un plan depuis "
            "la photo (ou la confronter à la scène), puis relancer"
        )
        raise DetectionError(msg)
    graph = WallGraph.from_level(level)
    rays = CameraRays.from_camera(camera, ref.width_px, ref.height_px)
    planes = wall_planes(graph, rays.origin[:2])
    existing = [e for e in level.elements.values() if isinstance(e, Opening)]
    provenance = Provenance(
        image_ids=[image_id],
        step=STEP,
        model=elements[0].model_id if elements else None,
        run_id=run_id,
    )
    observations: list[ObservedOpening] = []
    added: dict[UUID, Opening] = {}
    for element in elements:
        if element.category not in ("door", "window"):
            continue
        obs = project_opening(element, rays, planes, level, [*existing, *added.values()])
        if obs.filtered is None and obs.kind is not None and obs.host_wall is not None:
            opening = Opening(
                name=f"{'Porte' if obs.kind == 'door' else 'Fenêtre'} vue sur la photo",
                kind=OpeningKind.DOOR if obs.kind == "door" else OpeningKind.WINDOW,
                host_wall=obs.host_wall,
                offset=max(0.0, obs.offset_m or 0.0),
                width=obs.width_m or WIDTH_RANGE_M[0],
                height=obs.height_m or MIN_HEIGHT_M,
                sill_height=obs.sill_m or 0.0,
                source=Source.OBSERVED,
                confidence=min(0.9, max(0.1, element.score)),
                uncertainty=Uncertainty(sigma=0.15 * (obs.width_m or 1.0)),
                provenance=provenance,
            )
            added[opening.id] = opening
            obs = obs.model_copy(update={"element_id": opening.id})
        observations.append(obs)
    warnings = [
        f"{o.category} écartée ({o.filtered}) : {o.reason}" for o in observations if o.filtered
    ]
    if not added:
        return project, observations, warnings
    new_level = level.model_copy(update={"elements": {**level.elements, **added}})
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    after = history.commit(
        project,
        after,
        actor=ACTOR,
        label=f"Ajouter {len(added)} ouverture(s) vue(s) sur la photo",
        run_id=run_id,
    )
    return after, observations, warnings


def run_openings(
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    *,
    detector: RoomElementDetector,
    run_id: UUID | None = None,
) -> OpeningsRun:
    """Photo → détecteur → projection → ouvertures observées ; superposition en artefact."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    elements = detector.detect(image)
    after, observations, warnings = apply_openings(
        project, level_id, image_id, elements, run_id=rid
    )
    out_dir = runs_dir / str(rid) / "openings"
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[Artifact] = [_overlay(out_dir / "openings_overlay.png", image.pixels, elements)]
    (out_dir / "openings.json").write_text(
        "[\n" + ",\n".join(o.model_dump_json() for o in observations) + "\n]\n", encoding="utf-8"
    )
    kept = [o for o in observations if o.filtered is None]
    if not elements:
        warnings.append("Aucune porte ni fenêtre vue par le détecteur.")
    return OpeningsRun(
        run_id=rid,
        image_id=image_id,
        project=after,
        observations=observations,
        result=StepResult(
            step=STEP,
            patch=after.history[-1].patch if kept else [],
            confidence=min((o.score for o in kept), default=0.0),
            assumptions=[
                Assumption(
                    text="Ouvertures vues par un détecteur, projetées par la caméra estimée de la "
                    "photo : largeur et hauteur à l'échelle du plan (±15 % tant que rien n'est "
                    "mesuré), à vérifier et corriger une par une.",
                    impact="high",
                )
            ],
            warnings=warnings,
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[detector.model_id],
        ),
    )
