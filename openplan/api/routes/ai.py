"""Assistant IA : état du modèle configuré, perception d'une photo, journal réseau."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from openplan.adapters.gateway import NETWORK_LOG, ModelGateway, NetworkLogEntry, read_network_log
from openplan.api.deps import LanguageModelDep, ProjectDep, StoreDep
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.intention import (
    IntentionBody,
    IntentionPreview,
    IntentionRun,
    preview_intention,
    run_intention,
)
from openplan.pipeline.perception import PerceptionRun, run_vlm_perception
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["ai"])
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class AiStatus(StrictModel):
    configured: bool
    model_id: str | None = None
    destination: str | None = None
    remote: bool | None = None
    supports_images: bool | None = None


@router.get("/ai")
def ai_status(model: LanguageModelDep) -> AiStatus:
    if model is None:
        return AiStatus(configured=False)
    return AiStatus(
        configured=True,
        model_id=model.model_id,
        destination=model.destination,
        remote=model.remote,
        supports_images=model.supports_images,
    )


@router.post("/projects/{project_id}/images/{image_id}/perceive")
def perceive(
    image_id: UUID, project: ProjectDep, store: StoreDep, model: LanguageModelDep
) -> PerceptionRun:
    """Envoie la photo au modèle configuré si le projet y consent ; ne modifie pas la scène."""
    if model is None:
        raise HTTPException(
            status_code=409,
            detail="aucun modèle IA configuré (OPENPLAN_AI_BASE_URL et OPENPLAN_AI_MODEL)",
        )
    gateway = ModelGateway(store.project_dir(project.id) / NETWORK_LOG)
    return run_vlm_perception(
        project,
        store.project_dir(project.id),
        image_id,
        store.runs_dir(project.id),
        model=model,
        gateway=gateway,
    )


@router.post("/projects/{project_id}/levels/{level_id}/brief/preview")
def brief_preview(
    level_id: UUID, body: IntentionBody, project: ProjectDep, model: LanguageModelDep
) -> IntentionPreview:
    """Ce qui partirait au modèle (texte + description de la pièce, jamais la photo) et vers
    où (local / distant), sans rien envoyer : à afficher avant le bouton d'envoi."""
    return preview_intention(project, level_id, body, model)


@router.post("/projects/{project_id}/levels/{level_id}/brief")
def brief(
    level_id: UUID,
    body: IntentionBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
) -> IntentionRun:
    """Rôle Intention (3.3) : souhait → brief structuré (meubles du catalogue, priorités,
    préférences ; aucune position). Consentement exigé pour un modèle distant ; ne modifie
    pas la scène."""
    if model is None:
        raise HTTPException(
            status_code=409,
            detail="aucun modèle IA configuré (OPENPLAN_AI_BASE_URL et OPENPLAN_AI_MODEL)",
        )
    gateway = ModelGateway(store.project_dir(project.id) / NETWORK_LOG)
    return run_intention(
        project, level_id, body, store.runs_dir(project.id), model=model, gateway=gateway
    )


@router.get("/projects/{project_id}/runs/{run_id}/intention/{name}")
def intention_artifact(
    project: ProjectDep, run_id: UUID, name: str, store: StoreDep
) -> FileResponse:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "intention" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return FileResponse(path)


@router.get("/projects/{project_id}/network-log")
def network_log(project: ProjectDep, store: StoreDep) -> list[NetworkLogEntry]:
    return read_network_log(store.project_dir(project.id) / NETWORK_LOG)


@router.get("/projects/{project_id}/runs/{run_id}/perception/{name}")
def perception_artifact(
    project: ProjectDep, run_id: UUID, name: str, store: StoreDep
) -> FileResponse:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "perception" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return FileResponse(path)
