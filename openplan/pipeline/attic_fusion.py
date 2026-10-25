"""KAN-95 (lot C) : combles recoupés sur plusieurs photos.

Une photo seule lit mal la pente d'un toit vu dans l'axe (les versants sont vus de loin et en
raccourci), et son échelle repose sur une hauteur d'appareil supposée. Les photos de la carte
Photos reliée au plan (KAN-91, règle C1 : seules ses membres comptent) sont lues chacune avec
la profondeur gardée (KAN-66), puis recoupées :

- **repère commun** : le sol (hauteurs comptées depuis lui), l'axe du faîtage (profil en
  travers, lot B) et la focale commune de la série (médiane des focales trouvées par les
  lignes de fuite, en proportion du grand côté : même appareil) ;
- **pente** : votes de tous les plans inclinés en travers du faîtage, de toutes les photos,
  médiane pondérée (part des points), aberrants écartés (`attic.robust_center`) ;
- **faîtage** : sommet du profil en « Λ » de chaque photo qui voit les deux versants, à
  l'échelle de cette photo, médiane pondérée par les tranches d'appui, aberrants écartés ;
- **égout** : médiane des égouts vus (0 quand la pente touche le sol vu) ;
- **largeur** entre égouts : 2 × (faîtage − égout) / tan(pente), toit supposé symétrique ;
- **longueur** : déduite (pignon vu le plus loin dans l'axe + 0,5 m), comme pour une photo
  seule : des photos opposées ne se recouvrent pas (rapport de l'essai, lot D) ;
- **échelle** : chaque photo à la hauteur de prise de vue (saisie ou supposée) ; une mesure de
  référence (KAN-66) posée sur une seule photo vaut pour toutes : elle fixe l'échelle de sa
  photo, et le recoupement la transmet aux autres par le faîtage commun.

La concordance « N photos concordent à ±X % » est l'écart le plus grand, sur la largeur et le
faîtage, quand on retire une photo (jackknife) : elle dit si une photo pèse seule sur le
résultat. Chaque photo garde sa contribution (part, faîtage, pente, retenue ou écartée et
pourquoi) dans les métadonnées de sa caméra, pour le survol.

Le plan n'est remplacé que si sa structure est encore celle de l'estimation (aucun mur ni
versant retouché par l'utilisateur) ; un pas d'historique, annulable.
"""

from __future__ import annotations

import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from pydantic import Field, JsonValue

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import AdapterUnavailableError, DepthEstimator
from openplan.pipeline.attic import (
    AtticEstimate,
    Estimate,
    PitchVote,
    analyse_attic,
    robust_center,
    weighted_median,
)
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.confidence import EstimateConfidence, ScaleSource, assess
from openplan.pipeline.floorplan import ACTOR, DEFAULT_CAMERA_HEIGHT_M, DetectionError
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
from openplan.scene import history
from openplan.schemas.common import USER_AUTHORITATIVE, Point3, Source, StrictModel
from openplan.schemas.elements import (
    Camera,
    Ceiling,
    Element,
    RoofPlane,
    RoofWindow,
    Room,
    Wall,
)
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Assumption, StepResult
from openplan.schemas.scene import ConfidenceInputs, Project, ScaleBasis

STEP = "attic.fusion"
FUSION_STRIDE = 2
"""Profondeur gardée réduite (≈ 640 px) : un pixel sur deux, soit autant de points qu'une
photo pleine lue un pixel sur quatre."""
MIN_PHOTOS = 2
AXIAL_MIN = 0.8
"""Photo « dans l'axe » (voit un pignon de face) : |cos| entre son axe et le faîtage."""
LENGTH_BEHIND_M = 0.5
"""Comme pour une photo seule : pignon opposé supposé 0,5 m derrière l'appareil."""
DEFAULT_LENGTH_M = 5.0
EAVE_MIN_M = 0.3
"""Égout recoupé plus bas que 0,3 m : la pente touche le sol (pas de jambette)."""
OBLIQUE_MAX = 0.75
"""Photo « de biais » pour la pente : |cos| entre son axe et le faîtage sous 0,75 (grenier
mesuré : photos dans l'axe 0,80 à 1,00, photo vers un rampant 0,83, photos de biais 0,74 et
moins ; au-dessus de 0,75, la photo est traitée comme prise dans l'axe, par prudence)."""
AXIAL_ONLY_ERROR = 0.5
"""Grenier mesuré, paires de photos dans l'axe sans photo de biais : largeur −45 à −53 % et
+75 % (faîtage connu). L'erreur de forme annoncée ne descend pas sous 50 % dans ce cas."""
MAX_SPREAD = 0.5
"""Au-delà de ±50 % entre photos, elles se contredisent : rien n'est remplacé, c'est dit."""
PITCH_BAND_DEG = 2.0
"""Votes de pente à moins de 2° de la médiane jamais écartés."""
RIDGE_BAND = 0.05
"""Faîtages à moins de 5 % de la médiane jamais écartés."""
SHAPE_FLOOR = 0.05
"""Concordance affichée au moins à ±5 % : le jackknife sur peu de photos sous-estime."""
STRUCTURE = (Wall, RoofPlane, Ceiling, Room, RoofWindow)
FUSED_NOTE = "recoupé sur plusieurs photos"


class PhotoContribution(StrictModel):
    """Ce qu'une photo apporte au recoupement (survol de la phrase de concordance)."""

    image_id: UUID
    kept: bool
    reason: str
    """« faîtage et pente », « pente seulement », ou pourquoi elle est écartée."""
    weight: float = Field(ge=0.0, le=1.0)
    """Part de la photo dans le faîtage (ou, à défaut, dans la pente)."""
    ridge_m: float | None = None
    pitch_deg: float | None = None
    camera_height_m: float | None = None
    along_ridge: float | None = None
    factor: float | None = None
    """Mètres par unité de sa profondeur gardée, après recoupement (mesure de référence)."""


class AtticFusion(StrictModel):
    photos: list[PhotoContribution]
    used: int
    """Photos qui ont compté (faîtage ou pente)."""
    spread: float | None = None
    """Écart relatif le plus grand quand on retire une photo (None : une seule photo)."""
    summary: str
    pitch_deg: float
    ridge_height: Estimate
    eave_height: Estimate
    width: Estimate
    length: Estimate
    common_focal: bool
    reference_image: UUID | None = None


class FusionRun(StrictModel):
    run_id: UUID
    level_id: UUID
    applied: bool
    fusion: AtticFusion | None = None
    notices: list[str] = Field(default_factory=list)
    confidence: EstimateConfidence | None = None
    project: Project
    result: StepResult


@dataclass
class _Reading:
    image_id: UUID
    estimate: AtticEstimate | None = None
    focal_norm: float | None = None
    from_vps: bool = False
    long_side: int = 0
    camera_h: float | None = None
    """Hauteur de l'appareil, unités du modèle."""
    reference: float | None = None
    """Mètres par unité du modèle imposés par une mesure de référence sur cette photo."""
    segment_id: UUID | None = None
    reason: str = ""
    votes: list[PitchVote] = field(default_factory=list)
    stored: StoredDepth | None = None
    frame_up: NDArray[np.float64] | None = None


@dataclass
class _Fused:
    pitch: float
    ridge: float
    eave: float
    ridge_ids: list[UUID]
    pitch_ids: list[UUID]
    scale: dict[UUID, float]


# ------------------------------------------------------------------ lecture des photos
def _read(
    project: Project,
    project_dir: Path,
    image_ids: list[UUID],
    estimator: DepthEstimator | None,
) -> list[_Reading]:
    out: list[_Reading] = []
    vp = VanishingPointCameraEstimator()
    for image_id in image_ids:
        r = _Reading(image_id)
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


def _analyse(readings: list[_Reading]) -> bool:
    """Chaque photo lue avec la focale commune (si la série en donne une). Renvoie vrai si la
    focale commune a servi."""
    focals = [r.focal_norm for r in readings if r.from_vps and r.focal_norm]
    common = statistics.median(focals) if len(focals) >= MIN_PHOTOS else None
    for r in readings:
        stored = r.stored
        if stored is None:
            continue
        # Focale rapportée au grand côté, réappliquée dans la grille de la profondeur gardée.
        norm = common if common is not None else r.focal_norm
        focal = (norm or 0.0) * r.long_side * stored.scale[0]
        if focal <= 0:
            focal = stored.focal_px
        estimate, _planes, cls, _pixels, _warnings = analyse_attic(
            stored.depth,
            focal,
            stored.principal,
            frame_up=r.frame_up,
            frame_up_reliable=r.from_vps,
            stride=FUSION_STRIDE,
        )
        r.estimate = estimate
        r.votes = list(estimate.pitch_votes)
        if cls is None or estimate.camera_height is None:
            r.reason = "sol non vu"
        elif estimate.floor_assumed:
            r.reason = "sol hors champ"
        else:
            r.camera_h = estimate.camera_height.value
            r.reason = "lue"
    return common is not None


# ------------------------------------------------------------------ recoupement
def _scales(readings: list[_Reading], camera_height_m: float) -> dict[UUID, float]:
    return {r.image_id: camera_height_m / r.camera_h for r in readings if r.camera_h}


def photo_pitch_weight(along_ridge: float | None) -> float:
    """Confiance d'une photo pour la pente : une photo de biais voit un versant de près et de
    face (1,5), une photo dans l'axe le voit de loin et en raccourci (0,5)."""
    along = min(max(along_ridge if along_ridge is not None else 1.0, 0.0), 1.0)
    return 0.5 + (1.0 - along)


def _vote_weights(r: _Reading) -> list[tuple[PitchVote, float]]:
    """Votes d'une photo : leur part des points de la photo, rapportée à la confiance de la
    photo (`photo_pitch_weight`), pour qu'aucune photo ne pèse plus que sa confiance."""
    total = sum(v.weight for v in r.votes)
    if total <= 0 or r.estimate is None:
        return []
    weight = photo_pitch_weight(r.estimate.along_ridge)
    return [(v, weight * v.weight / total) for v in r.votes]


def _fuse(
    readings: list[_Reading],
    scales: dict[UUID, float],
    skip: UUID | None = None,
    only: set[UUID] | None = None,
) -> _Fused | None:
    """Pente, faîtage et égout recoupés ; pour la concordance, sans la photo `skip` et parmi
    les seules photos `only` (celles qui ont compté : une aberrante écartée le reste)."""
    pool = [
        r
        for r in readings
        if r.image_id != skip and r.estimate is not None and (only is None or r.image_id in only)
    ]
    votes = [(r.image_id, v.pitch_deg, w) for r in pool for v, w in _vote_weights(r)]
    pitch, kept_votes = robust_center(
        [p for _, p, _ in votes], [w for *_, w in votes], min_band=PITCH_BAND_DEG
    )
    # Faîtage : une voix par photo. L'erreur d'une photo est surtout systématique (biais de la
    # profondeur apprise, focale) : ses points d'appui ne la rendent pas plus juste, et une
    # photo très soutenue ne doit pas emporter seule la médiane.
    ridges = [
        (r.image_id, r.estimate.ridge_profile.ridge_z * scales[r.image_id], 1.0)
        for r in pool
        if r.estimate is not None and r.estimate.ridge_profile is not None and r.image_id in scales
    ]
    values = [v for _, v, _ in ridges]
    weights = [float(w) for *_, w in ridges]
    middle = weighted_median(values, weights) or 0.0
    ridge, kept_ridges = robust_center(values, weights, min_band=RIDGE_BAND * middle)
    if pitch is None or ridge is None:
        return None
    ridge_ids = [ridges[i][0] for i in kept_ridges]
    eaves = [
        (
            statistics.mean(e.value for e in r.estimate.eave_heights) * scales[r.image_id]
            if r.estimate.eave_heights
            else 0.0
        )
        for r in pool
        if r.image_id in ridge_ids and r.estimate is not None
    ]
    eave, _ = robust_center(eaves, [1.0] * len(eaves))
    eave = eave if eave is not None and eave >= EAVE_MIN_M else 0.0
    return _Fused(
        pitch=pitch,
        ridge=ridge,
        eave=min(eave, ridge - 0.5),
        ridge_ids=ridge_ids,
        pitch_ids=list(dict.fromkeys(votes[i][0] for i in kept_votes)),
        scale=scales,
    )


def _width(f: _Fused) -> float:
    return 2.0 * max(f.ridge - f.eave, 0.0) / math.tan(math.radians(f.pitch))


def _spread(readings: list[_Reading], scales: dict[UUID, float], full: _Fused) -> float | None:
    """Jackknife : écart relatif le plus grand (largeur, faîtage) quand on retire une photo."""
    used = list(dict.fromkeys([*full.ridge_ids, *full.pitch_ids]))
    if len(used) < MIN_PHOTOS:
        return None
    worst = 0.0
    width = _width(full)
    for image_id in used:
        other = _fuse(readings, scales, skip=image_id, only=set(used))
        if other is None:
            return None  # sans cette photo, plus de recoupement : elle porte tout
        worst = max(worst, abs(_width(other) / width - 1.0), abs(other.ridge / full.ridge - 1.0))
    return max(worst, SHAPE_FLOOR)


def _length(readings: list[_Reading], scales: dict[UUID, float]) -> Estimate:
    seen = [
        r.estimate.gable_distance.value * scales[r.image_id]
        for r in readings
        if r.estimate is not None
        and r.estimate.gable_distance is not None
        and r.image_id in scales
        and (r.estimate.along_ridge or 0.0) >= AXIAL_MIN
    ]
    if not seen:
        value = DEFAULT_LENGTH_M
        note = "aucun pignon vu dans l'axe : longueur supposée"
    else:
        value = statistics.median(seen) + LENGTH_BEHIND_M
        note = (
            "pignon opposé non vu : supposé 0,5 m derrière l'appareil (médiane des photos "
            "prises dans l'axe)"
        )
    return Estimate(value=value, sigma=max(0.5, 0.2 * value), source=Source.INFERRED, note=note)


def concordance(used: int, spread: float | None) -> str:
    if spread is None:
        return f"{used} photo{'s' if used > 1 else ''} : pas assez pour recouper"
    return f"{used} photos concordent à ±{math.ceil(spread * 100)} %"


# ------------------------------------------------------------------ plan
def _structure_is_estimated(project: Project, level_id: UUID) -> bool:
    level = project.levels[level_id]
    return not any(
        isinstance(e, STRUCTURE) and (e.source in USER_AUTHORITATIVE or e.locked)
        for e in level.elements.values()
    )


def _stripped(project: Project, level_id: UUID) -> Project:
    """Le niveau sans sa structure estimée ni les caméras de photos (reposées ensuite)."""
    level = project.levels[level_id]
    elements = {
        k: e
        for k, e in level.elements.items()
        if not isinstance(e, STRUCTURE) and not (isinstance(e, Camera) and e.image_id is not None)
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


def _contributions(
    readings: list[_Reading],
    fused: _Fused,
    final_scale: dict[UUID, float],
    rho: float,
) -> list[PhotoContribution]:
    """Part de chaque photo (moyenne de sa part du faîtage, une voix par photo retenue, et de
    sa part de la pente, sa confiance de pente parmi les photos retenues) ; son faîtage et sa
    hauteur d'appareil tels qu'elle les lit seule, à la hauteur de prise de vue (× la mesure de
    référence s'il y en a une)."""
    ridge_share = {i: 1.0 / len(fused.ridge_ids) for i in fused.ridge_ids}
    pitch_weight = {
        r.image_id: photo_pitch_weight(r.estimate.along_ridge)
        for r in readings
        if r.image_id in fused.pitch_ids and r.estimate is not None
    }
    total_v = sum(pitch_weight.values()) or 1.0
    out: list[PhotoContribution] = []
    for r in readings:
        est = r.estimate
        own_pitch = None
        if r.votes:
            own_pitch, _ = robust_center(
                [v.pitch_deg for v in r.votes], [v.weight for v in r.votes]
            )
        in_ridge = r.image_id in fused.ridge_ids
        in_pitch = r.image_id in fused.pitch_ids
        if in_ridge and in_pitch:
            reason = "faîtage et pente"
        elif in_ridge:
            reason = "faîtage"
        elif in_pitch:
            reason = "pente seulement (faîtage non vu des deux côtés)"
        elif est is not None and est.ridge_profile is not None and r.image_id in fused.scale:
            reason = "écartée : faîtage trop loin des autres photos"
        elif r.reason in ("profondeur absente", "photo absente du projet", "sol non vu"):
            reason = f"écartée : {r.reason}"
        else:
            reason = "écartée : ni faîtage ni pente lisibles"
        weight = (
            ridge_share.get(r.image_id, 0.0) + pitch_weight.get(r.image_id, 0.0) / total_v
        ) / 2
        own = fused.scale.get(r.image_id)
        out.append(
            PhotoContribution(
                image_id=r.image_id,
                kept=in_ridge or in_pitch,
                reason=reason,
                weight=round(min(max(weight, 0.0), 1.0), 3),
                ridge_m=None
                if est is None or est.ridge_profile is None or own is None
                else round(est.ridge_profile.ridge_z * own * rho, 3),
                pitch_deg=None if own_pitch is None else round(own_pitch, 1),
                camera_height_m=None
                if r.camera_h is None or r.image_id not in final_scale
                else round(r.camera_h * final_scale[r.image_id], 3),
                along_ridge=None
                if est is None or est.along_ridge is None
                else round(est.along_ridge, 2),
                factor=final_scale.get(r.image_id),
            )
        )
    return out


def _camera_metadata(
    c: PhotoContribution, summary: str, spread: float | None
) -> dict[str, JsonValue]:
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


def _place_cameras(
    elements: dict[UUID, Element],
    contributions: list[PhotoContribution],
    readings: list[_Reading],
    geometry: AtticGeometry,
    summary: str,
    spread: float | None,
    height: float,
) -> dict[UUID, Element]:
    """Caméra de chaque photo (celle de la photo principale est posée par apply_attic), avec sa
    contribution en métadonnées ; la structure prend toutes les photos retenues en
    provenance."""
    for c in contributions:
        meta = _camera_metadata(c, summary, spread)
        existing = next(
            (e for e in elements.values() if isinstance(e, Camera) and e.image_id == c.image_id),
            None,
        )
        if existing is not None:
            elements[existing.id] = existing.model_copy(
                update={"metadata": {**existing.metadata, **meta}}
            )
            continue
        est = next(r.estimate for r in readings if r.image_id == c.image_id)
        x = geometry.length.value / 2.0
        if (
            est is not None
            and est.gable_distance is not None
            and c.factor
            and (est.along_ridge or 0) >= AXIAL_MIN
        ):
            x = geometry.length.value - est.gable_distance.value * c.factor
        z = c.camera_height_m if c.camera_height_m is not None else height
        camera = Camera(
            name="Photo",
            position=Point3(
                x=min(max(x, 0.0), geometry.length.value),
                y=geometry.width.value / 2.0,
                z=max(z, 0.1),
            ),
            yaw=0.0,
            pitch=0.0,
            image_id=c.image_id,
            source=Source.ESTIMATED,
            confidence=0.4,
            metadata=meta,
        )
        elements[camera.id] = camera
    kept_ids = [c.image_id for c in contributions if c.kept]
    for k, e in list(elements.items()):
        if isinstance(e, STRUCTURE) and e.provenance.step == "attic.apply":
            elements[k] = e.model_copy(
                update={"provenance": e.provenance.model_copy(update={"image_ids": kept_ids})}
            )
    return elements


def fuse_attic(  # noqa: PLR0911, PLR0912, PLR0915
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_ids: list[UUID],
    *,
    estimator: DepthEstimator | None = None,
    camera_height_m: float | None = None,
    run_id: UUID | None = None,
) -> FusionRun:
    """Recoupe les photos `image_ids` (membres de la carte Photos reliée, dans l'ordre de la
    carte) et remplace la structure estimée du niveau par les combles recoupés."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    entered = camera_height_m is not None
    height = camera_height_m if camera_height_m is not None else DEFAULT_CAMERA_HEIGHT_M
    ids = list(dict.fromkeys(image_ids))

    def done(
        applied: bool,
        after: Project,
        notices: list[str],
        fusion: AtticFusion | None = None,
        trust: EstimateConfidence | None = None,
        confidence: float = 0.0,
    ) -> FusionRun:
        return FusionRun(
            run_id=rid,
            level_id=level_id,
            applied=applied,
            fusion=fusion,
            notices=notices,
            confidence=trust,
            project=after,
            result=StepResult(
                step=STEP,
                confidence=confidence,
                warnings=[] if applied else notices,
                duration_s=time.perf_counter() - started,
                model_ids=[m for m in (getattr(estimator, "model_id", None),) if m],
            ),
        )

    if len(ids) < MIN_PHOTOS:
        return done(False, project, ["Une seule photo dans la carte : rien à recouper."])
    readings = _read(project, project_dir, ids, estimator)
    common_focal = _analyse(readings)
    scales = _scales(readings, height)
    fused = _fuse(readings, scales)
    if fused is None:
        return done(
            False,
            project,
            ["Recoupement impossible : aucune photo ne voit les deux versants avec le sol."],
        )
    # Échelle commune : chaque photo du faîtage retenu est remise à l'échelle du faîtage recoupé
    # (sa propre hauteur d'appareil n'est plus supposée) ; une mesure de référence fixe le tout.
    final: dict[UUID, float] = dict(scales)
    for r in readings:
        if r.image_id in fused.ridge_ids and r.estimate and r.estimate.ridge_profile:
            final[r.image_id] = fused.ridge / r.estimate.ridge_profile.ridge_z
    referenced = next((r for r in readings if r.reference and r.image_id in final), None)
    rho = 1.0
    if referenced is not None and referenced.reference is not None:
        rho = referenced.reference / final[referenced.image_id]
    final = {k: v * rho for k, v in final.items()}
    pitch, ridge, eave = fused.pitch, fused.ridge * rho, fused.eave * rho
    measured_spread = _spread(readings, scales, fused)
    used = len(dict.fromkeys([*fused.ridge_ids, *fused.pitch_ids]))
    # Sans photo de biais, toutes les photos lisent la pente de la même façon fausse (versants
    # vus de loin, en raccourci) : elles concordent entre elles sans être justes. L'erreur de
    # forme est alors celle mesurée sur des paires de photos dans l'axe (AXIAL_ONLY_ERROR).
    axial_only = not any(
        r.estimate is not None
        and r.image_id in fused.pitch_ids
        and (r.estimate.along_ridge if r.estimate.along_ridge is not None else 1.0) < OBLIQUE_MAX
        for r in readings
    )
    spread = measured_spread
    summary = concordance(used, measured_spread)
    if axial_only:
        spread = max(measured_spread or 0.0, AXIAL_ONLY_ERROR)
        summary += " ; pente lue dans l'axe seulement"
    shape = spread if spread is not None else 0.25
    scale_rel = 0.05 if referenced is not None else 0.10
    rel = math.hypot(shape, scale_rel)
    width_value = 2.0 * max(ridge - eave, 0.0) / math.tan(math.radians(pitch))
    ridge_e = Estimate(value=ridge, sigma=rel * ridge, note=f"{FUSED_NOTE} ({summary})")
    eave_e = (
        Estimate(value=eave, sigma=rel * max(eave, 0.1), note=FUSED_NOTE)
        if eave > 0
        else Estimate(value=0.0, sigma=0.05, note="pentes jusqu'au sol (aucune jambette recoupée)")
    )
    width_e = Estimate(
        value=width_value,
        sigma=rel * width_value,
        note=f"{FUSED_NOTE} : pente commune {decimal(pitch, 1)}°, toit supposé symétrique",
    )
    length_e = _length(readings, {k: v for k, v in final.items()})
    contributions = _contributions(readings, fused, final, rho)
    main = max(
        (c for c in contributions if c.kept and c.factor is not None),
        key=lambda c: c.weight,
        default=None,
    )
    if main is None:
        return done(False, project, ["Recoupement impossible : aucune photo à l'échelle."])
    fusion = AtticFusion(
        photos=contributions,
        used=used,
        spread=spread,
        summary=summary,
        pitch_deg=pitch,
        ridge_height=ridge_e,
        eave_height=eave_e,
        width=width_e,
        length=length_e,
        common_focal=common_focal,
        reference_image=referenced.image_id if referenced else None,
    )
    if measured_spread is not None and measured_spread > MAX_SPREAD:
        return done(
            False,
            project,
            [
                f"Les photos se contredisent ({summary}) : le plan n'est pas remplacé. "
                "Vérifiez qu'elles montrent bien la même pièce, ou ajoutez une mesure de "
                "référence."
            ],
            fusion,
        )
    if not _structure_is_estimated(project, level_id):
        return done(
            False,
            project,
            [
                "Plan retouché à la main : les combles recoupés sur les photos ne l'ont pas "
                f"remplacé ({summary})."
            ],
            fusion,
        )
    main_reading = next(r for r in readings if r.image_id == main.image_id)
    main_est = main_reading.estimate
    assert main_est is not None and main_reading.camera_h is not None
    gable = (
        Estimate(
            value=main_est.gable_distance.value * final[main.image_id],
            sigma=0.2 * main_est.gable_distance.value * final[main.image_id],
        )
        if main_est.gable_distance is not None and (main_est.along_ridge or 0) >= AXIAL_MIN
        else None
    )
    try:
        geometry = AtticGeometry(
            length=length_e,
            width=width_e,
            ridge_height=ridge_e,
            eave_height=eave_e,
            camera_height=Estimate(
                value=main_reading.camera_h * final[main.image_id],
                sigma=scale_rel * main_reading.camera_h * final[main.image_id],
                source=Source.ESTIMATED,
                note=FUSED_NOTE,
            ),
            gable_distance=gable if gable is not None and gable.value < length_e.value else None,
            image_id=main.image_id,
            run_id=rid,
        )
    except ValueError as exc:
        return done(False, project, [f"Combles recoupés invraisemblables : {exc}"], fusion)
    built, _step = apply_attic(_stripped(project, level_id), level_id, geometry)
    level = built.levels[level_id]
    elements = _place_cameras(
        dict(level.elements), contributions, readings, geometry, summary, spread, height
    )
    inputs = ConfidenceInputs(focal_from_vps=common_focal or main_reading.from_vps)
    basis = camera_basis(main.image_id, main.factor, height, entered=entered, inputs=inputs)
    if referenced is not None and referenced.segment_id is not None and main.factor:
        # L'échelle d'avant la mesure (retirer la mesure y revient) : celle de la photo
        # principale à la hauteur de prise de vue.
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
    source: ScaleSource = (
        "reference" if referenced is not None else ("entered" if entered else "assumed")
    )
    confidence = scale_confidence(
        source, inputs, height if entered else None, fused_photos=used, fusion_spread=spread
    )
    basis = basis.model_copy(update={"text": f"{basis.text} · {summary}", "confidence": confidence})
    new_level = level.model_copy(update={"elements": elements, "scale_basis": basis})
    after = project.model_copy(
        update={"levels": {**project.levels, level_id: new_level}, "images": project.images}
    )
    after = history.commit(
        project, after, actor=ACTOR, label=f"Combles recoupés sur {used} photos", run_id=rid
    )
    trust = assess(
        source,
        camera_height_m=height if entered else None,
        focal_from_vps=inputs.focal_from_vps,
        fused_photos=used,
        fusion_spread=spread,
    )
    notices = [
        f"Combles recoupés sur {used} photos : faîtage {metres(ridge)}, largeur "
        f"{metres(width_value)}, pente {decimal(pitch, 0)}° ({summary}). Longueur déduite, "
        "à vérifier."
    ]
    if axial_only:
        notices.append(
            "Aucune photo de biais : la pente vient de photos prises dans l'axe du faîtage, peu "
            "sûre. Ajoutez à la carte Photos une photo de biais vers un versant."
        )
    if referenced is not None:
        notices.append("Votre mesure de référence vaut pour toutes les photos recoupées.")
    run = done(True, after, notices, fusion, trust, confidence=max(0.0, 1.0 - trust.expected_error))
    run.result.assumptions.extend(
        [
            Assumption(
                text="Toit supposé symétrique : une pente commune aux deux versants.",
                impact="medium",
            ),
            Assumption(text=f"Longueur déduite : {length_e.note}.", impact="high"),
        ]
    )
    return run
