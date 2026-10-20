"""KAN-66 : mesure de référence sur une photo (deux points, une longueur connue) qui recale le
plan estimé depuis cette photo ; « Réestimer la photo pour pouvoir mesurer » pour une photo
d'avant, dont la profondeur n'a pas été gardée."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from openplan.adapters.registry import resolve_device
from openplan.api.change_events import author_of
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
    get_depth_estimator,
)
from openplan.api.jobs import JobStatus
from openplan.api.routes.autoplan import EXPECTED_S
from openplan.pipeline.reference import (
    ReferenceBody,
    ReferenceRefusedError,
    apply_reference,
    reestimate,
    remove_reference,
)
from openplan.schemas.common import StrictModel
from openplan.schemas.scene import Project

router = APIRouter(tags=["mesure de référence"])


class ReferenceRequest(StrictModel):
    p1: tuple[float, float]
    p2: tuple[float, float]
    """Pixels de la photo d'origine (orientation appliquée)."""
    length_m: float
    """La longueur connue, en mètres ; hors de 5 cm à 30 m, refus (422) avec la raison."""
    label: str | None = Field(default=None, max_length=60)


def _refused(exc: ReferenceRefusedError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.put("/projects/{project_id}/images/{image_id}/reference")
def put_reference(
    image_id: UUID,
    body: ReferenceRequest,
    project: ProjectDep,
    store: StoreDep,
    actor: ActorDep,
) -> Project:
    """Pose la mesure de référence de la photo (au plus une : elle REMPLACE la précédente) et
    recale le plan qui en vient, hauteurs comprises ; geste annulable. 422 avec la raison si
    la mesure est incohérente (points hors de la photo ou sur deux surfaces, longueur hors
    limites, échelle changée de plus de ×3) ou si la photo est à réestimer."""
    try:
        after = apply_reference(
            project,
            store.project_dir(project.id),
            image_id,
            ReferenceBody(**body.model_dump()),
            actor=actor,
            author=author_of(actor),
        )
    except ReferenceRefusedError as exc:
        raise _refused(exc) from exc
    store.save(after)
    return after


@router.delete("/projects/{project_id}/images/{image_id}/reference/{segment_id}")
def delete_reference(
    image_id: UUID, segment_id: UUID, project: ProjectDep, store: StoreDep, actor: ActorDep
) -> Project:
    """Retire la mesure : le plan revient à l'échelle de la hauteur de prise de vue ; annulable."""
    after = remove_reference(project, image_id, segment_id, actor=actor)
    store.save(after)
    return after


@router.post(
    "/projects/{project_id}/levels/{level_id}/images/{image_id}/reestimate", status_code=202
)
def reestimate_route(
    level_id: UUID,
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """« Réestimer la photo pour pouvoir mesurer » : un passage du modèle de profondeur sur une
    photo d'avant KAN-66, gardé ; le plan n'est pas reconstruit (retouches gardées), seule
    l'origine de son échelle est notée. Le projet se relit à la fin de la tâche."""
    if level_id not in project.levels:
        raise HTTPException(status_code=404, detail=f"niveau inconnu : {level_id}")
    if image_id not in project.images:
        raise HTTPException(status_code=404, detail=f"image inconnue : {image_id}")
    project_id = project.id
    device, _ = resolve_device(settings.device)
    estimator = get_depth_estimator(request)

    def work(progress: Callable[[str], None]) -> Project:
        progress("Estimation des distances…")
        before = store.load(project_id)
        after = reestimate(before, store.project_dir(project_id), level_id, image_id, estimator)
        if after is not before:
            latest = store.load(project_id)
            level = latest.levels.get(level_id)
            if level is None:
                msg = "le plan a été supprimé pendant l'estimation"
                raise ReferenceRefusedError(msg)
            # Seule l'origine de l'échelle change : posée sur le projet tel qu'il est maintenant.
            basis = after.levels[level_id].scale_basis
            after = latest.model_copy(
                update={
                    "levels": {
                        **latest.levels,
                        level_id: level.model_copy(update={"scale_basis": basis}),
                    }
                }
            )
            store.save(after)
        progress("Photo prête pour la mesure")
        return after

    expected = EXPECTED_S.get(device, EXPECTED_S["cpu"])
    return jobs.submit_staged(
        "reestimate",
        work,
        device=device,
        expected_s=expected,
        message=f"Estimation des distances, environ {expected:.0f} s",
        owner=actor,
        project_id=project_id,
        dedup=(project_id, level_id, "reestimate"),
    )
