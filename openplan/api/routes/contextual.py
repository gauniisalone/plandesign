"""Rendu contextualisé (A1) : prompt enrichi depuis une source et une intention, génération
des variantes en tâche de fond, liste et images. Rien ici ne touche au plan ni à la 3D."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.registry import resolve_device
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    LanguageModelDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
    get_depth_estimator,
    get_image_finisher,
)
from openplan.api.jobs import JobStatus
from openplan.pipeline import contextual as c
from openplan.pipeline import home_settings
from openplan.pipeline.contextual import ContextualError
from openplan.pipeline.say import readers_for
from openplan.pipeline.timings import TIMINGS_FILE, Timings
from openplan.scene.catalog import load_catalog

router = APIRouter(tags=["contextual"])


def _level(project_levels: Mapping[UUID, object], level_id: UUID) -> None:
    if level_id not in project_levels:
        raise HTTPException(status_code=404, detail=f"niveau inconnu : {level_id}")


@router.post("/projects/{project_id}/levels/{level_id}/contextual/prompt")
def contextual_prompt(
    level_id: UUID,
    body: c.ContextualPromptBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
) -> c.ContextualPrompt:
    """Le prompt enrichi et ce qui l'a nourri, à montrer et à modifier avant de générer. Le
    modèle distant ne lit l'intention que si la case Gemini de l'accueil est cochée (texte
    seul) ; sinon les phrases simples, sur la machine."""
    _level(project.levels, level_id)
    readers = readers_for(
        model,
        home_settings.load(store.root).gemini_text,
        ModelGateway(store.project_dir(project.id) / "network_log.jsonl"),
    )
    try:
        return c.build_prompt(
            project,
            level_id,
            body,
            categories=load_catalog().categories,
            model=readers.model,
            gateway=readers.gateway,
        )
    except ContextualError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/projects/{project_id}/levels/{level_id}/contextual", status_code=202)
def contextual_generate(
    level_id: UUID,
    body: c.ContextualBody,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """Génère les variantes demandées (tâche de fond, une étape par variante). 409 si la
    source n'existe pas, si la vue n'a pas de rendu, ou si les poids manquent (avec la
    commande à lancer). Le projet n'est pas modifié."""
    _level(project.levels, level_id)
    runs_dir = store.runs_dir(project.id)
    project_dir = store.project_dir(project.id)
    if (
        body.source.kind == "render"
        and not (runs_dir / str(body.source.id) / "blender" / "preview.png").is_file()
    ):
        raise HTTPException(
            status_code=409,
            detail="aucun rendu pour cette vue : rendez-la d'abord (Résultats › Rendre)",
        )
    if body.source.kind == "photo" and body.source.id not in project.images:
        raise HTTPException(status_code=409, detail=f"photo inconnue : {body.source.id}")
    try:
        c.check_references(project, project_dir, runs_dir, body.references)
    except ContextualError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    inspirer = get_image_finisher(request)
    device, _ = resolve_device(settings.device)
    expected = Timings(store.root / TIMINGS_FILE).estimate(
        "inspiration", c.CONTEXTUAL_EXPECTED_S
    ) * len(body.variants)

    def work(stage: object) -> c.ContextualRun:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        return c.generate(
            store.load(project.id),
            level_id,
            project_dir,
            runs_dir,
            body,
            inspirer=inspirer,  # type: ignore[arg-type]
            estimator=(lambda: get_depth_estimator(request))
            if body.source.kind == "photo"
            else None,
            stage=stage,  # type: ignore[arg-type]
        )

    return jobs.submit_staged(
        "contextual",
        work,
        device=device,
        expected_s=expected,
        message=f"Rendu contextualisé, {len(body.variants)} variante(s), environ {expected:.0f} s",
        owner=actor,
        project_id=project.id,
    )


@router.get("/projects/{project_id}/contextual")
def contextual_list(project: ProjectDep, store: StoreDep) -> list[c.ContextualEntry]:
    """Toutes les variantes générées du projet, avec leurs métadonnées."""
    return c.list_entries(store.runs_dir(project.id))


@router.get("/projects/{project_id}/runs/{run_id}/contextual/{name}")
def contextual_image(run_id: UUID, name: str, project: ProjectDep, store: StoreDep) -> FileResponse:
    try:
        path = c.image_path(store.runs_dir(project.id), run_id, name)
    except ContextualError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="image/png")
