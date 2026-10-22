"""File des rendus réalistes (KAN-89) : ce que le serveur et le poste de rendu s'échangent.

Le poste ne reçoit que ces messages, validés ici (champs inconnus refusés), et trois fichiers
JSON de taille plafonnée (projet, bake, caméra), eux aussi validés par leur schéma. Aucun champ
ne porte une commande, un chemin ou une adresse : le poste n'exécute rien de ce qu'il reçoit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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


class WorkerJob(StrictModel):
    """Une demande prise par le poste. `lease` accompagne chaque appel suivant : une demande
    reprise par la file (poste tombé) ne peut plus être déposée par l'ancien bail."""

    request_id: UUID
    lease: UUID
    created_at: datetime


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


class RealisticCreate(StrictModel):
    camera_id: UUID


class RealisticStatus(StrictModel):
    """État du service pour la personne connectée (cadre Rendus)."""

    allowed: bool
    worker: Literal["online", "offline", "paused", "busy"] = "offline"
    """`offline` : pas de battement de cœur récent ; le rendu sera fait à son retour."""
    quota: int = 0
    used_today: int = 0


class RealisticList(StrictModel):
    status: RealisticStatus
    requests: list[RealisticRequest]


FAILURE_TEXT: dict[str, str] = {
    "unavailable": "Le moteur de rendu n'est pas prêt sur le poste de rendu.",
    "vram": "Le poste de rendu manquait de mémoire : réessayez plus tard.",
    "timeout": "Le rendu a pris trop de temps et a été arrêté.",
    "engine_error": "Le rendu a échoué sur le poste de rendu.",
    "invalid_input": "La vue n'a pas pu être lue par le poste de rendu.",
    "abandoned": "Le poste de rendu ne l'a pas terminé.",
}
