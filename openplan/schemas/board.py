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
NoteColor = Literal["yellow", "orange", "pink", "violet", "blue", "green", "gray", "white"]
"""KAN-65 : les huit couleurs d'un post-it (une note de l'espace de travail)."""
MAX_ITEMS = 2000
MAX_SIDE = 20000.0
MAX_URL = 2000
MAX_TAGS = 64
_URL = re.compile(r"https?://[^\s\x00-\x1f\x7f<>\"]+", re.IGNORECASE)
_TAG = re.compile(r"[ms]:[^\s\x00-\x1f\x7f]{1,58}")
LinkKind = Literal["feeds_plan", "guides_style", "renders_view"]
"""KAN-91 : les trois liens entre cartes. `feeds_plan` : carte Photos → plan (ses photos le
construisent) ; `guides_style` : carte Inspirations → carte Rendus (ses images guident
l'ambiance) ; `renders_view` : plan → carte Rendus (la géométrie rendue)."""
LINK_ROLES: dict[str, tuple[str, str]] = {
    "feeds_plan": ("photos", "plan"),
    "guides_style": ("inspirations", "renders"),
    "renders_view": ("plan", "renders"),
}
"""Le rôle de la source et de la cible de chaque lien (`plan` : un élément de sorte plan ;
sinon le rôle d'un cadre). Le type d'un lien se déduit des deux cartes, jamais choisi."""


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
    """« Référence » (A2) : cette image guide l'allure des rendus. Depuis KAN-91, dérivé de
    l'appartenance et posé par le serveur : vrai si et seulement si l'image est membre d'une
    carte Inspirations (gardé pour les projets d'avant)."""
    kept: bool = False
    """« À conserver » (A3) : une variante retenue, marquée sur sa carte."""
    comment: str = Field(default="", max_length=2000)
    """Commentaire attaché à la carte (A3), montré sous ses métadonnées."""
    color: NoteColor | None = None
    """KAN-65 : couleur d'un post-it ; absente (note d'avant KAN-65) = jaune, sans migration.
    Un champ de l'élément : elle s'écrit élément par élément, comme le reste (B2c)."""
    updated_at: datetime | None = None
    """B2c : posé par le serveur à chaque écriture de cet élément (la dernière gagne) ; ce que
    le client envoie est ignoré. Absent d'un tableau d'avant B2c."""
    updated_by: str | None = Field(default=None, max_length=120)
    """B2c : l'acteur de cette écriture (`user`, `user:<id>`), posé par le serveur."""
    ignored_tags: list[str] = Field(default_factory=list, max_length=MAX_TAGS)
    """KAN-91 : étiquettes de matière ou de style désactivées sur cette image (`m:terracotta`,
    `s:japandi`) ; elles ne guident plus les rendus. Pas de couleur (décision C2)."""

    @field_validator("ignored_tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        for tag in value:
            if not _TAG.fullmatch(tag):
                msg = "étiquette : « m:<matière> » ou « s:<style> », 60 caractères au plus"
                raise ValueError(msg)
        return list(dict.fromkeys(value))

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


class BoardLink(StrictModel):
    """KAN-91 : un lien d'une carte à une autre. Ce qui entre dans une génération, ce sont les
    membres des cartes reliées (décision C1) : une image hors carte ne compte jamais."""

    id: UUID = Field(default_factory=uuid4)
    source: UUID
    target: UUID
    kind: LinkKind
    """Vérifié contre les rôles des deux cartes (`LINK_ROLES`)."""
    auto: bool = False
    """Posé par la synchronisation (zéro geste), pas tiré à la main."""
    updated_at: datetime | None = None
    """Posé par le serveur à chaque écriture du lien (la dernière gagne, comme B2c)."""
    updated_by: str | None = Field(default=None, max_length=120)

    @property
    def pair(self) -> str:
        return f"{self.source}>{self.target}"


class Board(StrictModel):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    """Numéro de la sauvegarde : un envoi parti d'une révision plus ancienne (deux onglets
    ouverts) est refusé en 409, rien n'est écrasé en silence."""
    items: list[BoardItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    view: BoardView = Field(default_factory=BoardView)
    dismissed: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    """Références retirées à la main : la synchronisation ne les remet pas."""
    links: list[BoardLink] = Field(default_factory=list, max_length=MAX_ITEMS)
    """KAN-91 : les liens entre cartes (photos → plan, plan → rendus, inspirations → rendus)."""
    cut_links: list[str] = Field(default_factory=list, max_length=MAX_ITEMS)
    """KAN-91 : paires « source>cible » coupées à la main : la synchronisation ne les recrée
    pas (même rôle que `dismissed` pour les éléments)."""
    updated_at: datetime = Field(default_factory=now_utc)


class BoardPatch(StrictModel):
    """B2c : les éléments écrits par un geste, en une fois. Chaque élément de `set` remplace
    (ou ajoute) celui de même id, `delete` retire ; la dernière écriture reçue gagne, élément
    par élément, sans révision ni 409. `view` : où regarde l'espace de travail (facultatif)."""

    set: list[BoardItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    delete: list[UUID] = Field(default_factory=list, max_length=MAX_ITEMS)
    view: BoardView | None = None
    links_set: list[BoardLink] = Field(default_factory=list, max_length=MAX_ITEMS)
    """KAN-91 : liens posés ou modifiés, lien par lien (la dernière écriture gagne)."""
    links_delete: list[UUID] = Field(default_factory=list, max_length=MAX_ITEMS)
    """KAN-91 : liens coupés ; leur paire est notée dans `cut_links`."""


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
    placed: bool = False
    """Envoyé par l'espace de travail, qui pose lui-même sa carte (dans Inspirations, ou là où
    le fichier a été déposé) : la synchronisation ne le range jamais dans « Fichiers ». Sans
    cela, une lecture du tableau entre l'envoi et l'écriture de la carte en faisait une copie
    dans un cadre « Fichiers » créé exprès (25 oct.). Faux : joint par l'API (C1)."""
