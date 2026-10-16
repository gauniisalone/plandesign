"""Assistant qui agit sur le plan (directive 3 d'augus, cadrage `docs/architecture/07`).

Le modèle de langage traduit une phrase en **intentions typées** dans un vocabulaire fermé ;
il choisit l'intention et la cible, jamais une coordonnée : le moteur géométrique calcule
positions et règles. Chaque intention exécutée est un commit d'historique attribué à l'acteur
`assistant`, annulable, résumé et surligné dans le plan.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from openplan.schemas.common import StrictModel
from openplan.schemas.elements import StairKind
from openplan.schemas.layout import ItemDimensions, Place

ASSISTANT_ACTOR = "assistant"
"""Acteur d'historique des interventions : mêmes droits qu'un `pipeline:*` (jamais de source
`user_defined`, ne supprime pas ce que l'utilisateur a posé lui-même), donc chaque geste se
distingue de ceux de l'utilisateur et s'annule d'un clic."""

IntentKind = Literal[
    "add_furniture",
    "move_furniture",
    "remove_furniture",
    "propose_layouts",
    "set_dimension",
    "add_opening",
    "add_stairs",
    "name_room",
    "ask",
    "answer",
]
Dimension = Literal["wall", "length", "width", "height"]
"""Ce que vaut `value_m` : la longueur d'un mur ciblé, la longueur ou la largeur de la pièce,
la hauteur sous plafond."""
OpeningWord = Literal[
    "door",
    "window",
    "double_door",
    "sliding_door",
    "pocket_door",
    "folding_door",
    "french_window",
    "sliding_window",
]
"""Sortes d'ouvertures que l'assistant sait poser (E5 : la bibliothèque entière)."""
ROOM_TYPES: tuple[str, ...] = (
    "bedroom",
    "living_room",
    "kitchen",
    "bathroom",
    "office",
    "attic",
    "hallway",
    "other",
)
INTENT_LABELS: dict[str, str] = {
    "add_furniture": "ajouter un meuble",
    "move_furniture": "déplacer un meuble",
    "remove_furniture": "retirer un meuble",
    "propose_layouts": "proposer des dispositions",
    "set_dimension": "régler une dimension",
    "add_opening": "ajouter une porte ou une fenêtre",
    "add_stairs": "poser une cage d'escalier",
    "name_room": "nommer la pièce",
    "ask": "poser une question",
    "answer": "répondre",
}


class AssistantIntent(StrictModel):
    kind: IntentKind
    category: str | None = None
    """Catégorie du catalogue (ajout, ou cible par catégorie)."""
    count: int = Field(default=1, ge=1, le=10)
    dimensions_m: ItemDimensions | None = None
    """Seulement si l'utilisateur a écrit la dimension (même règle que le brief)."""
    near: list[Place] = Field(default_factory=list)
    away_from: list[Place] = Field(default_factory=list)
    target: str | None = None
    """Cible d'un déplacement ou d'un retrait, telle que dite (« le lit », « bureau »)."""
    text: str = ""
    """Question de clarification (`ask`) ou réponse en texte (`answer`)."""
    value_m: float | None = Field(default=None, gt=0.0, lt=100.0)
    """Dimension écrite par l'utilisateur (mètres) pour `set_dimension` ou largeur d'une
    ouverture."""
    dimension: Dimension | None = None
    opening: OpeningWord | None = None
    leaves: int | None = Field(default=None, ge=1, le=2)
    """Vantaux d'une porte-fenêtre (« porte-fenêtre double » : 2)."""
    name: str | None = None
    """Nom voulu pour la pièce (`name_room`)."""
    room_type: str | None = None
    """Usage de la pièce dans le vocabulaire fermé `ROOM_TYPES`."""
    stairs: StairKind | None = None
    """Type d'escalier (`add_stairs`) : droit, quart tournant, demi-tournant, hélicoïdal."""


class AssistantReply(StrictModel):
    """Ce que le modèle a compris, validé avec tolérance : le douteux est écarté et dit."""

    intents: list[AssistantIntent] = Field(default_factory=list)
    message: str = ""
    """Phrase du modèle pour l'utilisateur (courte)."""
    dropped: list[str] = Field(default_factory=list)


ActionStatus = Literal["done", "refused", "question"]


class AssistantAction(StrictModel):
    """Une intention exécutée (ou non) : ce qui a été fait, en clair, et les éléments touchés."""

    kind: IntentKind
    status: ActionStatus
    summary: str
    element_ids: list[UUID] = Field(default_factory=list)
    """Éléments à surligner dans le plan."""
    reason: str = ""
    """Pourquoi c'est refusé, ou la question posée."""
