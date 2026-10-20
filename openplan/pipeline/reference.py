"""KAN-66 : mesure de référence sur une photo. Deux points de l'image et une longueur connue
(« une porte de 2,04 m ») fixent l'échelle du plan estimé depuis cette photo.

Calcul géométrique seulement, jamais un nouveau passage du modèle de profondeur : la profondeur
est gardée à l'estimation (`images/<id>.depth.npy`, avec la focale et le point principal dans
`images/<id>.depth.json`) ; les deux points sont rétroprojetés avec elle, le facteur d'échelle
est la longueur saisie divisée par leur distance en unités du modèle. Recaler (pas réestimer) :
le niveau est remis à l'échelle (`scale_3d` : plan ET hauteurs, l'erreur du modèle de
profondeur est globale), annulable, retouches gardées. Les murs restent estimés ; la mesure
entre dans les mesures du projet avec sa calibration. Une photo d'avant (sans profondeur
gardée) se réestime sur un geste explicite.

Une mesure incohérente est refusée avec la raison (`ReferenceRefusedError`) : longueur hors de
5 cm à 30 m, point sans profondeur, segment qui saute d'une surface à une autre (bord
d'occultation : un meuble devant le mur du fond), facteur hors de 0,3 à 3 par rapport à
l'échelle actuelle. Le texte affiché reste prudent : « échelle calée sur votre mesure »."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import CameraParameters, DepthEstimator, DepthMap, ImageArray
from openplan.geom.scale import CalibrationError, apply_axis_scale
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.pipeline.ingestion import IMAGES_DIR, ImageNotFoundError, load_image
from openplan.pipeline.recalibrate import room_axes
from openplan.scene import history
from openplan.schemas.common import Point2, StrictModel
from openplan.schemas.scene import (
    REFERENCE_LENGTH_M,
    Calibration,
    Level,
    Measurement,
    MeasurementKind,
    Project,
    ReferenceSegment,
    RoomSize,
    ScaleBasis,
)

FACTOR_RANGE = (0.3, 3.0)
"""Écart toléré entre l'échelle actuelle (hauteur de prise de vue) et celle de la mesure."""
JUMP_MAX = 0.25
"""Écart relatif maximal entre la profondeur le long du segment et une variation régulière
entre ses deux bouts : au-delà, le segment passe d'une surface à une autre."""
SAMPLES = 24
WINDOW = 1
"""Profondeur d'un point : médiane sur (2 × WINDOW + 1)² pixels (bruit du modèle)."""
DEPTH_MAX_SIDE = 640
"""Profondeur gardée réduite à ce côté (au plus ~0,8 Mo par photo, en float16)."""
DEPTH_SUFFIX = ".depth.npy"
META_SUFFIX = ".depth.json"


class ReferenceRefusedError(ValueError):
    """Mesure de référence refusée ; le message est montré tel quel."""


def _fr(value: float, digits: int = 2) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def check_length(meters: float) -> float:
    low, high = REFERENCE_LENGTH_M
    if not math.isfinite(meters) or not low <= meters <= high:
        msg = f"longueur hors des limites : entre {low * 100:.0f} cm et {high:.0f} m"
        raise ReferenceRefusedError(msg)
    return meters


# ------------------------------------------------------------------ géométrie
def _depth_at(depth: NDArray[np.float32], u: float, v: float) -> float | None:
    h, w = depth.shape
    ui, vi = round(u), round(v)
    if not (0 <= ui < w and 0 <= vi < h):
        return None
    patch = depth[max(vi - WINDOW, 0) : vi + WINDOW + 1, max(ui - WINDOW, 0) : ui + WINDOW + 1]
    good = patch[np.isfinite(patch) & (patch > 0)]
    return float(np.median(good)) if good.size else None


def backproject_point(
    depth: NDArray[np.float32], focal_px: float, principal: tuple[float, float], u: float, v: float
) -> NDArray[np.float64]:
    z = _depth_at(depth, u, v)
    if z is None:
        msg = "un des deux points tombe hors de la photo ou sans profondeur"
        raise ReferenceRefusedError(msg)
    cx, cy = principal
    return np.array([(u - cx) * z / focal_px, (v - cy) * z / focal_px, z])


def segment_distance(
    depth: NDArray[np.float32],
    focal_px: float,
    principal: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
) -> float:
    """Distance 3D entre les deux points, en unités du modèle ; refus si le segment saute
    d'une surface à une autre."""
    a = backproject_point(depth, focal_px, principal, *p1)
    b = backproject_point(depth, focal_px, principal, *p2)
    za, zb = float(a[2]), float(b[2])
    worst = 0.0
    for t in np.linspace(0.0, 1.0, SAMPLES)[1:-1]:
        z = _depth_at(depth, p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
        if z is None:
            continue
        # Une surface plane vue en perspective : 1/z varie linéairement le long du segment.
        expected = 1.0 / ((1 - t) / za + t / zb)
        worst = max(worst, abs(z - expected) / expected)
    if worst > JUMP_MAX:
        msg = (
            "les deux points ne sont pas sur une même surface (le segment passe devant un "
            "objet ou par un bord) : posez-les sur une même cote, une porte, un mur"
        )
        raise ReferenceRefusedError(msg)
    distance = float(np.linalg.norm(a - b))
    if distance <= 0:
        msg = "les deux points sont confondus"
        raise ReferenceRefusedError(msg)
    return distance


def reference_factor(model_distance: float, length_m: float, current_factor: float) -> float:
    """Facteur à appliquer au niveau, PAR RAPPORT à son échelle actuelle (déjà appliquée).
    Refus hors de 0,3 à 3 : la mesure et l'échelle actuelle sont sans rapport."""
    relative = length_m / model_distance / current_factor
    if not FACTOR_RANGE[0] <= relative <= FACTOR_RANGE[1]:
        msg = (
            f"cette mesure changerait l'échelle de ×{_fr(relative)} : vérifiez les deux points "
            "et la longueur (une erreur de cm et de m ?)"
        )
        raise ReferenceRefusedError(msg)
    return relative


# ------------------------------------------------------------------ profondeur gardée
@dataclass(frozen=True)
class StoredDepth:
    depth: NDArray[np.float32]
    focal_px: float
    """Dans la grille de `depth`."""
    principal: tuple[float, float]
    scale: tuple[float, float]
    """Pixels de la photo d'origine → grille de `depth`."""

    def to_grid(self, p: tuple[float, float]) -> tuple[float, float]:
        return p[0] * self.scale[0], p[1] * self.scale[1]


def depth_paths(project_dir: Path, image_id: UUID) -> tuple[Path, Path]:
    folder = project_dir / IMAGES_DIR
    return folder / f"{image_id}{DEPTH_SUFFIX}", folder / f"{image_id}{META_SUFFIX}"


def write_depth(
    project_dir: Path,
    image_id: UUID,
    depth: NDArray[np.float32],
    focal_px: float,
    principal: tuple[float, float],
    image_size: tuple[int, int],
    *,
    model_id: str = "",
) -> None:
    """Garde la profondeur (réduite, float16) et ce qu'il faut pour rétroprojeter. `focal_px`
    et `principal` sont dans la grille de `depth` ; `image_size` (largeur, hauteur) est celle
    de la photo d'origine, dans laquelle les points de la mesure sont donnés."""
    h, w = depth.shape
    step = max(1, math.ceil(max(h, w) / DEPTH_MAX_SIDE))
    # Un pixel sur `step` (pas de moyenne) : aucune profondeur inventée sur les bords d'objets.
    small = np.ascontiguousarray(depth[::step, ::step])
    npy, meta = depth_paths(project_dir, image_id)
    npy.parent.mkdir(parents=True, exist_ok=True)
    tmp = npy.with_name(npy.name + ".tmp")
    with tmp.open("wb") as f:
        np.save(f, small.astype(np.float16))
    tmp.replace(npy)
    sh, sw = small.shape
    meta.write_text(
        json.dumps(
            {
                "focal_px": focal_px / step,
                "principal": [principal[0] / step, principal[1] / step],
                # Photo → grille : la profondeur couvre toute la photo (w × h), réduite de `step`.
                "scale": [w / image_size[0] / step, h / image_size[1] / step],
                "image_size": list(image_size),
                "depth_size": [sw, sh],
                "model_id": model_id,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def save_depth(
    project_dir: Path,
    image: ImageArray,
    depth_map: DepthMap,
    camera: CameraParameters | None = None,
) -> bool:
    """Après une estimation : garde la profondeur de cette photo pour la mesure de référence.
    Faux (rien d'écrit) si la profondeur est relative : aucune longueur n'y serait juste."""
    if not depth_map.metric:
        return False
    if camera is None:
        camera = VanishingPointCameraEstimator().analyse(image).camera
    depth = depth_map.depth.astype(np.float32)
    h, w = depth.shape
    # La caméra est estimée dans les pixels de la photo ; la profondeur peut être plus petite.
    sx, sy = w / image.width, h / image.height
    write_depth(
        project_dir,
        image.image_id,
        depth,
        camera.focal_px * sx,
        (camera.principal_point[0] * sx, camera.principal_point[1] * sy),
        (image.width, image.height),
        model_id=depth_map.model_id,
    )
    return True


def load_depth(project_dir: Path, image_id: UUID) -> StoredDepth | None:
    npy, meta = depth_paths(project_dir, image_id)
    if not (npy.is_file() and meta.is_file()):
        return None
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
        depth = np.load(npy, allow_pickle=False).astype(np.float32)
        return StoredDepth(
            depth=depth,
            focal_px=float(data["focal_px"]),
            principal=(float(data["principal"][0]), float(data["principal"][1])),
            scale=(float(data["scale"][0]), float(data["scale"][1])),
        )
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def remove_depth(project_dir: Path, image_id: UUID) -> None:
    for path in depth_paths(project_dir, image_id):
        path.unlink(missing_ok=True)


# ------------------------------------------------------------------ échelle du niveau
def scale_3d(level: Level, factor: float) -> Level:
    """Toutes les longueurs du niveau, hauteurs comprises (plafond, murs, égouts ; les pentes
    gardent leur angle)."""
    return apply_axis_scale(level, 0.0, factor, factor, factor)[0]


def camera_basis(
    image_id: UUID, factor: float | None, camera_height_m: float, *, entered: bool
) -> ScaleBasis:
    """L'échelle d'un plan estimé : hauteur de prise de vue saisie, ou supposée par défaut."""
    words = "saisie" if entered else "supposée"
    tail = "" if entered else " (par défaut)"
    return ScaleBasis(
        source="camera_height" if entered else "default",
        text=f"Échelle : hauteur de prise de vue {words} {_fr(camera_height_m)} m{tail}",
        image_id=image_id,
        factor=factor,
        camera_height_m=camera_height_m,
    )


def with_basis(project: Project, level_id: UUID, basis: ScaleBasis) -> Project:
    """Pose l'origine de l'échelle sur le niveau, sans entrée d'historique (écriture du
    système, à la fin de l'estimation)."""
    level = project.levels[level_id].model_copy(update={"scale_basis": basis})
    return project.model_copy(update={"levels": {**project.levels, level_id: level}})


def basis_level(project: Project, image_id: UUID) -> UUID:
    """Le plan estimé depuis cette photo (un étage avant une variante)."""
    found = [
        (level.variant_of is not None, lid)
        for lid, level in project.levels.items()
        if level.scale_basis is not None and level.scale_basis.image_id == image_id
    ]
    if not found:
        msg = (
            "cette photo n'a pas servi à estimer un plan : estimez d'abord le plan depuis la "
            "photo, ou réestimez-la pour pouvoir mesurer"
        )
        raise ReferenceRefusedError(msg)
    return min(found)[1]


def _room_size(project: Project, level_id: UUID) -> RoomSize | None:
    try:
        _, length, width = room_axes(project, level_id)
    except CalibrationError:
        return None
    return RoomSize(
        length_m=round(length, 3),
        width_m=round(width, 3),
        height_m=round(project.levels[level_id].height, 3),
    )


class ReferenceBody(StrictModel):
    p1: tuple[float, float]
    p2: tuple[float, float]
    """Pixels de la photo d'origine."""
    length_m: float
    label: str | None = None


def _without_segment(
    project: Project, image_id: UUID, segment_id: UUID | None
) -> tuple[dict[UUID, Measurement], list[Calibration]]:
    """Mesures et calibrations du projet sans celles de la mesure de référence retirée (la
    mesure porte l'identifiant du segment)."""
    if segment_id is None:
        return dict(project.measurements), list(project.calibrations)
    measurements = {k: v for k, v in project.measurements.items() if k != segment_id}
    calibrations = [c for c in project.calibrations if segment_id not in c.measurement_ids]
    return measurements, calibrations


def apply_reference(
    project: Project,
    project_dir: Path,
    image_id: UUID,
    body: ReferenceBody,
    *,
    actor: str,
    author: str,
) -> Project:
    """Pose (ou remplace) la mesure de référence de la photo et recale le plan qui en vient."""
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    length = check_length(body.length_m)
    label = (body.label or "").strip()[:60] or None
    for x, y in (body.p1, body.p2):
        if not (0 <= x <= ref.width_px and 0 <= y <= ref.height_px):
            msg = "un des deux points tombe hors de la photo"
            raise ReferenceRefusedError(msg)
    level_id = basis_level(project, image_id)
    level = project.levels[level_id]
    basis = level.scale_basis
    assert basis is not None
    stored = load_depth(project_dir, image_id)
    if stored is None or basis.factor is None:
        msg = (
            "la profondeur de cette photo n'a pas été gardée : réestimez la photo pour "
            "pouvoir mesurer"
        )
        raise ReferenceRefusedError(msg)
    distance = segment_distance(
        stored.depth,
        stored.focal_px,
        stored.principal,
        stored.to_grid(body.p1),
        stored.to_grid(body.p2),
    )
    relative = reference_factor(distance, length, basis.factor)
    old = ref.reference_segments[0].id if ref.reference_segments else None
    camera = basis.previous if basis.source == "reference" else basis
    segment = ReferenceSegment(p1=body.p1, p2=body.p2, length_m=length, label=label, author=author)
    measurements, calibrations = _without_segment(project, image_id, old)
    measurement = Measurement(
        id=segment.id,
        kind=MeasurementKind.POINT_TO_POINT,
        value=length,
        image_points=(
            image_id,
            Point2(x=body.p1[0], y=body.p1[1]),
            Point2(x=body.p2[0], y=body.p2[1]),
        ),
        note=label,
    )
    measurements[measurement.id] = measurement
    calibrations.append(
        Calibration(
            level_id=level_id,
            measurement_ids=[measurement.id],
            scale_factor=relative,
            residuals_m={measurement.id: 0.0},
            rms_m=0.0,
        )
    )
    what = f" ({label})" if label else ""
    scaled = scale_3d(level, relative).model_copy(
        update={
            "scale_basis": ScaleBasis(
                source="reference",
                text=f"Échelle calée sur votre mesure de {_fr(length)} m{what}",
                image_id=image_id,
                segment_id=segment.id,
                factor=basis.factor * relative,
                before=_room_size(project, level_id),
                previous=camera,
                camera_height_m=basis.camera_height_m,
            )
        }
    )
    images = {**project.images, image_id: ref.model_copy(update={"reference_segments": [segment]})}
    after = project.model_copy(
        update={
            "images": images,
            "levels": {**project.levels, level_id: scaled},
            "measurements": measurements,
            "calibrations": calibrations,
        }
    )
    return history.commit(
        project, after, actor=actor, label=f"Mesure de référence : {_fr(length)} m{what}"
    )


def remove_reference(project: Project, image_id: UUID, segment_id: UUID, *, actor: str) -> Project:
    """Retire la mesure : le plan revient à l'échelle de la hauteur de prise de vue."""
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    if not any(s.id == segment_id for s in ref.reference_segments):
        msg = f"mesure inconnue : {segment_id}"
        raise ImageNotFoundError(msg)
    images = {
        **project.images,
        image_id: ref.model_copy(
            update={"reference_segments": [s for s in ref.reference_segments if s.id != segment_id]}
        ),
    }
    measurements, calibrations = _without_segment(project, image_id, segment_id)
    levels = dict(project.levels)
    for lid, level in project.levels.items():
        basis = level.scale_basis
        if basis is None or basis.segment_id != segment_id:
            continue
        back = basis.previous
        if back is not None and back.factor is not None and basis.factor is not None:
            levels[lid] = scale_3d(level, back.factor / basis.factor).model_copy(
                update={"scale_basis": back}
            )
        else:
            levels[lid] = level.model_copy(update={"scale_basis": back})
    after = project.model_copy(
        update={
            "images": images,
            "levels": levels,
            "measurements": measurements,
            "calibrations": calibrations,
        }
    )
    return history.commit(project, after, actor=actor, label="Retirer la mesure de référence")


# ------------------------------------------------------------------ photo d'avant
def depth_factor(
    depth: NDArray[np.float32],
    focal_px: float,
    principal: tuple[float, float],
    camera_height_m: float,
) -> float | None:
    """Mètres par unité du modèle quand l'échelle repose sur la hauteur de prise de vue : la
    hauteur donnée divisée par celle que la profondeur voit (sol trouvé), sinon None."""
    from openplan.pipeline.depthplan import observe_room

    observation, _ = observe_room(depth, focal_px, principal)
    if observation is None or observation.camera_height_m <= 0:
        return None
    return camera_height_m / observation.camera_height_m


def reestimate(
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    estimator: DepthEstimator,
) -> Project:
    """« Réestimer la photo pour pouvoir mesurer » (photo d'avant KAN-66) : un passage du
    modèle de profondeur, gardé ; le plan n'est pas reconstruit, seule l'origine de son échelle
    est notée (hauteur de prise de vue d'avant, ou supposée par défaut)."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise ReferenceRefusedError(msg)
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    camera = VanishingPointCameraEstimator().analyse(image).camera
    depth_map = estimator.predict(image)
    if not save_depth(project_dir, image, depth_map, camera):
        msg = "profondeur relative : aucune longueur ne peut y être mesurée"
        raise ReferenceRefusedError(msg)
    stored = load_depth(project_dir, image_id)
    assert stored is not None
    old = project.levels[level_id].scale_basis
    height = DEFAULT_CAMERA_HEIGHT_M
    entered = False
    if old is not None and old.image_id == image_id and old.camera_height_m is not None:
        height, entered = old.camera_height_m, old.source == "camera_height"
    factor = depth_factor(stored.depth, stored.focal_px, stored.principal, height)
    if factor is None:
        msg = "aucun sol vu dans la profondeur de cette photo : l'échelle ne peut pas y être calée"
        raise ReferenceRefusedError(msg)
    if old is not None and old.source == "reference":
        # Déjà calée : la profondeur est rafraîchie, l'échelle de la mesure est gardée.
        return project
    return with_basis(project, level_id, camera_basis(image_id, factor, height, entered=entered))
