"""KAN-124 (MH1, MH2) : ce qui manque, calculé par l'app à partir de ce qu'elle sait déjà.

Aucun modèle ici : les règles sont déterministes, et l'assistant ne pose jamais une question
dont la réponse est connue. Ordre (KAN-131 : la seule correction justifiée des cotes lues
depuis une photo est une cote donnée par l'utilisateur) :

1. `cote_connue` : un plan estimé depuis une photo, échelle non calée sur une mesure et pas
   « fiable », et aucune cote saisie (aucun mur `measured` ou `user_defined`) ;
2. `hauteur_plafond` : hauteur ni mesurée ni saisie, hors combles (le faîtage la remplace) ;
3. `type_piece` : la pièce principale du plan n'a pas d'usage.

Chaque question peut être passée ; `assumption` dit alors ce que l'app suppose. Le style et le
budget (champs du banc) ne sont pas encore demandés : l'app n'a pas de champ où les garder
(phase 2).
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from openplan.pipeline.mainroom import main_room
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.elements import Wall
from openplan.schemas.numbers import metres
from openplan.schemas.scene import Level, Project

QuestionField = Literal["cote_connue", "hauteur_plafond", "type_piece"]
FIELDS: tuple[QuestionField, ...] = ("cote_connue", "hauteur_plafond", "type_piece")

HEIGHT_BOUNDS = (1.8, 4.0)
COTE_BOUNDS = (0.3, 20.0)
"""Bornes du banc (schema.json) : revalidées par le serveur, quoi que rende le modèle."""

ROOM_TYPE_BY_ANSWER: dict[str, str] = {
    "salon": "living_room",
    "chambre": "bedroom",
    "cuisine": "kitchen",
    "salle_de_bain": "bathroom",
    "bureau": "office",
    "combles": "attic",
    "entree": "hallway",
    "autre": "other",
}
"""Les valeurs du banc (français) vers le vocabulaire fermé de l'app (`ROOM_TYPES`)."""

ROOM_TYPE_LABELS: dict[str, str] = {
    "salon": "Séjour",
    "chambre": "Chambre",
    "cuisine": "Cuisine",
    "salle_de_bain": "Salle de bain",
    "bureau": "Bureau",
    "combles": "Combles",
    "entree": "Entrée, couloir",
    "autre": "Autre",
}

KNOWN_SOURCES = frozenset({Source.MEASURED, Source.USER_DEFINED})


class Choice(StrictModel):
    value: str
    label: str


class Question(StrictModel):
    """Une question de calage, telle que l'app la pose (le modèle ne fait que l'extraction)."""

    field: QuestionField
    text: str
    reason: str
    """Pourquoi l'app la pose : ce qu'elle sait, et ce qui manque."""
    assumption: str
    """Ce que l'app suppose si la question est passée."""
    skippable: bool = True
    unit: Literal["m"] | None = None
    minimum: float | None = None
    maximum: float | None = None
    choices: list[Choice] = Field(default_factory=list)


def plan_exists(level: Level) -> bool:
    return any(isinstance(e, Wall) for e in level.elements.values())


def _cote_known(level: Level) -> bool:
    return any(isinstance(e, Wall) and e.source in KNOWN_SOURCES for e in level.elements.values())


def question_for(project: Project, level_id: UUID, field: QuestionField) -> Question:
    """La question de ce champ, telle que l'app la pose (qu'il manque ou non)."""
    level = project.levels[level_id]
    if field == "cote_connue":
        basis = level.scale_basis
        confidence = basis.confidence if basis is not None else None
        said = f" ({confidence.label})" if confidence is not None else ""
        return Question(
            field=field,
            text="Quelle est la largeur de la pièce, d'un mur à l'autre ?",
            reason=(
                f"Les cotes sont estimées d'après les photos{said}. Une seule cote mesurée "
                "suffit à les caler."
            ),
            assumption="Les cotes restent celles estimées d'après les photos, sans calage.",
            unit="m",
            minimum=COTE_BOUNDS[0],
            maximum=COTE_BOUNDS[1],
        )
    if field == "hauteur_plafond":
        return Question(
            field=field,
            text="Quelle est la hauteur sous plafond ?",
            reason="La hauteur n'a été ni mesurée ni saisie.",
            assumption=f"Hauteur supposée : {metres(level.height)}.",
            unit="m",
            minimum=HEIGHT_BOUNDS[0],
            maximum=HEIGHT_BOUNDS[1],
        )
    return Question(
        field=field,
        text="À quoi sert cette pièce ?",
        reason="La pièce du plan n'a pas encore d'usage.",
        assumption="Pièce sans usage précisé : l'aménagement reste général.",
        choices=[Choice(value=k, label=v) for k, v in ROOM_TYPE_LABELS.items()],
    )


def _is_missing(level: Level, field: QuestionField) -> bool:
    if field == "cote_connue":
        basis = level.scale_basis
        trusted = (
            basis is not None
            and basis.confidence is not None
            and basis.confidence.level == "fiable"
        )
        return (
            plan_exists(level)
            and basis is not None
            and basis.source != "reference"
            and not trusted
            and not _cote_known(level)
        )
    if field == "hauteur_plafond":
        return level.kind != "attic" and level.height_source not in KNOWN_SOURCES
    room = main_room(level)
    return room is not None and room.room_type is None


def missing(project: Project, level_id: UUID) -> list[Question]:
    """Les questions de calage qui restent, dans l'ordre où l'app les pose."""
    level = project.levels[level_id]
    return [question_for(project, level_id, f) for f in FIELDS if _is_missing(level, f)]


def summary(project: Project, level_id: UUID, previous: Any = None) -> dict[str, Any]:
    """Le résumé du projet envoyé au modèle (jamais l'historique, ni une photo)."""
    level = project.levels[level_id]
    room = main_room(level)
    answer = {v: k for k, v in ROOM_TYPE_BY_ANSWER.items()}
    out: dict[str, Any] = {
        "projet": project.name,
        "type_piece": answer.get(room.room_type or "") if room is not None else None,
        "plan_existe": plan_exists(level),
        "nb_photos": len(project.images),
        "champs_remplis": {},
    }
    if previous is not None:
        out["derniere_reponse"] = previous
    return out
