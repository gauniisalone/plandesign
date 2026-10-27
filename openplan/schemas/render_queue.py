"""File des rendus réalistes (KAN-89) : ce que le serveur et le poste de rendu s'échangent.

Le poste ne reçoit que ces messages, validés ici (champs inconnus refusés), et trois fichiers
JSON de taille plafonnée (projet, bake, caméra), eux aussi validés par leur schéma. Aucun champ
ne porte une commande, un chemin ou une adresse : le poste n'exécute rien de ce qu'il reçoit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from openplan.schemas.common import StrictModel

RequestState = Literal["pending", "taken", "done", "failed", "expired"]
WorkerState = Literal["idle", "busy", "paused", "waiting"]
"""`waiting` : le poste passe son tour (verrou du travail lourd, GPU occupé, tests en cours)."""
FailureReason = Literal["unavailable", "vram", "timeout", "engine_error", "invalid_input"]

MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_BAKE_BYTES = 8 * 1024 * 1024
MAX_CAMERA_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
"""Plafond d'un rendu déposé (PNG) ; l'intergiciel refuse un envoi plus gros avant de le lire."""
MAX_IMAGE_SIDE = 2048
"""Plus grand côté d'un rendu déposé (Z-Image rend en 1024 px de grand côté)."""
JSON_FILES: dict[str, int] = {
    "project": MAX_PROJECT_BYTES,
    "bake": MAX_BAKE_BYTES,
    "camera": MAX_CAMERA_BYTES,
}
"""Fichiers qu'un poste peut télécharger pour une demande, et leur plafond (octets)."""
MAX_SEED = 2**31 - 1
MAX_STYLE_IMAGES = 6
"""KAN-97 : images de style envoyées au poste pour un rendu, au plus."""
MAX_STYLE_IMAGE_BYTES = 2 * 1024 * 1024
"""KAN-97 : une image de style servie au poste (JPEG réduit à STYLE_IMAGE_SIDE), au plus."""
STYLE_IMAGE_SIDE = 512
MAX_STYLE_WORDS = 8
StyleName = Annotated[str, Field(min_length=1, max_length=120)]
StyleKey = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z -]*$")]
"""Un mot du vocabulaire de style (`pipeline.style_words`), tel que le poste le renvoie."""
TYPICAL_SECONDS = 120.0
"""KAN-91 : durée annoncée d'un rendu tant que le journal n'en a pas assez (« ~2 min »)."""


class WorkerJob(StrictModel):
    """Une demande prise par le poste. `lease` accompagne chaque appel suivant : une demande
    reprise par la file (poste tombé) ne peut plus être déposée par l'ancien bail."""

    request_id: UUID
    lease: UUID
    created_at: datetime
    seed: int | None = Field(default=None, ge=0, le=MAX_SEED)
    """KAN-91 : tirage d'une variante (même vue, autre tirage) ; absent du message pour un
    premier rendu, qui garde le tirage par défaut du moteur."""
    style: list[StyleName] | None = Field(default=None, max_length=MAX_STYLE_IMAGES)
    """KAN-97 : les noms des images de style (cartes Inspirations reliées à la carte Rendus,
    C1), dans l'ordre de `/style/{k}` ; envoyé seulement à un poste qui annonce `STYLE` (un
    ancien poste refuserait ce champ)."""


class WorkerClaim(StrictModel):
    """L'appel périodique du poste (toutes les 10 à 15 s) : il vaut battement de cœur. `idle`
    prend une demande s'il y en a ; `paused` ou `waiting` (le poste passe son tour) ne prend
    rien et dit seulement que le poste est là."""

    state: Literal["idle", "paused", "waiting"] = "idle"
    version: str = Field(default="", max_length=32, pattern=r"^[A-Za-z0-9_.+-]*$")


class WorkerResult(StrictModel):
    """Ce que le poste dit de son rendu, avec l'image (PNG)."""

    seconds: float = Field(ge=0.0, le=6 * 3600)
    vram_peak_mib: int = Field(ge=0, le=1_000_000)
    width: int = Field(ge=16, le=MAX_IMAGE_SIDE)
    height: int = Field(ge=16, le=MAX_IMAGE_SIDE)
    engine: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_.-]+$")
    style_words: list[StyleKey] | None = Field(default=None, max_length=MAX_STYLE_WORDS)
    """KAN-97 : les mots de style retenus (vocabulaire fermé, anglais), [] si aucun ; envoyé
    seulement à un serveur qui sert `STYLE` (un ancien serveur refuserait ce champ)."""


class WorkerFailure(StrictModel):
    """Un code seulement : aucun texte libre (ni chemin, ni trace) ne quitte le poste."""

    reason: FailureReason


class RealisticRequest(StrictModel):
    """Une demande, telle que l'interface la montre."""

    id: UUID
    level_id: UUID
    camera_id: UUID
    state: RequestState
    created_at: datetime
    finished_at: datetime | None = None
    expires_at: datetime
    seconds: float | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None
    """Phrase en français pour un échec ; jamais un détail technique."""
    image: bool = False
    taken_at: datetime | None = None
    """KAN-91 : début du calcul (barre d'avancement de la galerie)."""
    photo_id: UUID | None = None
    """KAN-91 (D5) : la photo dont la caméra estimée est la vue de ce rendu ; None pour un
    point de vue libre. L'avant/après avec cette photo est alors fidèle."""
    guided_by: list[str] | None = None
    """KAN-97 : « Guidé par : … ». Les mots de style retenus par le poste, en français, ou les
    noms des images d'inspiration (voir `guided_kind`) ; [] : aucune inspiration reliée ; None :
    rendu d'avant KAN-97."""
    guided_kind: Literal["words", "unread", "no_style"] | None = None
    """`words` : mots lus ; `unread` : noms des images, non lues par le poste (ancien poste,
    SigLIP2 absent) ; `no_style` : noms des images, lues sans style net retenu."""


class RealisticCreate(StrictModel):
    camera_id: UUID


class RealisticFromPhoto(StrictModel):
    """KAN-91 (D5) : rendre le point de vue exact d'une photo réelle (sa caméra estimée)."""

    image_id: UUID


class RealisticCover(StrictModel):
    """KAN-91 : le rendu choisi comme couverture du projet (None : choix automatique)."""

    cover_id: UUID | None = None


class RealisticReaction(StrictModel):
    """KAN-91 : une pastille de réaction (KAN-78) sur un rendu réaliste."""

    item_id: UUID
    """L'identifiant du rendu (de la demande)."""
    key: str
    emoji: str
    count: int
    mine: bool
    names: list[str]


class RealisticStatus(StrictModel):
    """État du service pour la personne connectée (cadre Rendus)."""

    allowed: bool
    worker: Literal["online", "offline", "paused", "busy", "waiting"] = "offline"
    """`offline` : pas de battement de cœur récent ; le rendu sera fait à son retour.
    `waiting` : en ligne, mais le poste finit un autre travail (verrou, GPU, tests)."""
    quota: int = 0
    used_today: int = 0


class RealisticList(StrictModel):
    status: RealisticStatus
    requests: list[RealisticRequest]
    typical_seconds: float = TYPICAL_SECONDS
    """KAN-91 : médiane des derniers rendus faits (journal du poste), sinon ~2 min."""
    cover_id: UUID | None = None
    """KAN-91 : le rendu choisi comme couverture du projet (liste « Mes projets »)."""
    reactions: list[RealisticReaction] = Field(default_factory=list)


FAILURE_TEXT: dict[str, str] = {
    "unavailable": "Le moteur de rendu n'est pas prêt sur le poste de rendu.",
    "vram": "Le poste de rendu manquait de mémoire : réessayez plus tard.",
    "timeout": "Le rendu a pris trop de temps et a été arrêté.",
    "engine_error": "Le rendu a échoué sur le poste de rendu.",
    "invalid_input": "La vue n'a pas pu être lue par le poste de rendu.",
    "abandoned": "Le poste de rendu ne l'a pas terminé.",
    "lost": "Le poste de rendu a perdu la connexion, relancez le rendu.",
}

CAPABILITIES_HEADER = "X-Render-Capabilities"
"""Réponse à `claim` : les capacités annoncées par le poste que ce serveur sait servir (jetons
joints par « + »). En en-tête, pas dans `WorkerJob` (strict chez un ancien poste) : un ancien
poste ou un ancien serveur l'ignore. Le poste n'use d'une capacité que si elle y figure."""
HEARTBEAT = "heartbeat"
"""KAN-96 : le poste bat pour sa prise pendant le calcul et l'envoi (bail court)."""
STYLE = "style"
"""KAN-97 : le poste lit les images de style (`WorkerJob.style`, `/style/{k}`) et renvoie ses
mots (`WorkerResult.style_words`)."""
BEAT_EVERY_S = 30
"""KAN-96 : cadence du battement d'un rendu pris (calcul et envoi compris)."""


def parse_capabilities(text: str) -> frozenset[str]:
    """Jetons séparés par « + » ; un jeton inconnu est ignoré par celui qui lit."""
    return frozenset(t for t in text.split("+") if t)


def capabilities(claim: WorkerClaim | None) -> frozenset[str]:
    """Les capacités que le poste annonce dans `WorkerClaim.version` (KAN-96, KAN-97) : un seul
    mécanisme, déjà accepté par le serveur de production (un nouveau champ du modèle strict
    vaudrait un 422 à un poste en avance sur la production)."""
    return frozenset() if claim is None else parse_capabilities(claim.version)
