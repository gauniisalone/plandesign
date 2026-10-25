"""KAN-95 : plan recoupé sur les photos de la carte Photos reliée au plan (combles, et depuis
P2 pièces droites)."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Request

from openplan.adapters.protocols import AdapterUnavailableError, DepthEstimator
from openplan.adapters.registry import resolve_device
from openplan.api import board_rules
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
    get_depth_estimator,
)
from openplan.api.jobs import JobRegistry, JobStatus
from openplan.api.settings import Settings
from openplan.pipeline import board_links as bl
from openplan.pipeline.attic_fusion import MIN_PHOTOS, FusionRun, fuse_attic
from openplan.pipeline.client_depth import StoredDepthEstimator
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.room_fusion import RoomFusionRun, fuse_room
from openplan.scene.store import ProjectStore
from openplan.schemas.elements import Wall
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["autoplan"])

EXPECTED_S = {"cuda": 20.0, "cpu": 90.0}
"""Ordre de grandeur : la profondeur des photos déjà gardée n'est pas recalculée."""


def member_photos(project: Project, store: ProjectStore, level_id: UUID) -> list[UUID]:
    """Les photos qui comptent pour ce plan : membres de la carte Photos reliée (C1)."""
    board = board_rules.board_now(project, store)
    plan = bl.plan_item(board, level_id)
    if plan is None:
        return []
    out: list[UUID] = []
    for ref in bl.generation_inputs(board, plan.id).photos:
        try:
            image_id = UUID(ref)
        except ValueError:
            continue
        if image_id in project.images:
            out.append(image_id)
    return out


def fuse_members(
    store: ProjectStore,
    project_id: UUID,
    level_id: UUID,
    depth: DepthEstimator | None,
    progress: Callable[[str], None] | None = None,
    *,
    force: bool = False,
) -> FusionRun | RoomFusionRun | None:
    """Recoupe les photos membres quand la carte en compte au moins deux : combles (`force` :
    même si le niveau n'est pas encore en combles), sinon pièce droite estimée depuis une photo
    (P2b ; un plan tiré d'une image de plan dessiné n'est jamais touché). Enregistre le plan
    recoupé si rien n'a changé pendant le calcul ; None quand il n'y a rien à recouper."""
    before = store.load(project_id)
    level = before.levels.get(level_id)
    if level is None:
        return None
    attic = level.kind == "attic" or force
    from_photo = level.scale_basis is not None and any(
        isinstance(e, Wall) for e in level.elements.values()
    )
    if not attic and not from_photo:
        return None
    photos = member_photos(before, store, level_id)
    if len(photos) < MIN_PHOTOS:
        return None
    if progress is not None:
        progress(f"Recoupement des {len(photos)} photos…")
    project_dir = store.project_dir(project_id)
    run: FusionRun | RoomFusionRun = (
        fuse_attic(before, project_dir, level_id, photos, estimator=depth)
        if attic
        else fuse_room(before, project_dir, level_id, photos, estimator=depth)
    )
    if not run.applied:
        return run
    if not validate_invariants(run.project).valid:
        return run.model_copy(
            update={
                "applied": False,
                "project": before,
                "notices": ["Plan recoupé incohérent : le plan n'a pas été remplacé."],
            }
        )
    latest = store.load(project_id)
    if (latest.history_cursor, len(latest.history)) != (before.history_cursor, len(before.history)):
        msg = "le plan a été modifié pendant le recoupement : rien n'a été écrasé, relancez-le"
        raise DetectionError(msg)
    after = run.project.model_copy(update={"images": {**run.project.images, **latest.images}})
    store.save(after)
    return run.model_copy(update={"project": after})


@router.post("/projects/{project_id}/levels/{level_id}/attic-fusion", status_code=202)
def attic_fusion(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """Recoupe les combles sur toutes les photos de la carte Photos reliée à ce plan (au
    moins deux). Répond tout de suite ; le résultat (`FusionRun`) se lit dans le job."""
    return _submit(level_id, project, store, settings, jobs, actor, request, force=True)


@router.post("/projects/{project_id}/levels/{level_id}/photo-fusion", status_code=202)
def photo_fusion(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """KAN-95 (P2) : recoupe les photos de la carte reliée selon la pièce (combles, ou pièce
    droite estimée depuis une photo). Le résultat se lit dans le job."""
    return _submit(level_id, project, store, settings, jobs, actor, request, force=False)


def _submit(
    level_id: UUID,
    project: Project,
    store: ProjectStore,
    settings: Settings,
    jobs: JobRegistry,
    actor: str,
    request: Request,
    *,
    force: bool,
) -> JobStatus:
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    project_id = project.id
    device, _ = resolve_device(settings.device)

    def work(progress: Callable[[str], None]) -> FusionRun | RoomFusionRun:
        progress("Préparation…")
        depth: DepthEstimator | None
        if settings.accounts_enabled:
            depth = StoredDepthEstimator(store.project_dir(project_id))
        else:
            try:
                depth = get_depth_estimator(request)
            except AdapterUnavailableError:
                depth = None
        run = fuse_members(store, project_id, level_id, depth, progress, force=force)
        if run is None:
            msg = "il faut au moins deux photos dans la carte Photos reliée à un plan estimé"
            raise DetectionError(msg)
        progress("Plan recoupé" if run.applied else "Recoupement non appliqué")
        return run

    expected = EXPECTED_S.get(device, EXPECTED_S["cpu"])
    return jobs.submit_staged(
        "attic_fusion",
        work,
        device=device,
        expected_s=expected,
        message=f"Recoupement des photos, environ {expected:.0f} s",
        owner=actor,
        project_id=project_id,
        dedup=(project_id, level_id, "attic_fusion"),
    )
