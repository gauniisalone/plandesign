"""Réactions aux éléments de l'espace de travail (KAN-78) : un jeu fixe de six emojis, une
réaction par personne, par élément et par emoji. Gardées à part du tableau (`reactions.json` à
côté de `board.json` et `comments.json`), la même chose en local et avec les comptes ; un code
d'avant KAN-78 ne lit pas ce fichier, il l'ignore donc sans erreur."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from openplan.schemas.common import StrictModel, now_utc

ReactionKey = Literal["up", "heart", "love", "think", "down", "fire"]
EMOJI: dict[str, str] = {
    "up": "👍",
    "heart": "❤️",
    "love": "😍",
    "think": "🤔",
    "down": "👎",
    "fire": "🔥",
}
"""L'ordre du sélecteur et des pastilles."""
MAX_REACTIONS = 20000


class Reaction(StrictModel):
    item_id: UUID
    key: ReactionKey
    author: str = Field(max_length=120)
    """`local`, ou l'id du compte ; jamais une adresse."""
    at: datetime = Field(default_factory=now_utc)


class ReactionFile(StrictModel):
    version: Literal[1] = 1
    reactions: list[Reaction] = Field(default_factory=list, max_length=MAX_REACTIONS)
