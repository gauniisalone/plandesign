"""Mesures connues, toujours d'autorité utilisateur."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from openplan.api.deps import ProjectDep, StoreDep
from openplan.pipeline.editing import InvalidEditError
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.scene.ops import OpError
from openplan.schemas.scene import Measurement, Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["measurements"])


@router.post("/projects/{project_id}/measurements", status_code=201)
def add_measurement(body: Measurement, project: ProjectDep, store: StoreDep) -> Project:
    after = project.model_copy(update={"measurements": {**project.measurements, body.id: body}})
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    after = history.commit(project, after, actor=USER_ACTOR, label="Ajouter une mesure")
    store.save(after)
    return after


@router.delete("/projects/{project_id}/measurements/{measurement_id}")
def remove_measurement(measurement_id: UUID, project: ProjectDep, store: StoreDep) -> Project:
    if measurement_id not in project.measurements:
        msg = f"mesure inconnue : {measurement_id}"
        raise OpError(msg)
    remaining = {k: v for k, v in project.measurements.items() if k != measurement_id}
    after = project.model_copy(update={"measurements": remaining})
    after = history.commit(project, after, actor=USER_ACTOR, label="Supprimer une mesure")
    store.save(after)
    return after
