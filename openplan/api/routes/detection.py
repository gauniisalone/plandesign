"""Détection heuristique depuis une photo et proposition de plan (niveau vide)."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import Field

from openplan.api.deps import DepthEstimatorDep, OpeningDetectorDep, ProjectDep, StoreDep
from openplan.api.files import served_file
from openplan.api.routes.scene import EditResult, _result
from openplan.pipeline.depthplan import DepthPlanRun, run_depthplan
from openplan.pipeline.detection import DetectionRun, detect_and_propose
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M, DetectionError
from openplan.pipeline.multiview import adopt_view, rotate_view
from openplan.pipeline.openings import OpeningsRun, run_openings
from openplan.schemas.common import StrictModel
from openplan.schemas.results import ValidationReport
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["detection"])
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class DetectBody(StrictModel):
    image_id: UUID
    camera_height_m: float = Field(default=DEFAULT_CAMERA_HEIGHT_M, gt=0.3, lt=3.0)


class DetectResult(StrictModel):
    run: DetectionRun
    report: ValidationReport


@router.post("/projects/{project_id}/levels/{level_id}/detect")
def detect(level_id: UUID, body: DetectBody, project: ProjectDep, store: StoreDep) -> DetectResult:
    """Analyse la photo. Niveau vide : propose un rectangle `estimated`. Niveau déjà muni de
    murs : confronte la photo à la scène (caméra « Photo n », murs inférés confirmés,
    violation `inconsistent_views` en cas d'écart ; voir `pipeline.multiview`)."""
    run = detect_and_propose(
        project,
        store.project_dir(project.id),
        level_id,
        body.image_id,
        store.runs_dir(project.id),
        camera_height_m=body.camera_height_m,
    )
    report = validate_invariants(run.project)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(run.project)
    return DetectResult(run=run, report=report)


class DetectDepthBody(StrictModel):
    image_id: UUID
    camera_height_m: float | None = Field(default=None, gt=0.3, lt=3.0)
    """Hauteur de prise de vue saisie : recale l'échelle du modèle ; None = échelle du modèle."""


class DepthPlanResult(StrictModel):
    run: DepthPlanRun
    report: ValidationReport


@router.post("/projects/{project_id}/levels/{level_id}/detect-depth")
def detect_from_depth(
    level_id: UUID,
    body: DetectDepthBody,
    project: ProjectDep,
    store: StoreDep,
    estimator: DepthEstimatorDep,
) -> DepthPlanResult:
    """Plan depuis la profondeur (2.3) : sol, murs verticaux, polygone avec renfoncements,
    appliqué au niveau vide. 409 si le niveau a déjà des murs ou si les poids manquent."""
    run = run_depthplan(
        project,
        store.project_dir(project.id),
        level_id,
        body.image_id,
        store.runs_dir(project.id),
        estimator=estimator,
        camera_height_m=body.camera_height_m,
    )
    report = validate_invariants(run.project)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(run.project)
    return DepthPlanResult(run=run, report=report)


class OpeningsResult(StrictModel):
    run: OpeningsRun
    report: ValidationReport


@router.post("/projects/{project_id}/levels/{level_id}/images/{image_id}/openings")
def detect_openings(
    level_id: UUID,
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    detector: OpeningDetectorDep,
) -> OpeningsResult:
    """Portes et fenêtres vues sur la photo (2.4b), projetées par la caméra de la photo sur le
    mur hôte → ouvertures `observed`, corrigeables ; le rapport dit ce qui a été écarté et
    pourquoi. 409 sans caméra pour cette photo ou sans poids."""
    run = run_openings(
        project,
        store.project_dir(project.id),
        level_id,
        image_id,
        store.runs_dir(project.id),
        detector=detector,
    )
    report = validate_invariants(run.project)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(run.project)
    return OpeningsResult(run=run, report=report)


@router.get("/projects/{project_id}/runs/{run_id}/openings/{name}")
def openings_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> Response:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "openings" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return served_file(store, path)


@router.post("/projects/{project_id}/levels/{level_id}/views/{camera_id}/adopt")
def adopt_view_route(
    level_id: UUID, camera_id: UUID, project: ProjectDep, store: StoreDep
) -> EditResult:
    """« Garder cette photo » : le plan suit ce que la photo a vu, le mur le moins sûr est
    déplacé (`pipeline.multiview.adopt_view`). Écarter une photo, à l'inverse, se fait par
    `update_element` sur sa caméra (`metadata.excluded`) : elle reste dans le projet."""
    after, _ = adopt_view(project, level_id, camera_id)
    report = validate_invariants(after)
    store.save(after)
    return _result(after, level_id, report)


class RotateBody(StrictModel):
    quarter_turns: int = Field(default=1, ge=1, le=3)


@router.post("/projects/{project_id}/levels/{level_id}/views/{camera_id}/rotate")
def rotate_view_route(
    level_id: UUID,
    camera_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    body: RotateBody | None = None,
) -> EditResult:
    """« Tourner la vue » : l'orientation d'une photo n'est connue qu'à un quart de tour
    près ; l'utilisateur la corrige, la caméra est replacée et sa visée tourne, aucun mur ne
    bouge, la cohérence est recontrôlée (`pipeline.multiview.rotate_view`)."""
    after, _ = rotate_view(project, level_id, camera_id, (body or RotateBody()).quarter_turns)
    report = validate_invariants(after)
    store.save(after)
    return _result(after, level_id, report)


@router.get("/projects/{project_id}/runs/{run_id}/detection/{name}")
def detection_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> Response:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "detection" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return served_file(store, path)
