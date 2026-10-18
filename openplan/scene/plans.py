"""Plusieurs plans par projet (T7e) : « + Nouveau plan ».

Un plan est un niveau (`Level`). Trois sortes, les mots d'augus :

- « Variante de ce plan » : copie du plan courant (murs, pièces, meubles, vues), autre version
  du même lieu ; jamais empilée en 3D, vue seule ;
- « Nouvel étage » : plan vide posé au-dessus du plus haut étage, empilé en 3D ;
- « Plan vide » : variante vide du plan courant (même lieu, même altitude), à tracer.

Une variante pointe toujours vers un étage (`variant_of`), jamais vers une autre variante.
Créer un plan est un seul pas d'historique : Ctrl+Z le retire.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import Level, Project

PlanKind = Literal["variant", "storey", "empty"]
PLAN_KIND_LABELS: dict[str, str] = {
    "variant": "Variante de ce plan",
    "storey": "Nouvel étage",
    "empty": "Plan vide",
}
"""Libellés du choix de « + Nouveau plan », tels qu'augus les a donnés."""

STOREY_HEIGHT_M = 2.50


class PlanError(ValueError):
    """Plan d'origine inconnu."""


class NewPlan(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    kind: PlanKind
    source_level_id: UUID | None = None
    """Le plan courant (celui d'où l'on demande) ; absent = le premier plan du projet."""


def storey_of(project: Project, level: Level) -> Level:
    """L'étage d'un plan : lui-même, ou l'étage dont il est la variante."""
    if level.variant_of is not None:
        return project.levels.get(level.variant_of, level)
    return level


def stack_of(project: Project, level: Level) -> list[Level]:
    """Ce qui s'affiche avec ce plan en 3D : les étages du projet, ce plan prenant la place de
    son étage s'il en est une variante ; les autres variantes jamais (T7e)."""
    storey = storey_of(project, level)
    return [
        level if other.id == storey.id else other
        for other in project.levels.values()
        if other.variant_of is None
    ]


def _copy_level(level: Level) -> Level:
    """Une copie dont tous les identifiants (niveau, sommets, éléments) sont neufs : les rendus
    de la variante ne se confondent jamais avec ceux de l'original (ils suivent les caméras)."""
    text = level.model_dump_json()
    for old in [*level.vertices, *level.elements]:
        text = text.replace(str(old), str(uuid4()))
    data = json.loads(text)
    data["id"] = str(uuid4())
    return Level.model_validate(data)


def add_plan(project: Project, body: NewPlan, *, actor: str = USER_ACTOR) -> tuple[Project, UUID]:
    """Ajoute un plan, un seul pas d'historique ; renvoie le projet et l'id du nouveau plan."""
    if body.source_level_id is not None:
        source = project.levels.get(body.source_level_id)
        if source is None:
            msg = f"plan inconnu : {body.source_level_id}"
            raise PlanError(msg)
    else:
        source = next(iter(project.levels.values()), None)
    name = body.name.strip() or PLAN_KIND_LABELS[body.kind]
    if source is None or body.kind == "storey":
        storeys = [lv for lv in project.levels.values() if lv.variant_of is None]
        top = max((lv.elevation + lv.height for lv in storeys), default=0.0)
        level = Level(
            name=name,
            kind="upper" if storeys else None,
            elevation=round(top, 3),
            height=STOREY_HEIGHT_M,
            north_deg=source.north_deg if source is not None else 90.0,
        )
    else:
        storey = storey_of(project, source)
        if body.kind == "variant":
            level = _copy_level(source).model_copy(update={"name": name, "variant_of": storey.id})
        else:
            level = Level(
                name=name,
                kind=source.kind,
                elevation=source.elevation,
                height=source.height,
                height_source=source.height_source,
                north_deg=source.north_deg,
                variant_of=storey.id,
            )
    after = project.model_copy(update={"levels": {**project.levels, level.id: level}})
    label = f"Nouveau plan « {name} »"
    return history.commit(project, after, actor=actor, label=label), level.id
