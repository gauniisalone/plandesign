"""KAN-95 (P2a) : brique générique du recoupement de plusieurs photos d'une même pièce.

Ce qui ne dépend pas de la forme de la pièce, partagé par les combles (`attic_fusion`) et, à
venir, les pièces droites :

- **lecture** des photos membres de la carte Photos reliée (C1) : profondeur gardée (KAN-66,
  calculée et gardée si elle manque et qu'un estimateur est là), focale des lignes de fuite,
  mesure de référence posée sur la photo ;
- **focale commune** de la série : médiane des focales trouvées par les lignes de fuite, en
  proportion du grand côté (même appareil), réappliquée dans la grille de chaque profondeur ;
- **gros plan** (KAN-91, L5 ; P2d) : une photo qui ne voit pas le fond de la pièce (portée sous
  `CLOSE_UP_REACH` hauteurs d'appareil) est écartée du recoupement, jamais pour rétrécir la
  pièce ;
- **échelles** : chaque photo à la hauteur de prise de vue (saisie ou supposée) ; une mesure de
  référence sur une seule photo fixe celle de toutes (`reference_scale`) ;
- **concordance** : écart le plus grand quand on retire une photo à tour de rôle (jackknife),
  « N photos concordent à ±X % » ;
- **plan** : structure remplacée seulement si elle est encore celle de l'estimation ; une caméra
  par photo, sa contribution en métadonnées (survol), son échelle pour la mesure de référence ;
  l'origine de l'échelle du niveau et sa confiance.

La statistique robuste (médiane pondérée, rejet MAD) est dans `geom.robust`.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, JsonValue

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import AdapterUnavailableError, DepthEstimator
from openplan.pipeline.confidence import CLOSE_UP_REACH, ScaleSource
from openplan.pipeline.depthplan import depth_reach
from openplan.pipeline.ingestion import load_image
from openplan.pipeline.reference import (
    FUSION_FACTOR_KEY,
    FUSION_KEY,
    FUSION_SPREAD_KEY,
    FUSION_SUMMARY_KEY,
    StoredDepth,
    camera_basis,
    load_depth,
    save_depth,
    scale_confidence,
    segment_distance,
)
from openplan.schemas.common import USER_AUTHORITATIVE, StrictModel
from openplan.schemas.elements import Camera, Element
from openplan.schemas.scene import ConfidenceInputs, Project, ScaleBasis

MIN_PHOTOS = 2
FUSION_STRIDE = 2
"""Profondeur gardée réduite (≈ 640 px) : un pixel sur deux, soit autant de points qu'une
photo pleine lue un pixel sur quatre."""
MAX_SPREAD = 0.5
"""Au-delà de ±50 % entre photos, elles se contredisent : rien n'est remplacé, c'est dit."""
SHAPE_FLOOR = 0.05
"""Concordance affichée au moins à ±5 % : le jackknife sur peu de photos sous-estime."""
CLOSE_UP_REASON = "écartée : gros plan (la photo ne voit pas le fond de la pièce)"


@dataclass
class PhotoReading:
    """Une photo lue : ce qu'il faut pour l'analyser et la mettre à l'échelle."""

    image_id: UUID
    focal_norm: float | None = None
    """Focale / grand côté (les lignes de fuite si elles la donnent, sinon le repli)."""
    from_vps: bool = False
    long_side: int = 0
    reference: float | None = None
    """Mètres par unité du modèle imposés par une mesure de référence sur cette photo."""
    segment_id: UUID | None = None
    reason: str = ""
    stored: StoredDepth | None = None
    frame_up: NDArray[np.float64] | None = None
    camera_h: float | None = None
    """Hauteur de l'appareil au-dessus du sol vu, unités du modèle (None : sol non vu)."""
    reach: float | None = None
    close_up: bool = False

    @property
    def usable(self) -> bool:
        """À l'échelle (sol vu) et pas un gros plan."""
        return self.camera_h is not None and not self.close_up


class PhotoContribution(StrictModel):
    """Ce qu'une photo apporte au recoupement (survol de la phrase de concordance)."""

    image_id: UUID
    kept: bool
    reason: str
    """Ce qu'elle apporte (« faîtage et pente »…), ou pourquoi elle est écartée."""
    weight: float = Field(ge=0.0, le=1.0)
    ridge_m: float | None = None
    pitch_deg: float | None = None
    camera_height_m: float | None = None
    along_ridge: float | None = None
    factor: float | None = None
    """Mètres par unité de sa profondeur gardée, après recoupement (mesure de référence)."""
    close_up: bool = False


# ------------------------------------------------------------------ lecture
def read_photos(
    project: Project,
    project_dir: Path,
    image_ids: Iterable[UUID],
    estimator: DepthEstimator | None,
) -> list[PhotoReading]:
    """Chaque photo : focale, profondeur gardée (calculée et gardée si besoin), mesure de
    référence. Une photo sans profondeur reste dans la liste, avec sa raison."""
    out: list[PhotoReading] = []
    vp = VanishingPointCameraEstimator()
    for image_id in dict.fromkeys(image_ids):
        r = PhotoReading(image_id)
        out.append(r)
        ref = project.images.get(image_id)
        if ref is None:
            r.reason = "photo absente du projet"
            continue
        image = load_image(project_dir, ref)
        analysis = vp.analyse(image)
        r.long_side = max(image.width, image.height)
        r.from_vps = analysis.frame is not None and analysis.frame.focal_from_vps
        r.focal_norm = analysis.camera.focal_px / r.long_side
        stored = load_depth(project_dir, image_id)
        if stored is None and estimator is not None and not getattr(estimator, "from_store", False):
            try:
                depth_map = estimator.predict(image)
            except (AdapterUnavailableError, OSError, RuntimeError, ValueError):
                depth_map = None
            if depth_map is not None and save_depth(project_dir, image, depth_map, analysis.camera):
                stored = load_depth(project_dir, image_id)
        if stored is None:
            r.reason = "profondeur absente"
            continue
        if ref.reference_segments:
            seg = ref.reference_segments[0]
            try:
                distance = segment_distance(
                    stored.depth,
                    stored.focal_px,
                    stored.principal,
                    stored.to_grid(seg.p1),
                    stored.to_grid(seg.p2),
                )
            except ValueError:
                distance = 0.0
            if distance > 0:
                r.reference, r.segment_id = seg.length_m / distance, seg.id
        r.reason = "à lire"
        r.stored = stored
        r.frame_up = analysis.frame.up if analysis.frame is not None else None
    return out


def common_focal_norm(readings: list[PhotoReading]) -> float | None:
    """Focale commune de la série : médiane des focales des lignes de fuite (au moins deux)."""
    focals = [r.focal_norm for r in readings if r.from_vps and r.focal_norm]
    return statistics.median(focals) if len(focals) >= MIN_PHOTOS else None


def grid_focal(reading: PhotoReading, common: float | None) -> float:
    """Focale dans la grille de la profondeur gardée : la commune si la série en donne une."""
    assert reading.stored is not None
    norm = common if common is not None else reading.focal_norm
    focal = (norm or 0.0) * reading.long_side * reading.stored.scale[0]
    return focal if focal > 0 else reading.stored.focal_px


def set_camera_height(reading: PhotoReading, camera_h: float | None) -> None:
    """Hauteur d'appareil vue (sol trouvé), puis portée et gros plan (P2d) : une photo qui ne
    voit pas le fond de la pièce ne compte pas."""
    reading.camera_h = camera_h
    if camera_h is None or reading.stored is None:
        return
    reading.reach = depth_reach(reading.stored.depth, camera_h)
    reading.close_up = reading.reach is not None and reading.reach < CLOSE_UP_REACH
    if reading.close_up:
        reading.reason = CLOSE_UP_REASON


def default_scales(readings: list[PhotoReading], camera_height_m: float) -> dict[UUID, float]:
    """Mètres par unité du modèle de chaque photo utilisable, à la hauteur de prise de vue."""
    return {
        r.image_id: camera_height_m / r.camera_h
        for r in readings
        if r.usable and r.camera_h is not None
    }


def reference_scale(
    readings: list[PhotoReading], scales: dict[UUID, float]
) -> tuple[PhotoReading | None, float]:
    """La mesure de référence posée sur une photo recoupée, et le facteur qui met toutes les
    échelles `scales` (déjà rendues cohérentes entre photos) à la sienne (1 sans mesure)."""
    referenced = next(
        (r for r in readings if r.reference and r.image_id in scales and r.usable), None
    )
    if referenced is None or referenced.reference is None:
        return None, 1.0
    return referenced, referenced.reference / scales[referenced.image_id]


# ------------------------------------------------------------------ concordance
def jackknife(
    used: list[UUID],
    refit: Callable[[UUID], dict[str, float] | None],
    full: dict[str, float],
) -> float | None:
    """Écart relatif le plus grand sur les cotes `full` quand on retire chaque photo `used` à
    tour de rôle (`refit` recoupe sans elle) ; None avec une seule photo, ou si une photo porte
    seule une cote."""
    if len(used) < MIN_PHOTOS:
        return None
    worst = 0.0
    for image_id in used:
        other = refit(image_id)
        if other is None:
            return None
        for key, value in full.items():
            if value:
                worst = max(worst, abs(other[key] / value - 1.0))
    return max(worst, SHAPE_FLOOR)


def concordance(used: int, spread: float | None) -> str:
    if spread is None:
        return f"{used} photo{'s' if used > 1 else ''} : pas assez pour recouper"
    return f"{used} photos concordent à ±{math.ceil(spread * 100)} %"


# ------------------------------------------------------------------ plan
def structure_is_estimated(project: Project, level_id: UUID, structure: tuple[type, ...]) -> bool:
    """Aucun élément de structure retouché (source de l'utilisateur) ni verrouillé."""
    return not any(
        isinstance(e, structure) and (e.source in USER_AUTHORITATIVE or e.locked)
        for e in project.levels[level_id].elements.values()
    )


def stripped(project: Project, level_id: UUID, structure: tuple[type, ...]) -> Project:
    """Le niveau sans sa structure estimée ni les caméras de photos (reposées ensuite)."""
    level = project.levels[level_id]
    elements = {
        k: e
        for k, e in level.elements.items()
        if not isinstance(e, structure) and not (isinstance(e, Camera) and e.image_id is not None)
    }
    used = {
        v
        for e in elements.values()
        for v in (getattr(e, "start_vertex", None), getattr(e, "end_vertex", None))
        if v is not None
    }
    vertices = {k: v for k, v in level.vertices.items() if k in used}
    new_level = level.model_copy(update={"elements": elements, "vertices": vertices})
    return project.model_copy(update={"levels": {**project.levels, level_id: new_level}})


def camera_metadata(
    c: PhotoContribution, summary: str, spread: float | None
) -> dict[str, JsonValue]:
    """Métadonnées de la caméra d'une photo recoupée : sa contribution (survol), la phrase de
    concordance, et son échelle (mesure de référence sur cette photo)."""
    meta: dict[str, JsonValue] = {
        FUSION_KEY: {
            "kept": c.kept,
            "reason": c.reason,
            "weight": c.weight,
            "ridge_m": c.ridge_m,
            "pitch_deg": c.pitch_deg,
            "camera_height_m": c.camera_height_m,
        },
        FUSION_SUMMARY_KEY: summary,
        FUSION_SPREAD_KEY: spread,
    }
    if c.factor is not None:
        meta[FUSION_FACTOR_KEY] = c.factor
    return meta


def with_provenance(
    elements: dict[UUID, Element], structure: tuple[type, ...], step: str, image_ids: list[UUID]
) -> dict[UUID, Element]:
    """La structure posée par `step` prend toutes les photos retenues en provenance."""
    out = dict(elements)
    for k, e in elements.items():
        if isinstance(e, structure) and e.provenance.step == step:
            out[k] = e.model_copy(
                update={"provenance": e.provenance.model_copy(update={"image_ids": image_ids})}
            )
    return out


def fused_basis(
    main: PhotoContribution,
    height: float,
    *,
    entered: bool,
    inputs: ConfidenceInputs,
    referenced: PhotoReading | None,
    rho: float,
    final: dict[UUID, float],
    summary: str,
    used: int,
    spread: float | None,
) -> ScaleBasis:
    """Origine de l'échelle du niveau recoupé : la photo principale à la hauteur de prise de
    vue, ou la mesure de référence (avec l'échelle d'avant, pour la retirer) ; texte suivi de
    la concordance, confiance recalculée avec elle."""
    basis = camera_basis(main.image_id, main.factor, height, entered=entered, inputs=inputs)
    if referenced is not None and referenced.segment_id is not None and main.factor:
        previous = basis.model_copy(update={"factor": main.factor / rho})
        basis = ScaleBasis(
            source="reference",
            text="Échelle calée sur votre mesure",
            image_id=referenced.image_id,
            segment_id=referenced.segment_id,
            factor=final[referenced.image_id],
            previous=previous,
            camera_height_m=basis.camera_height_m,
            confidence_inputs=inputs,
        )
    source = scale_source(referenced, entered=entered)
    confidence = scale_confidence(
        source, inputs, height if entered else None, fused_photos=used, fusion_spread=spread
    )
    return basis.model_copy(update={"text": f"{basis.text} · {summary}", "confidence": confidence})


def scale_source(referenced: PhotoReading | None, *, entered: bool) -> ScaleSource:
    if referenced is not None:
        return "reference"
    return "entered" if entered else "assumed"
