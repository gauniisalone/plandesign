"""Import d'un plan 2D existant (U1) : aperçu d'un DXF puis création des murs."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, Request, UploadFile

from openplan.api.access import usage_event
from openplan.api.deps import ProjectDep, StoreDep
from openplan.api.routes.scene import EditResult, _result
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.plan_import import (
    PlanApplyBody,
    PlanImportPreview,
    apply_segments,
    preview_dxf,
    preview_image,
    preview_pdf,
    preview_svg,
)
from openplan.schemas.common import StrictModel
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["plans"])
SUPPORTED = {".dxf", ".pdf", ".svg"}


@router.post("/projects/{project_id}/levels/{level_id}/import-plan/preview")
async def import_plan_preview(
    level_id: UUID,
    file: UploadFile,
    project: ProjectDep,
    paper_scale: int | None = Query(default=None, ge=1, le=5000),
) -> PlanImportPreview:
    """Lit le fichier (DXF, PDF vectoriel, SVG) et renvoie ses segments en mètres, ses calques
    et ses avertissements. Rien n'entre dans la scène ; le fichier n'est pas conservé.
    `paper_scale` : échelle papier 1:n choisie par l'utilisateur (PDF, SVG en mm)."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    name = (file.filename or "").lower()
    suffix = name[name.rfind(".") :] if "." in name else ""
    if suffix not in SUPPORTED:
        msg = (
            f"format non pris en charge : {suffix or 'sans extension'} ; "
            "DXF, PDF vectoriel ou SVG (une image de plan se met en fond et se vectorise)"
        )
        raise DetectionError(msg)
    data = await file.read()
    if suffix == ".pdf":
        return preview_pdf(data, paper_scale=paper_scale)
    if suffix == ".svg":
        return preview_svg(data, paper_scale=paper_scale)
    return preview_dxf(data)


class ImagePlanBody(StrictModel):
    image_id: UUID


@router.post("/projects/{project_id}/levels/{level_id}/import-plan/preview-image")
def import_plan_preview_image(
    level_id: UUID, body: ImagePlanBody, project: ProjectDep, store: StoreDep
) -> PlanImportPreview:
    """Traits épais de l'image mise en fond du plan (heuristique locale, pas de modèle) →
    segments en mètres via l'échelle du fond ; murs à créer en `estimated`."""
    return preview_image(project, store.project_dir(project.id), level_id, body.image_id)


@router.post("/projects/{project_id}/levels/{level_id}/import-plan")
def import_plan_apply(
    level_id: UUID, body: PlanApplyBody, project: ProjectDep, store: StoreDep, request: Request
) -> EditResult:
    """Les segments retenus deviennent des murs `observed` du niveau (vide), un seul commit,
    annulable ; invariants vérifiés."""
    after, _step = apply_segments(project, level_id, body)
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(after)
    usage_event(request, "plan_built", project.id, 0)  # KAN-109 L2 : plan importé
    return _result(after, level_id, report)
