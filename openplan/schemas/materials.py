"""Registre des matières de rendu (D2) : textures PBR et ciel HDRI CC0, téléchargés à la demande
hors du dépôt, empreinte vérifiée, licence prouvée. Même principe que le registre des poids."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field, model_validator

from openplan.schemas.bake import Finish
from openplan.schemas.common import StrictModel

_SHA = re.compile(r"^[0-9a-f]{64}$")
SURFACE_CHANNELS = ("diff", "nor", "rough")


class MaterialFile(StrictModel):
    url: str = Field(pattern=r"^https://")
    sha256: str
    bytes: int = Field(gt=0)

    @model_validator(mode="after")
    def _sha(self) -> MaterialFile:
        if not _SHA.match(self.sha256):
            msg = f"empreinte SHA-256 invalide : {self.sha256!r}"
            raise ValueError(msg)
        return self


class MaterialEntry(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    kind: Literal["surface", "sky"]
    finish: Finish | None = None
    """Finition du bake qu'elle habille (surface) ; None pour le ciel."""
    label: str
    source: str
    page: str = Field(pattern=r"^https://")
    license: Literal["cc0"]
    """CC0 seulement : rien à créditer, rien à restreindre pour l'utilisateur."""
    license_evidence: str = Field(pattern=r"^https://")
    verified_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    tile_m: float = Field(default=1.0, gt=0.0, le=10.0)
    """Côté d'une répétition de la texture, en mètres."""
    brightness: float = Field(default=1.0, gt=0.2, le=3.0)
    """Éclaircit (> 1) ou fonce la couleur de la texture au rendu : enduit clair à partir d'une
    photo d'enduit gris, sans changer de fichier ni de licence."""
    saturation: float = Field(default=1.0, ge=0.0, le=2.0)
    """Désature (< 1) la texture au rendu : planches peintes blanchies, chaux plus neutre."""
    planks: Literal["u", "v"] | None = None
    """Sens des planches dans l'image (le long de sa largeur `u` ou de sa hauteur `v`) ; sur
    une pente, le rendu les aligne dans le sens de la pente, comme un lambris. None : pas de
    sens (enduit, pierre)."""
    files: dict[str, MaterialFile]

    @model_validator(mode="after")
    def _shape(self) -> MaterialEntry:
        if self.kind == "surface":
            if self.finish is None or set(self.files) != set(SURFACE_CHANNELS):
                msg = f"{self.id} : une surface a une finition et les fichiers {SURFACE_CHANNELS}"
                raise ValueError(msg)
        elif set(self.files) != {"hdri"} or self.finish is not None:
            msg = f"{self.id} : un ciel a un seul fichier « hdri » et pas de finition"
            raise ValueError(msg)
        return self

    @property
    def megabytes(self) -> float:
        return sum(f.bytes for f in self.files.values()) / 1e6


class MaterialRegistry(StrictModel):
    version: int = 1
    materials: list[MaterialEntry]

    @model_validator(mode="after")
    def _unique(self) -> MaterialRegistry:
        ids = [m.id for m in self.materials]
        finishes = [m.finish for m in self.materials if m.kind == "surface"]
        if len(ids) != len(set(ids)) or len(finishes) != len(set(finishes)):
            msg = "matières en double (identifiant ou finition)"
            raise ValueError(msg)
        if sum(1 for m in self.materials if m.kind == "sky") > 1:
            msg = "un seul ciel dans le registre"
            raise ValueError(msg)
        return self

    def get(self, material_id: str) -> MaterialEntry | None:
        return next((m for m in self.materials if m.id == material_id), None)
