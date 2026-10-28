"""Types communs du format de scène : sources, provenance, incertitude, primitives.

Conventions (docs/architecture/01) : longueurs en mètres, angles en radians,
repère 2D du niveau (X droite, Y haut), repère 3D Z vers le haut.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

SCHEMA_VERSION = "0.21.0"

DECODED_IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")
"""KAN-113 : les seuls formats que Pillow décode (`Image.open(…, formats=…)`, partout). Aucun
autre décodeur (SPIDER, TIFF, EPS, GIF…) n'est jamais essayé, même pour un fichier renommé
en .jpg : la surface d'attaque des décodeurs de Pillow s'arrête à ces trois-là."""

Meters = Annotated[float, Field(ge=0.0)]
"""Longueur en mètres, jamais négative."""

Radians = float
"""Angle en radians."""

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
"""Niveau de confiance dans [0, 1]. 1.0 est réservé aux sources measured et user_defined."""


def now_utc() -> datetime:
    """Horodatage courant, toujours en UTC et conscient du fuseau."""
    return datetime.now(UTC)


class Source(StrEnum):
    """Origine d'une donnée. L'ordre de priorité est donné par SOURCE_PRIORITY."""

    MEASURED = "measured"
    """Saisi ou confirmé par l'utilisateur avec une valeur métrique."""
    OBSERVED = "observed"
    """Détecté directement dans une image."""
    ESTIMATED = "estimated"
    """Estimation algorithmique."""
    INFERRED = "inferred"
    """Déduit du contexte (ex. mur caché fermé par continuité)."""
    GENERATED = "generated"
    """Créé par l'IA (aménagement)."""
    USER_DEFINED = "user_defined"
    """Ajouté manuellement, sans mesure métrique."""


_PRIORITY_ORDER: tuple[Source, ...] = (
    Source.GENERATED,
    Source.INFERRED,
    Source.ESTIMATED,
    Source.OBSERVED,
    Source.USER_DEFINED,
    Source.MEASURED,
)

SOURCE_PRIORITY: dict[Source, int] = {s: i for i, s in enumerate(_PRIORITY_ORDER)}
"""Rang de priorité : plus il est élevé, moins une étape automatique peut l'écraser."""

USER_AUTHORITATIVE: frozenset[Source] = frozenset({Source.MEASURED, Source.USER_DEFINED})
"""Sources qui font autorité : seules elles admettent confidence == 1.0."""


def source_rank(source: Source) -> int:
    """Rang de priorité d'une source (0 = la plus faible)."""
    return SOURCE_PRIORITY[source]


class StrictModel(BaseModel):
    """Base de tous les modèles du format : champs inconnus refusés, assignations validées."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Provenance(StrictModel):
    """D'où vient une donnée : images, étape de pipeline, modèle, exécution."""

    image_ids: list[UUID] = Field(default_factory=list)
    step: str | None = None
    """Nom de l'étape de pipeline, ex. "detection.heuristic_box"."""
    model: str | None = None
    """Identifiant du registre de modèles, ex. "depth_anything_v2_small@1.0"."""
    run_id: UUID | None = None
    created_at: datetime = Field(default_factory=now_utc)


class Uncertainty(StrictModel):
    """Incertitude 1-sigma en mètres (ou radians pour un angle). None = inconnue, pas zéro."""

    sigma: float | None = Field(default=None, ge=0.0)
    interval: tuple[float, float] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _interval_ordered(self) -> Uncertainty:
        if self.interval is not None and self.interval[0] > self.interval[1]:
            msg = f"interval désordonné : {self.interval}"
            raise ValueError(msg)
        return self


class Point2(StrictModel):
    x: float
    y: float


class Point3(StrictModel):
    x: float
    y: float
    z: float


class Transform3(StrictModel):
    """Position, rotation autour de Z, échelle uniforme. Suffisant pour un objet posé."""

    position: Point3
    yaw: Radians = 0.0
    scale: float = Field(default=1.0, gt=0.0)


class BBox3(StrictModel):
    """Boîte englobante alignée sur l'objet : largeur (x), profondeur (y), hauteur (z)."""

    width: Meters
    depth: Meters
    height: Meters


class ElementBase(StrictModel):
    """Champs communs à tout élément de scène. Chaque sous-classe fixe `type` (Literal)."""

    id: UUID = Field(default_factory=uuid4)
    source: Source
    confidence: Confidence
    locked: bool = False
    visible: bool = True
    provenance: Provenance = Field(default_factory=Provenance)
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    name: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
