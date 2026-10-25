"""Édition de la scène : commandes, annuler/rétablir, dérivés, validation."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.pipeline import journal
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.editing import InvalidEditError, edit
from openplan.pipeline.journal import JournalEvent
from openplan.scene import history
from openplan.scene.ops import (
    Op,
    OpError,
    RemoveElement,
    ReplaceFurniture,
    SetIntent,
    UpdateElement,
)
from openplan.scene.plans import NewPlan, PlanError, PlanRefusedError, add_plan, remove_plan
from openplan.schemas.common import StrictModel
from openplan.schemas.derived import DerivedLevel
from openplan.schemas.elements import Furniture
from openplan.schemas.intent import META_FIELDS
from openplan.schemas.results import ValidationReport
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["scene"])


class EditResult(StrictModel):
    project: Project
    report: ValidationReport
    derived: DerivedLevel


def _result(project: Project, level_id: UUID, report: ValidationReport) -> EditResult:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    return EditResult(project=project, report=report, derived=compute_derived(level))


@router.post("/projects/{project_id}/levels/{level_id}/ops")
def apply_operation(
    level_id: UUID,
    op: Annotated[Op, Body()],
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
) -> EditResult:
    """Applique une commande d'édition en tant qu'utilisateur (nommé par son jeton quand l'API
    est exposée), valide, persiste. Sous le verrou du projet : deux clics rapprochés font
    deux gestes, jamais un geste écrasé par l'autre."""
    with store.lock(project.id):
        fresh = store.load(project.id)
        after, report = edit(fresh, level_id, op, actor=actor)
        store.save(after)
    _journal_gesture(store.project_dir(project.id), fresh, after, level_id, op)
    return _result(after, level_id, report)


def _journal_gesture(
    project_dir: Path, before: Project, after: Project, level_id: UUID, op: Op
) -> None:
    """Journal V2 : ce que l'utilisateur fait d'une demande (ambiance corrigée, meuble déplacé
    ou retiré), rattaché à la dernière demande qui avait produit cet élément. Identifiants et
    noms de champs seulement, jamais de coordonnée."""
    kind: str | None = None
    element_id: UUID | None = None
    fields: list[str] = []
    level = before.levels.get(level_id)
    if isinstance(op, SetIntent):
        kind, element_id = "intent_corrected", op.room_id
        fields = sorted(k for k in op.fields if k not in META_FIELDS)
    elif level is not None and isinstance(op, UpdateElement | RemoveElement | ReplaceFurniture):
        element = level.elements.get(op.element_id)
        if isinstance(element, Furniture):
            element_id = op.element_id
            if isinstance(op, RemoveElement):
                kind = "furniture_removed"
            elif isinstance(op, ReplaceFurniture):
                kind = "furniture_replaced"
            elif {"transform", "position", "yaw"} & op.fields.keys():
                kind = "furniture_moved"
    if kind is None or element_id is None:
        return
    origin = next(
        (
            e
            for e in reversed(journal.read(project_dir))
            if element_id in e.element_ids and e.kind not in ("undo", kind)
        ),
        None,
    )
    if origin is None and kind != "intent_corrected":
        return  # un meuble posé à la main, sans demande derrière : rien à apprendre
    journal.record(
        project_dir,
        after,
        JournalEvent(
            project_id=after.id,
            kind=kind,  # type: ignore[arg-type]
            fields=fields,
            element_ids=[element_id],
            history_ids=journal.history_ids(before, after),
            request_id=origin.id if origin else None,
        ),
    )


class NewPlanResult(EditResult):
    level_id: UUID
    """Le plan créé (T7e) : l'atelier l'ouvre."""


@router.post("/projects/{project_id}/levels")
def new_plan(body: NewPlan, project: ProjectDep, store: StoreDep, actor: ActorDep) -> NewPlanResult:
    """T7e, « + Nouveau plan » : variante de ce plan (copie), nouvel étage (vide, au-dessus)
    ou plan vide (variante vide) ; un seul pas d'historique, annulable."""
    with store.lock(project.id):
        fresh = store.load(project.id)
        try:
            after, level_id = add_plan(fresh, body, actor=actor)
        except PlanError as exc:
            raise OpError(str(exc)) from exc
        report = validate_invariants(after)
        if not report.valid:
            raise InvalidEditError(report)
        store.save(after)
    edit = _result(after, level_id, report)
    return NewPlanResult(**edit.model_dump(), level_id=level_id)


@router.delete("/projects/{project_id}/levels/{level_id}")
def delete_plan(level_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep) -> Project:
    """KAN-91 (retours MEP 14), « Supprimer le plan » : un seul pas d'historique, annulable.
    L'espace de travail suit à sa lecture : la carte Rendus et la carte Photos du plan s'en
    vont avec leurs liens, ses photos restent, hors carte. 409 pour le seul plan, le premier,
    ou un étage qui a des variantes."""
    with store.lock(project.id):
        fresh = store.load(project.id)
        try:
            after = remove_plan(fresh, level_id, actor=actor)
        except PlanRefusedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PlanError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        report = validate_invariants(after)
        if not report.valid:
            raise InvalidEditError(report)
        store.save(after)
    return after


@router.post("/projects/{project_id}/undo")
def undo(project: ProjectDep, store: StoreDep) -> Project:
    with store.lock(project.id):
        before = store.load(project.id)
        after = history.undo(before)
        store.save(after)
    journal.record_undo(store.project_dir(project.id), before, after)
    return after


@router.post("/projects/{project_id}/redo")
def redo(project: ProjectDep, store: StoreDep) -> Project:
    with store.lock(project.id):
        after = history.redo(store.load(project.id))
        store.save(after)
    return after


@router.get("/projects/{project_id}/levels/{level_id}/derived")
def derived(level_id: UUID, project: ProjectDep) -> DerivedLevel:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    return compute_derived(level)


@router.get("/projects/{project_id}/validation")
def validation(project: ProjectDep) -> ValidationReport:
    return validate_invariants(project)
