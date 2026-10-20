"""Commentaires de l'espace de travail (KAN-51) : des fils épinglés n'importe où, ou sur un
élément (carte, photo, rendu, cadre du plan). Gardés à part du tableau (`comments.json` à
côté de `board.json`), la même chose en local et avec les comptes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from openplan.schemas.common import StrictModel, now_utc

MAX_TEXT = 2000
MAX_THREADS = 2000
MAX_MESSAGES = 500
LOCAL_AUTHOR = "local"
"""L'auteur en mode local : « Vous » à l'écran ; « Utilisateur local » une fois en ligne."""


class CommentPoint(StrictModel):
    x: float = Field(ge=-1e6, le=1e6)
    y: float = Field(ge=-1e6, le=1e6)


class CommentTarget(StrictModel):
    """Où l'épingle est posée : un point de l'espace de travail, ou un élément (`item_id`) ;
    `at` est alors la position de l'épingle par rapport au coin de l'élément, et `last` la
    dernière position connue de l'élément (le fil reste là s'il est supprimé : « détaché »)."""

    at: CommentPoint
    item_id: UUID | None = None
    last: CommentPoint | None = None


class CommentMessage(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    author: str = Field(max_length=120)
    """`local`, ou l'id du compte ; jamais une adresse."""
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    created_at: datetime = Field(default_factory=now_utc)
    edited_at: datetime | None = None


class CommentThread(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    target: CommentTarget
    resolved: bool = False
    resolved_at: datetime | None = None
    messages: list[CommentMessage] = Field(default_factory=list, max_length=MAX_MESSAGES)


class CommentFile(StrictModel):
    version: Literal[1] = 1
    threads: list[CommentThread] = Field(default_factory=list, max_length=MAX_THREADS)
