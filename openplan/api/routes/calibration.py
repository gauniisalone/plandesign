"""Calibration métrique d'un niveau à partir des mesures du projet."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.api.routes.scene import EditResult, _result
from openplan.pipeline.calibration import calibrate_level
from openplan.pipeline.recalibrate import (
    DimensionsBody,
    DimensionsSummary,
    RecalibrateBody,
    RecalibrateSummary,
    preview_dimensions,
    recalibrate_dimensions,
    recalibrate_level,
)
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import Calibration, Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["calibration"])


class CalibrationResult(StrictModel):
    calibration: Calibration
    project: Project
    applied: bool


@router.post("/projects/{project_id}/levels/{level_id}/calibrate")
def calibrate(
    level_id: UUID, project: ProjectDep, store: StoreDep, dry_run: bool = False
) -> CalibrationResult:
    """dry_run=true renvoie le facteur et les résidus sans toucher au projet."""
    after, calibration = calibrate_level(project, level_id, dry_run=dry_run)
    if not dry_run:
        store.save(after)
    return CalibrationResult(calibration=calibration, project=after, applied=not dry_run)


class RecalibrateResult(EditResult):
    summary: RecalibrateSummary


class DimensionsResult(RecalibrateResult):
    dimensions: DimensionsSummary


@router.post("/projects/{project_id}/levels/{level_id}/recalibrate/dimensions/preview")
def recalibrate_dimensions_preview(
    level_id: UUID, body: DimensionsBody, project: ProjectDep
) -> DimensionsSummary:
    """R3 : dimensions actuelles de la pièce et ce que donneraient celles saisies (« 9,65 ×
    5,00 m → 8,00 × 4,20 m »), sans rien modifier."""
    return preview_dimensions(project, level_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/recalibrate/dimensions")
def recalibrate_dimensions_route(
    level_id: UUID,
    body: DimensionsBody,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
) -> DimensionsResult:
    """R3 : longueur, largeur, hauteur de la pièce (facultatives) ; une valeur, échelle
    uniforme ; longueur et largeur, échelle selon les axes de la pièce ; annulable."""
    after, summary, dims = recalibrate_dimensions(project, level_id, body, actor=actor)
    report = validate_invariants(after)
    store.save(after)
    base = _result(after, level_id, report)
    return DimensionsResult(**base.model_dump(), summary=summary, dimensions=dims)


@router.post("/projects/{project_id}/levels/{level_id}/recalibrate")
def recalibrate(
    level_id: UUID,
    body: RecalibrateBody,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
) -> RecalibrateResult:
    """« Recalibrer » : une longueur connue (deux points du plan, un mur ou la hauteur sous
    plafond) met tout le niveau à l'échelle, fond de plan compris ; geste utilisateur,
    annulable. 409 si la référence est trop courte ou le facteur invraisemblable."""
    after, summary = recalibrate_level(project, level_id, body, actor=actor)
    report = validate_invariants(after)
    store.save(after)
    base = _result(after, level_id, report)
    return RecalibrateResult(**base.model_dump(), summary=summary)
