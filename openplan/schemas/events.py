"""Journal d'un projet (KAN-51 § 8, KAN-52, partagé avec KAN-13) : une ligne de `events.jsonl`
par geste notable. Qui, quoi, où, quand ; jamais le texte d'un commentaire, jamais une adresse.

Les lignes écrites par KAN-51 (sans `id`) restent lisibles : le lecteur leur donne un id stable
tiré de la ligne elle-même (`pipeline.journal`)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from openplan.schemas.common import StrictModel, now_utc

EventType = Literal[
    "comment.created",
    "comment.replied",
    "comment.resolved",
    "comment.reopened",
    "member.joined",
    "render.done",
    "board.changed",
    "plan.changed",
]
MAX_LABEL = 200


class ProjectEvent(StrictModel):
    id: UUID = Field(default_factory=uuid4)
    type: EventType
    project_id: UUID
    actor: str = Field(max_length=120)
    """`local`, ou l'id du compte ; pour un rendu, celui qui l'a lancé."""
    thread_id: UUID | None = None
    item_id: UUID | None = None
    level_id: UUID | None = None
    run_id: UUID | None = None
    label: str | None = Field(default=None, max_length=MAX_LABEL)
    """L'objet tel qu'il s'appelait au moment du geste (« Photo 2 »), ou le geste sur le plan
    (« Ajouter un mur ») ; jamais un texte libre de commentaire."""
    item_kind: str | None = Field(default=None, max_length=40)
    """KAN-58 : la sorte de l'élément touché quand il n'y en a qu'un (« une note »)."""
    added: int | None = Field(default=None, ge=0)
    changed: int | None = Field(default=None, ge=0)
    removed: int | None = Field(default=None, ge=0)
    """KAN-58 : le détail d'une modification de l'espace de travail (éléments posés, modifiés,
    retirés)."""
    at: datetime = Field(default_factory=now_utc)
