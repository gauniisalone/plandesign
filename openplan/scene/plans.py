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
from openplan.schemas.scene import JsonPatch, Level, Measurement, Project

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


ONLY_PLAN = "C'est le seul plan du projet : il ne se supprime pas."
HAS_VARIANTS = "Cet étage a des variantes : supprimez d'abord ses variantes ({names})."


class PlanRefusedError(ValueError):
    """Suppression refusée, la raison dite à l'utilisateur (409)."""


def remove_plan(project: Project, level_id: UUID, *, actor: str = USER_ACTOR) -> Project:
    """KAN-91 (retours MEP 14), « Supprimer le plan » : un seul pas d'historique, Ctrl+Z le
    rend. Jamais le seul plan, jamais un étage dont des variantes dépendent (elles deviendraient
    orphelines). KAN-93 : le premier plan aussi ; le plan suivant devient le premier (l'ordre
    des niveaux est gardé, Ctrl+Z remet le plan à sa place)."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"plan inconnu : {level_id}"
        raise PlanError(msg)
    if len(project.levels) < 2:
        raise PlanRefusedError(ONLY_PLAN)
    variants = [lv.name for lv in project.levels.values() if lv.variant_of == level_id]
    if variants:
        raise PlanRefusedError(HAS_VARIANTS.format(names=", ".join(f"« {n} »" for n in variants)))
    levels = {lid: lv for lid, lv in project.levels.items() if lid != level_id}
    after = project.model_copy(
        update={"levels": levels, "measurements": _measurements_left(project, levels)}
    )
    done = history.commit(project, after, actor=actor, label=f"Supprimer le plan « {level.name} »")
    if next(reversed(project.levels)) == level_id:
        return done
    return _in_place(done, project, after)


def _measurements_left(project: Project, levels: dict[UUID, Level]) -> dict[UUID, Measurement]:
    """KAN-93 : les mesures d'un plan supprimé (sur ses murs ou ses sommets) partent avec lui,
    Ctrl+Z les rend ; une mesure sur une photo reste (les photos sont celles du projet)."""
    elements = {eid for lv in levels.values() for eid in lv.elements}
    vertices = {vid for lv in levels.values() for vid in lv.vertices}
    return {
        mid: m
        for mid, m in project.measurements.items()
        if (m.element_id is None or m.element_id in elements)
        and all(v in vertices for v in m.vertex_ids or [])
    }


def _in_place(done: Project, before: Project, after: Project) -> Project:
    """KAN-93 : un patch JSON rajoute une clé à la fin ; Ctrl+Z remettrait le plan supprimé
    après les autres, et le premier plan ne serait plus le premier. Le pas d'historique
    remplace donc les niveaux en entier, dans leur ordre, dans les deux sens."""
    entry = done.history[-1]

    def ordered(patch: JsonPatch, levels: Project) -> JsonPatch:
        kept = [op for op in patch if not str(op.get("path", "")).startswith("/levels/")]
        whole = history.content(levels)["levels"]
        return [*kept, {"op": "replace", "path": "/levels", "value": whole}]

    entry = entry.model_copy(
        update={
            "patch": ordered(entry.patch, after),
            "inverse_patch": ordered(entry.inverse_patch, before),
        }
    )
    return done.model_copy(update={"history": [*done.history[:-1], entry]})
