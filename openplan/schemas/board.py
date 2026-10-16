"""Tableau du projet (B1) : disposition libre des photos, notes, rendus, plan, cadres et
fichiers joints. Gardé à part de la géométrie (`board.json` à côté de `project.json`) : une
sauvegarde du tableau ne peut pas écraser un geste sur le plan, et inversement.

Position et taille d'un élément (`x`, `y`, `w`, `h`, en pixels du tableau) ne disent que
comment il est montré : redimensionner le cadre du plan ne change jamais ses cotes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, field_validator

from openplan.schemas.common import StrictModel, now_utc

BoardKind = Literal[
    "photo",
    "render",
    "note",
    "plan",
    "frame",
    "file",
    "video",
    "pdf",
    "link",
    "contextual",
    "requirements",
]
"""`requirements` (T1) : la note Exigences, une seule par tableau (rôle `prompt`) ; son contenu
vit dans le projet (consigne et réponses gardées), le tableau ne garde que sa place."""
BoardRole = Literal[
    "prompt", "photos", "inspirations", "renders", "inspiration", "files", "moodboard"
]
"""`photos` : cadre des photos réelles de la pièce (→ plan) ; `inspirations` (T2) : cadre des
images d'ambiance, jointes au projet et cochées Référence ; `inspiration` : une image
d'inspiration générée sans plan (rendu de secours)."""
MediaKind = Literal["image", "video", "pdf", "file"]
MAX_ITEMS = 2000
MAX_SIDE = 20000.0
MAX_URL = 2000
_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f<>\"]+", re.IGNORECASE)


class BoardItem(StrictModel):
    """Un élément du tableau. `ref` désigne ce qu'il montre : l'id d'une image (photo), d'un
    rendu ou d'une image d'inspiration (render), d'un niveau (plan), d'un fichier joint
    (file) ; une note et un cadre n'en ont pas."""

    id: UUID = Field(default_factory=uuid4)
    kind: BoardKind
    x: float = Field(ge=-1e6, le=1e6)
    y: float = Field(ge=-1e6, le=1e6)
    w: float = Field(gt=0.0, le=MAX_SIDE)
    h: float = Field(gt=0.0, le=MAX_SIDE)
    z: int = 0
    locked: bool = False
    frame: UUID | None = None
    """Le cadre qui le contient (il bouge avec lui)."""
    ref: str | None = Field(default=None, max_length=80)
    text: str = Field(default="", max_length=4000)
    """Texte d'une note, titre d'un cadre, nom d'un fichier joint."""
    role: BoardRole | None = None
    """Ce que le tableau a posé de lui-même : la note de la demande, les cadres Photos,
    Rendus et Fichiers, une image d'inspiration sans plan."""
    url: str | None = Field(default=None, max_length=MAX_URL)
    """Adresse d'un lien (C1), http ou https seulement ; la page n'est jamais récupérée."""
    reference: bool = False
    """Coché « Référence » (A2) : cette image guide l'allure des rendus contextualisés."""
    kept: bool = False
    """« À conserver » (A3) : une variante retenue, marquée sur sa carte."""
    comment: str = Field(default="", max_length=2000)
    """Commentaire attaché à la carte (A3), montré sous ses métadonnées."""

    @field_validator("url")
    @classmethod
    def _web_only(cls, value: str | None) -> str | None:
        if value is not None and not _URL.fullmatch(value):
            msg = "adresse de lien : http:// ou https:// seulement, sans espace"
            raise ValueError(msg)
        return value


class BoardView(StrictModel):
    """Où regardait le tableau : retrouvé à la réouverture et au retour du plan."""

    x: float = 0.0
    y: float = 0.0
    zoom: float = Field(default=1.0, gt=0.05, le=8.0)


class Board(StrictModel):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    """Numéro de la sauvegarde : un envoi parti d'une révision plus ancienne (deux onglets
    ouverts) est refusé en 409, rien n'est écrasé en silence."""
    items: list[BoardItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    view: BoardView = Field(default_factory=BoardView)
    dismissed: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    """Références retirées à la main : la synchronisation ne les remet pas."""
    updated_at: datetime = Field(default_factory=now_utc)


class MediaRef(StrictModel):
    """Un fichier joint au projet (B1) : gardé tel quel dans `media/`, jamais exécuté ; servi
    en pièce jointe, sauf les images courantes, montrées."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1, max_length=200)
    media_type: str = Field(max_length=120)
    size: int = Field(ge=0)
    path: str
    imported_at: datetime = Field(default_factory=now_utc)
    kind: MediaKind = "file"
    """Ce qu'il est, d'après ses premiers octets (C1) : image, vidéo lisible (mp4, webm),
    PDF, ou autre fichier."""
    thumb: str | None = None
    """Vignette rendue sur la machine (première page d'un PDF), si elle a pu l'être."""
