"""KAN-124 (MH3, MH4, MH5) : de l'extraction validée à ce que l'app propose.

Le serveur revalide tout, quoi que rende le modèle : le champ doit être celui demandé, la
valeur dans les bornes, l'action possible dans l'état du projet (photo, plan). Une action est
TOUJOURS un cas d'usage déclaré dans le registre (KAN-105), décrit ici (méthode, chemin, corps)
et lancé par le front après le clic sur le bouton de confirmation, que l'app génère elle-même ;
la route existante revalide encore et fait son unique pas d'annulation. Aucune action ne part
d'ici, et le texte ne promet que ce que l'app sait faire.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import Field, JsonValue

from openplan.pipeline.calage.missing import (
    COTE_BOUNDS,
    HEIGHT_BOUNDS,
    ROOM_TYPE_BY_ANSWER,
    ROOM_TYPE_LABELS,
    Question,
    QuestionField,
    plan_exists,
)
from openplan.schemas.common import StrictModel
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.scene import Project

API = "/api"
Kind = Literal["action", "valeur", "supposition", "precision", "rien"]
ButtonKind = Literal["confirm", "correct", "skip"]


class Action(StrictModel):
    """Un cas d'usage du registre, prêt à être appelé par le front après le clic."""

    use_case: str
    method: Literal["POST"]
    path: str
    body: dict[str, JsonValue] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)


class Button(StrictModel):
    label: str
    kind: ButtonKind
    action: Action | None = None
    """Seul le bouton « confirm » d'une proposition d'action en porte une."""


class Proposal(StrictModel):
    kind: Kind
    field: QuestionField | None = None
    text: str
    value: JsonValue = None
    """La valeur retenue par l'app (mètres, ou type de pièce de `ROOM_TYPES`)."""
    estimated: bool = False
    """Vrai quand la réponse elle-même est approximative (« environ 2,50 m »)."""
    buttons: list[Button] = Field(default_factory=list)


NOT_UNDERSTOOD = "Je n'ai pas compris. Répondez à la question, ou passez-la."
CORRECT = Button(label="Corriger", kind="correct")
SKIP = Button(label="Passer", kind="skip")


def _level_path(project: Project, level_id: UUID) -> str:
    return f"{API}/projects/{project.id}/levels/{level_id}"


def skipped(question: Question) -> Proposal:
    """La question passée : l'app dit ce qu'elle suppose (MH2), rien n'est modifié."""
    return Proposal(
        kind="supposition",
        field=question.field,
        text=question.assumption,
        buttons=[Button(label="D'accord", kind="confirm")],
    )


def _bounds(field: QuestionField) -> tuple[float, float] | None:
    return {"hauteur_plafond": HEIGHT_BOUNDS, "cote_connue": COTE_BOUNDS}.get(field)


def _precision(field: QuestionField, value: Any) -> Proposal:
    bounds = _bounds(field)
    if bounds is not None:
        read = f"{metres(float(value))}, vraiment ? " if isinstance(value, int | float) else ""
        what = "La hauteur" if field == "hauteur_plafond" else "La largeur de la pièce"
        text = (
            f"{read}{what} se donne en mètres, entre {decimal(bounds[0])} et {metres(bounds[1])}."
        )
    else:
        text = "Précisez votre réponse, ou passez la question."
    return Proposal(kind="precision", field=field, text=text, buttons=[SKIP])


def _dimension(
    project: Project, level_id: UUID, field: QuestionField, e: dict[str, Any]
) -> Proposal:
    value = e.get("valeur")
    bounds = _bounds(field)
    if bounds is None or not isinstance(value, int | float) or isinstance(value, bool):
        return Proposal(kind="rien", field=field, text=NOT_UNDERSTOOD)
    if not bounds[0] <= float(value) <= bounds[1]:
        return _precision(field, value)  # revalidé ici, même si le schéma l'a déjà refusé
    if not plan_exists(project.levels[level_id]):
        return Proposal(
            kind="rien",
            field=field,
            text="Il n'y a pas encore de plan à régler : générez-le d'abord.",
        )
    v = round(float(value), 3)
    if field == "hauteur_plafond":
        key, text = "height_m", f"Régler la hauteur sous plafond à {metres(v)} ?"
    else:
        element = str(e.get("element") or "").lower()
        if element and "largeur" not in element and "longueur" not in element:
            return Proposal(
                kind="precision",
                field=field,
                text=(
                    "Pour caler le plan, il faut la largeur ou la longueur de la pièce, d'un mur "
                    "à l'autre : la cote d'une porte ou d'un meuble ne suffit pas encore."
                ),
                buttons=[SKIP],
            )
        key = "length_m" if "longueur" in element else "width_m"
        side = "la longueur" if key == "length_m" else "la largeur"
        text = f"Caler le plan sur {side} de la pièce : {metres(v)} ?"
    action = Action(
        use_case="plan.recalibrate.dimensions:post",
        method="POST",
        path=f"{_level_path(project, level_id)}/recalibrate/dimensions",
        body={key: v},
    )
    return Proposal(
        kind="action",
        field=field,
        text=text,
        value=v,
        estimated=e.get("certitude") == "approx",
        buttons=[Button(label="Oui", kind="confirm", action=action), CORRECT, SKIP],
    )


def _plan_photo(project: Project, level_id: UUID) -> UUID | None:
    basis = project.levels[level_id].scale_basis
    if basis is not None and basis.image_id in project.images:
        return basis.image_id
    return next(iter(project.images), None)


def _generate(project: Project, level_id: UUID) -> Proposal:
    """generate_plan ou regenerate_plan : c'est l'état du projet qui tranche, pas le modèle."""
    image_id = _plan_photo(project, level_id)
    if image_id is None:
        return Proposal(kind="rien", text="Ajoutez d'abord une photo de la pièce.")
    exists = plan_exists(project.levels[level_id])
    action = Action(
        use_case="plan.images.autoplan:post",
        method="POST",
        path=f"{_level_path(project, level_id)}/images/{image_id}/autoplan",
        query={"replace": "true"} if exists else {},
    )
    text = (
        "Régénérer le plan depuis les photos ? L'ancien plan reste annulable."
        if exists
        else "Générer le plan depuis la photo ?"
    )
    label = "Régénérer" if exists else "Générer"
    return Proposal(
        kind="action", text=text, buttons=[Button(label=label, kind="confirm", action=action)]
    )


def _renders(project: Project, level_id: UUID) -> Proposal:
    if not plan_exists(project.levels[level_id]):
        return Proposal(kind="rien", text="Il faut d'abord un plan pour lancer les rendus.")
    action = Action(
        use_case="plan.render.all:post",
        method="POST",
        path=f"{_level_path(project, level_id)}/render/all",
    )
    return Proposal(
        kind="action",
        text="Lancer les rendus des vues du plan ?",
        buttons=[Button(label="Lancer les rendus", kind="confirm", action=action)],
    )


def _room_type(field: QuestionField, e: dict[str, Any]) -> Proposal:
    value = e.get("valeur")
    if field != "type_piece" or e.get("champ") != "type_piece" or value not in ROOM_TYPE_BY_ANSWER:
        return Proposal(kind="rien", field=field, text=NOT_UNDERSTOOD)
    return Proposal(
        kind="valeur",
        field=field,
        text=f"Pièce : {ROOM_TYPE_LABELS[str(value)]}.",
        value=ROOM_TYPE_BY_ANSWER[str(value)],
        buttons=[Button(label="Oui", kind="confirm"), CORRECT],
    )


def propose(  # noqa: PLR0911 - une branche par outil de la liste fermée
    project: Project, level_id: UUID, question: Question, e: dict[str, Any]
) -> Proposal:
    """Ce que l'app propose pour l'extraction `e` (déjà conforme au schéma) à `question`."""
    field = question.field
    tool = e.get("outil")
    if tool == "skip":
        return skipped(question)
    if e.get("besoin_clarification") is True:
        return _precision(field, e.get("valeur"))
    if tool == "set_dimension":
        if e.get("champ") != field:
            return Proposal(kind="rien", field=field, text=NOT_UNDERSTOOD)
        return _dimension(project, level_id, field, e)
    if tool in ("generate_plan", "regenerate_plan"):
        return _generate(project, level_id)
    if tool == "launch_renders":
        return _renders(project, level_id)
    if e.get("champ") == "type_piece":
        return _room_type(field, e)
    return Proposal(kind="rien", field=field, text=NOT_UNDERSTOOD)
