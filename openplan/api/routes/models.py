"""Registre des modèles (installés ou non) et profondeur métrique d'une photo."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import Field

from openplan.adapters.neural.weights import is_installed, license_accepted
from openplan.adapters.registry import ModelEntry, load_registry, resolve_device
from openplan.api.deps import (
    ActorDep,
    DepthEstimatorDep,
    ImageGeneratorDep,
    JobsDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
)
from openplan.api.jobs import JobStatus
from openplan.pipeline.attic import AtticRun, run_attic
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.depth import DepthRun, run_depth
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.pipeline.inspiration import InspirationOptions, InspirationRun, run_inspiration
from openplan.scene.store import ProjectStore
from openplan.schemas.common import StrictModel
from openplan.schemas.results import StepResult, ValidationReport
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["models"])
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _artifact(store: ProjectStore, project: Project, run_id: UUID, step: str, name: str) -> Path:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / step / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return path


class ModelStatus(StrictModel):
    entry: ModelEntry
    installed: bool | None = None
    """None quand il n'y a pas de poids à installer (algorithme, outil, service)."""
    license_accepted: bool | None = None
    """None si la licence n'exige pas d'acceptation explicite."""


@router.get("/models")
def list_models(settings: SettingsDep) -> list[ModelStatus]:
    out: list[ModelStatus] = []
    for e in load_registry():
        weights = e.kind == "weights"
        out.append(
            ModelStatus(
                entry=e,
                installed=is_installed(e.id, settings.models_dir, e.sentinel) if weights else None,
                license_accepted=(
                    license_accepted(e.id, settings.models_dir)
                    if weights and e.requires_license_acceptance
                    else None
                ),
            )
        )
    return out


class DeviceInfo(StrictModel):
    requested: str
    device: str
    """Périphérique effectif : cuda ou cpu."""
    label: str
    """Libellé lisible : nom du GPU, pourquoi cpu."""


@router.get("/device")
def device_info(settings: SettingsDep) -> DeviceInfo:
    """Périphérique de calcul des modèles locaux (auto : cuda dès qu'un GPU est vu)."""
    device, label = resolve_device(settings.device)
    return DeviceInfo(requested=settings.device, device=device, label=label)


def inspiration_expectation(device: str, steps: int) -> tuple[float, str]:
    """Durée attendue et phrase affichable, d'après les mesures du protocole (RTX PRO 500 :
    20 étapes en 10 s ; processeur : de l'ordre de 20 s par étape)."""
    if device == "cuda":
        expected = 3.0 + 0.5 * steps
        return expected, f"Génération sur GPU : environ {expected:.0f} s attendues."
    expected = 20.0 * steps
    return expected, (
        f"Génération sur processeur (aucun GPU utilisé) : plusieurs minutes, environ "
        f"{expected / 60:.0f} min pour {steps} étapes. La page reste utilisable pendant ce temps."
    )


@router.post("/projects/{project_id}/images/{image_id}/inspire", status_code=202)
def inspire(
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    estimator: DepthEstimatorDep,
    generator: ImageGeneratorDep,
    body: InspirationOptions | None = None,
) -> JobStatus:
    """Lance l'image d'inspiration locale (source generated) en tâche de fond et répond tout
    de suite avec son état ; suivre par `GET /projects/{id}/jobs/{job_id}`. La génération
    peut prendre plusieurs minutes sur processeur : la requête ne l'attend pas."""
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    options = body or InspirationOptions()
    device, _ = resolve_device(settings.device)
    expected, message = inspiration_expectation(device, options.steps)
    project_dir, runs_dir = store.project_dir(project.id), store.runs_dir(project.id)

    def work() -> InspirationRun:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        return run_inspiration(
            store.load(project.id),
            project_dir,
            image_id,
            runs_dir,
            estimator=estimator,
            generator=generator,
            options=options,
        )

    return jobs.submit(
        "inspiration",
        work,
        device=device,
        expected_s=expected,
        message=message,
        owner=actor,
        project_id=project.id,
    )


@router.get("/jobs/running")
def running_job(jobs: JobsDep, actor: ActorDep) -> dict[str, object]:
    """La tâche lourde en cours de cet utilisateur, s'il y en a une : sa sorte et depuis quand ;
    `queued` : le nombre de ses tâches en attente, seulement s'il y en a. Sert à isoler les e2e
    lourds (aucun test ne démarre tant que le précédent calcule ou attend)."""
    job = jobs.running_for(actor)
    body: dict[str, object] = (
        {"running": False}
        if job is None
        else {"running": True, "kind": job.kind, "elapsed_s": round(jobs.elapsed(job), 1)}
    )
    queued = jobs.queued_for(actor)
    if queued:
        body["queued"] = len(queued)
    return body


@router.get("/projects/{project_id}/jobs/{job_id}")
def job_status(job_id: UUID, project: ProjectDep, jobs: JobsDep) -> JobStatus:
    """État d'une tâche de fond lancée pour ce projet (queued avec sa place dans la file,
    running, done avec résultat, failed, cancelled)."""
    del project  # vérifie seulement que le projet existe
    return jobs.get(job_id)


@router.delete("/projects/{project_id}/jobs/{job_id}")
def cancel_job(job_id: UUID, project: ProjectDep, jobs: JobsDep, actor: ActorDep) -> JobStatus:
    """Annule une tâche en attente de cet utilisateur (elle ne démarrera pas) ; 409 si elle a
    déjà démarré, 404 si elle est inconnue ou d'un autre utilisateur."""
    del project  # vérifie seulement que le projet existe
    return jobs.cancel(job_id, actor)


@router.get("/projects/{project_id}/runs/{run_id}/inspiration/{name}")
def inspiration_artifact(
    project: ProjectDep, run_id: UUID, name: str, store: StoreDep
) -> FileResponse:
    return FileResponse(_artifact(store, project, run_id, "inspiration", name))


@router.post("/projects/{project_id}/images/{image_id}/depth")
def depth(
    image_id: UUID, project: ProjectDep, store: StoreDep, estimator: DepthEstimatorDep
) -> DepthRun:
    """Carte de profondeur métrique locale ; ne modifie pas la scène."""
    return run_depth(
        project,
        store.project_dir(project.id),
        image_id,
        store.runs_dir(project.id),
        estimator=estimator,
    )


@router.get("/projects/{project_id}/runs/{run_id}/depth/{name}")
def depth_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> FileResponse:
    return FileResponse(_artifact(store, project, run_id, "depth", name))


class AtticRequest(StrictModel):
    camera_height_m: float | None = Field(default=None, gt=0.3, lt=3.0)
    """Hauteur de prise de vue saisie : recale l'échelle du modèle de profondeur dessus."""


@router.post("/projects/{project_id}/images/{image_id}/attic")
def attic(
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    estimator: DepthEstimatorDep,
    body: AtticRequest | None = None,
) -> AtticRun:
    """Combles estimés (sol, pentes, pignon) depuis la profondeur ; ne modifie pas la scène."""
    return run_attic(
        project,
        store.project_dir(project.id),
        image_id,
        store.runs_dir(project.id),
        estimator=estimator,
        camera_height_m=body.camera_height_m if body else None,
    )


@router.get("/projects/{project_id}/runs/{run_id}/attic/{name}")
def attic_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> FileResponse:
    return FileResponse(_artifact(store, project, run_id, "attic", name))


class AtticApplyResult(StrictModel):
    project: Project
    report: ValidationReport
    result: StepResult


@router.post("/projects/{project_id}/levels/{level_id}/attic")
def attic_apply(
    level_id: UUID, body: AtticGeometry, project: ProjectDep, store: StoreDep
) -> AtticApplyResult:
    """Crée murs, pentes et caméra des combles validés sur un niveau vide (sources conservées)."""
    after, result = apply_attic(project, level_id, body)
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(after)
    return AtticApplyResult(project=after, report=report, result=result)
