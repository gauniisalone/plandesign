"""Projet, niveaux, images, mesures, calibrations, contraintes et historique."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue, model_validator

from openplan.schemas.common import (
    SCHEMA_VERSION,
    Meters,
    Point2,
    Source,
    StrictModel,
    now_utc,
)
from openplan.schemas.elements import Element, Vertex
from openplan.schemas.home import GuidedAnswers

REFERENCE_LENGTH_M = (0.05, 30.0)
"""KAN-66 : longueur admise pour une mesure de référence, en mètres."""


class ReferenceSegment(StrictModel):
    """KAN-66 : mesure de référence posée sur une photo, deux points et une longueur connue
    (« une porte de 2,04 m ») ; elle fixe l'échelle du plan estimé depuis cette photo."""

    id: UUID = Field(default_factory=uuid4)
    p1: tuple[float, float]
    p2: tuple[float, float]
    """Pixels de l'image d'origine (orientation appliquée), origine en haut à gauche."""
    length_m: float = Field(ge=REFERENCE_LENGTH_M[0], le=REFERENCE_LENGTH_M[1])
    label: str | None = Field(default=None, max_length=60)
    """Ce qui a été mesuré (« porte »), facultatif."""
    author: str
    """Auteur du geste, comme dans l'historique."""
    created_at: datetime = Field(default_factory=now_utc)


class ImageRef(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    path: str
    """Relatif au dossier du projet, jamais absolu."""
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    exif_stripped: bool
    focal_35mm: float | None = Field(default=None, gt=0.0)
    """Lu dans l'EXIF avant suppression, si l'utilisateur l'autorise."""
    quality: dict[str, float] = Field(default_factory=dict)
    """Sortie du contrôle qualité : blur, exposure, ..."""
    imported_at: datetime = Field(default_factory=now_utc)
    reference_segments: list[ReferenceSegment] = Field(default_factory=list, max_length=1)
    """KAN-66 : au plus une mesure par photo en V1 (une nouvelle remplace l'ancienne)."""


class MeasurementKind(StrEnum):
    WALL_LENGTH = "wall_length"
    DOOR_WIDTH = "door_width"
    CEILING_HEIGHT = "ceiling_height"
    POINT_TO_POINT = "point_to_point"
    OBJECT_DIMENSION = "object_dimension"
    PLAN_DIMENSION = "plan_dimension"
    """Cote lue sur un plan photographié."""


class Measurement(StrictModel):
    """Une mesure connue, toujours d'autorité utilisateur.

    Elle cible exactement une chose : un élément, deux sommets du plan, ou deux points image.
    """

    id: UUID = Field(default_factory=uuid4)
    kind: MeasurementKind
    value: float = Field(gt=0.0)
    """Mètres."""
    tolerance: Meters = 0.01
    element_id: UUID | None = None
    vertex_ids: tuple[UUID, UUID] | None = None
    image_points: tuple[UUID, Point2, Point2] | None = None
    """(image_id, p1, p2) en pixels."""
    confirmed_by_user: bool = True
    created_at: datetime = Field(default_factory=now_utc)
    note: str | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> Measurement:
        targets = [self.element_id, self.vertex_ids, self.image_points]
        if sum(t is not None for t in targets) != 1:
            msg = "une mesure cible exactement un élément, deux sommets ou deux points image"
            raise ValueError(msg)
        if self.vertex_ids is not None and self.vertex_ids[0] == self.vertex_ids[1]:
            msg = "les deux sommets d'une mesure doivent être distincts"
            raise ValueError(msg)
        return self


class Calibration(StrictModel):
    """Résultat d'une résolution d'échelle : mesures utilisées, résidus, incohérences."""

    id: UUID = Field(default_factory=uuid4)
    level_id: UUID
    measurement_ids: list[UUID] = Field(min_length=1)
    scale_factor: float = Field(gt=0.0)
    """Facteur appliqué au plan précédent (1.0 s'il était déjà métrique)."""
    residuals_m: dict[UUID, float] = Field(default_factory=dict)
    rms_m: float = Field(ge=0.0)
    inconsistent: list[UUID] = Field(default_factory=list)
    """Mesures dont le résidu dépasse la tolérance."""
    created_at: datetime = Field(default_factory=now_utc)


class ConstraintKind(StrEnum):
    PARALLEL = "parallel"
    ORTHOGONAL = "orthogonal"
    FIXED_LENGTH = "fixed_length"
    FIXED_ANGLE = "fixed_angle"
    COLLINEAR = "collinear"
    KEEP_CLEAR = "keep_clear"
    MIN_PASSAGE = "min_passage"
    NO_OVERLAP = "no_overlap"
    MAX_HEIGHT_UNDER_SLOPE = "max_height_under_slope"


class Constraint(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    kind: ConstraintKind
    element_ids: list[UUID] = Field(min_length=1)
    params: dict[str, float] = Field(default_factory=dict)
    hard: bool = True
    """Dur = violation bloquante ; souple = pénalité de score."""
    source: Source = Source.USER_DEFINED
    enabled: bool = True


JsonPatch = list[dict[str, JsonValue]]
"""Liste d'opérations JSON Patch (RFC 6902)."""


class HistoryEntry(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=now_utc)
    actor: str
    """"user", "pipeline:<step>" ou "ai:<model>"."""
    label: str
    """Texte affichable : "Déplacer le sommet", "Calibrer"."""
    patch: JsonPatch
    inverse_patch: JsonPatch
    run_id: UUID | None = None


class Material(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    roughness: float = Field(default=0.6, ge=0.0, le=1.0)
    metallic: float = Field(default=0.0, ge=0.0, le=1.0)
    texture: str | None = None
    """Chemin relatif dans blender/materials ou dans le catalogue."""
    license: Literal["open_source", "cc0", "cc_by", "commercial_restricted", "unknown"] = "cc0"


LevelKind = Literal["ground", "upper", "attic", "basement"]
"""Sorte de niveau (R2) : rez-de-chaussée, étage, combles / grenier, sous-sol."""
LEVEL_DEFAULT_NAMES: dict[str, str] = {
    "ground": "Rez-de-chaussée",
    "upper": "Étage",
    "attic": "Grenier",
    "basement": "Sous-sol",
}
"""Nom donné au niveau quand sa sorte est connue et que l'utilisateur ne l'a pas renommé."""


class RoomSize(StrictModel):
    length_m: float
    width_m: float
    height_m: float


ScaleSource = Literal["reference", "camera_height", "default"]


class ScaleBasis(StrictModel):
    """KAN-66 : sur quoi repose l'échelle d'un plan estimé depuis une photo, dit tel quel à
    l'utilisateur (« Échelle calée sur votre mesure de 2,04 m (porte) »)."""

    source: ScaleSource
    """Mesure de référence, hauteur de prise de vue saisie, ou hauteur supposée par défaut."""
    text: str
    image_id: UUID
    segment_id: UUID | None = None
    """La mesure de référence utilisée (source « reference »)."""
    factor: float | None = Field(default=None, gt=0.0)
    """Mètres par unité du modèle de profondeur ; None sans profondeur gardée (la photo est
    à réestimer avant de pouvoir y mesurer)."""
    before: RoomSize | None = None
    """Dimensions de la pièce avant la mesure (source « reference »), si une pièce existe."""
    previous: ScaleBasis | None = None
    """Échelle d'avant la mesure (hauteur de prise de vue) : retirer la mesure y revient."""
    camera_height_m: float | None = Field(default=None, gt=0.0)
    """Hauteur de prise de vue de l'estimation (saisie ou par défaut)."""


class Level(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = "Rez-de-chaussée"
    kind: LevelKind | None = None
    """Sorte de niveau, déduite de la reconnaissance (combles) ou choisie ; None = pas encore
    connue (le nom par défaut reste « Rez-de-chaussée »)."""
    elevation: float = 0.0
    """Z du sol, en mètres."""
    height: Meters = 2.50
    height_source: Source = Source.ESTIMATED
    vertices: dict[UUID, Vertex] = Field(default_factory=dict)
    elements: dict[UUID, Element] = Field(default_factory=dict)
    background_image: UUID | None = None
    background_transform: dict[str, float] = Field(default_factory=dict)
    """Échelle, translation, rotation du fond de photo dans l'éditeur 2D."""
    north_deg: float = Field(default=90.0, ge=0.0, lt=360.0)
    """Direction du nord dans le repère du plan, en degrés depuis +X (antihoraire) : 90 = le
    nord est en haut du plan (défaut). Sert à l'éclairage solaire des rendus."""
    variant_of: UUID | None = None
    """T7e : None = un étage du projet (empilé en 3D à son altitude) ; sinon l'étage dont ce
    plan est une variante (autre version du même lieu, jamais empilée, vue seule)."""
    scale_basis: ScaleBasis | None = None
    """KAN-66 : origine de l'échelle quand le plan a été estimé depuis une photo."""

    @model_validator(mode="after")
    def _keys_match_ids(self) -> Level:
        _check_keys(self.vertices, "vertices")
        _check_keys(self.elements, "elements")
        return self


class PrivacySettings(StrictModel):
    strip_exif: bool = True
    keep_exif_focal: bool = True
    """Conserver la focale équivalente 35 mm (un nombre, utile à la reconstruction)."""
    allow_remote_models: bool = False
    """Réglage général : les modèles distants sont-ils envisageables pour ce projet ?"""
    remote_consent: bool = False
    """Confirmation explicite, par projet, avant le premier envoi de données à un service
    distant. Les deux drapeaux doivent être vrais (openplan.adapters.gateway)."""
    remote_text_only: bool = False
    """Accord donné par la case de l'accueil (L2) : le texte seul part au modèle distant, jamais
    une photo ni une image (la passerelle refuse toute requête avec image)."""
    telemetry: bool = False
    design_journal: bool = True
    """Journal local des demandes (V2) : consignes, traductions, ce qui en a été gardé, corrigé
    ou annulé ; dans le dossier du projet, jamais envoyé, sans image ni coordonnée."""


class RenderSettings(StrictModel):
    """Chemin « zéro geste » (V1, point 6) : ce qui s'enchaîne après le dépôt d'un fichier."""

    auto_path: bool = True
    """Après le plan : ambiance déduite, aménagement par zones, trois vues et leurs rendus."""
    auto_finish: bool = True
    """Finition IA enchaînée après chaque rendu du chemin automatique (l'image Blender reste
    toujours à côté) ; décision d'augus du 4 octobre."""
    auto_on_drop: bool = True
    """T4 : une photo déposée sur le tableau d'un projet sans plan lance la construction du
    plan (puis le chemin automatique) sans autre geste ; coupé, « Générer maintenant »."""
    auto_inspiration: bool = True
    """Sortie B de chaque vue du chemin automatique : l'image d'inspiration, « cotes non
    garanties », mise en avant à l'arrivée (décision PO du 6 octobre)."""


class Project(StrictModel):
    schema_version: str = SCHEMA_VERSION
    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    name_auto: bool | None = None
    """Nom choisi par OpenPlan (type de pièce et date, accueil L1a) : vrai, il suit le projet
    tant que l'utilisateur ne l'a pas renommé ; faux, nom donné à la main, jamais remplacé ;
    absent (projet d'avant 0.9.0), remplacé seulement s'il est encore celui d'un fichier."""
    pending_instruction: str | None = Field(default=None, max_length=300)
    """Consigne écrite sur l'accueil sans fichier ni surface (L1b) : gardée jusqu'à ce qu'une
    pièce fermée existe, puis appliquée (chemin automatique ou bandeau de l'atelier)."""
    pending_answers: GuidedAnswers | None = None
    """Réponses guidées de la note Exigences du tableau (T1) : gardées avec la consigne,
    lues par le chemin automatique, effacées quand il les applique."""
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    units_display: Literal["m", "cm", "mm"] = "m"
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    render: RenderSettings = Field(default_factory=RenderSettings)
    images: dict[UUID, ImageRef] = Field(default_factory=dict)
    levels: dict[UUID, Level] = Field(default_factory=dict)
    measurements: dict[UUID, Measurement] = Field(default_factory=dict)
    calibrations: list[Calibration] = Field(default_factory=list)
    constraints: dict[UUID, Constraint] = Field(default_factory=dict)
    materials: dict[UUID, Material] = Field(default_factory=dict)
    history: list[HistoryEntry] = Field(default_factory=list)
    history_cursor: int = Field(default=0, ge=0)
    """Nombre d'entrées appliquées ; les entrées au-delà sont « à rétablir »."""

    @model_validator(mode="after")
    def _consistent(self) -> Project:
        _check_keys(self.images, "images")
        _check_keys(self.levels, "levels")
        _check_keys(self.measurements, "measurements")
        _check_keys(self.constraints, "constraints")
        _check_keys(self.materials, "materials")
        if self.history_cursor > len(self.history):
            msg = f"history_cursor={self.history_cursor} dépasse l'historique ({len(self.history)})"
            raise ValueError(msg)
        return self


def _check_keys(mapping: Mapping[UUID, object], field: str) -> None:
    for key, value in mapping.items():
        value_id = getattr(value, "id", None)
        if value_id != key:
            msg = f"{field}: la clé {key} ne correspond pas à l'id {value_id}"
            raise ValueError(msg)
