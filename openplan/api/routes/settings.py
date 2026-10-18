"""Réglages d'un projet : nom, unités d'affichage, confidentialité."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import Field

from openplan.api.deps import ProjectDep, StoreDep
from openplan.api.guards import Role, require_role
from openplan.pipeline import journal
from openplan.pipeline.guided import has_answers
from openplan.pipeline.journal import JournalEvent, UiEvent
from openplan.pipeline.metrics import ProjectMetrics, project_metrics
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.schemas.common import StrictModel
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import PrivacySettings, Project, RenderSettings

router = APIRouter(tags=["projects"])


class ProjectSettingsUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    units_display: Literal["m", "cm", "mm"] | None = None
    privacy: PrivacySettings | None = None
    render: RenderSettings | None = None
    """Chemin automatique et finition enchaînée (V1, point 6)."""
    pending_instruction: str | None = Field(default=None, max_length=300)
    """Consigne gardée (L1b) ; une chaîne vide l'efface."""
    pending_answers: GuidedAnswers | None = None
    """Réponses guidées de la note Exigences (T1) ; un objet sans réponse les efface."""


@router.patch("/projects/{project_id}")
def update_settings(
    body: ProjectSettingsUpdate, project: ProjectDep, store: StoreDep, request: Request
) -> Project:
    if body.privacy is not None and body.privacy != project.privacy:
        # Confidentialité (accord pour un modèle distant, journal) : propriétaire seulement.
        require_role(request, project.id, Role.OWNER)
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if "privacy" in update:
        update["privacy"] = body.privacy
    if "render" in update:
        update["render"] = body.render
    if "pending_instruction" in update:
        update["pending_instruction"] = update["pending_instruction"].strip() or None
    if "pending_answers" in update:
        update["pending_answers"] = (
            body.pending_answers if has_answers(body.pending_answers) else None
        )
    if "name" in update:
        # Renommé à la main : le nom automatique ne le remplacera plus (L1a).
        update["name_auto"] = False
    after = project.model_copy(update=update)
    after = history.commit(project, after, actor=USER_ACTOR, label="Modifier les réglages")
    store.save(after)
    return after


class JournalSummary(StrictModel):
    """Journal local des demandes (V2) : actif ou coupé, nombre de lignes, où il est."""

    enabled: bool
    events: int
    location: str = "dans le dossier du projet (journal/design.jsonl), jamais envoyé"


@router.get("/projects/{project_id}/journal")
def journal_summary(project: ProjectDep, store: StoreDep) -> JournalSummary:
    return JournalSummary(
        enabled=project.privacy.design_journal,
        events=len(journal.read(store.project_dir(project.id))),
    )


@router.delete("/projects/{project_id}/journal")
def journal_clear(project: ProjectDep, store: StoreDep) -> JournalSummary:
    """Vide le journal du projet (sur demande ; couper le réglage n'efface rien)."""
    journal.clear(store.project_dir(project.id))
    return JournalSummary(enabled=project.privacy.design_journal, events=0)


class UiEventBody(StrictModel):
    event: UiEvent
    detail: str | None = Field(default=None, pattern=r"^[a-z0-9_:>-]{1,40}$")
    """Mot d'un vocabulaire fermé (« edit>render », « vue:2d>3d », « all ») : rien de libre."""


class UiEventRecorded(StrictModel):
    recorded: bool
    """Faux quand le journal du projet est coupé : rien n'est écrit."""


@router.post("/projects/{project_id}/journal/ui")
def journal_ui(body: UiEventBody, project: ProjectDep, store: StoreDep) -> UiEventRecorded:
    """Mesure du parcours (P0) : un geste d'interface au journal local, jamais envoyé."""
    recorded = journal.record(
        store.project_dir(project.id),
        project,
        JournalEvent(project_id=project.id, kind=body.event, note=body.detail),
    )
    return UiEventRecorded(recorded=recorded)


@router.get("/projects/{project_id}/metrics")
def metrics(project: ProjectDep, store: StoreDep) -> ProjectMetrics:
    """Indicateurs du parcours (P0), calculés sur la machine."""
    return project_metrics(project, store.project_dir(project.id), store.runs_dir(project.id))
