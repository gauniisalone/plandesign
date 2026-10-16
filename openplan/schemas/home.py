"""Questions guidées de l'accueil (L2, note 12) : ce que l'utilisateur a choisi, champ par
champ. Chaque réponse donnée passe dans l'intention de la pièce comme `user_defined` et prime
sur ce que dit le texte ; une réponse vide laisse le calcul décider."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from openplan.schemas.common import StrictModel
from openplan.schemas.intent import Aspect, LightTime, Mood

Space = Literal["appartement", "maison", "studio", "piece", "combles"]
SPACE_LABELS: dict[str, str] = {
    "appartement": "appartement",
    "maison": "maison",
    "studio": "studio",
    "piece": "une pièce",
    "combles": "combles / grenier",
}


class GuidedAnswers(StrictModel):
    """Réponses aux questions guidées ; tout est facultatif."""

    space: Space | None = None
    """Quel espace ? « piece » se précise par `room_type`."""
    room_type: str | None = Field(default=None, max_length=40)
    """Pour une pièce seule : bedroom, living_room, kitchen, bathroom, office…"""
    area_m2: float | None = Field(default=None, ge=8.0, le=400.0)
    bedrooms: int | None = Field(default=None, ge=0, le=6)
    style: str | None = Field(default=None, max_length=40)
    """None = laisser choisir (déduit)."""
    aspect: Aspect | None = None
    light_time: LightTime | None = None
    mood: list[Mood] = Field(default_factory=list, max_length=2)
    furniture: list[str] = Field(default_factory=list, max_length=8)
    """Catégories du catalogue voulues (une de chaque)."""
    avoid: list[str] = Field(default_factory=list, max_length=8)
    """Catégories du catalogue à ne pas poser."""
