"""Chaîne automatique à l'import : du fichier déposé au plan construit, en tâche de fond."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import APIRouter, Request

from openplan.adapters.protocols import (
    AdapterUnavailableError,
    DepthEstimator,
    RoomElementDetector,
)
from openplan.adapters.registry import resolve_device
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
    get_depth_estimator,
    get_opening_detector,
)
from openplan.api.jobs import JobStatus
from openplan.api.public_errors import public_detail
from openplan.pipeline.autoplan import AutoPlanRun, ImageKind, run_autoplan
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.schemas.common import StrictModel
from openplan.schemas.results import ValidationReport
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["autoplan"])

EXPECTED_S = {"cuda": 15.0, "cpu": 60.0}
"""GPU : mesuré le 29 septembre 2026 sur la machine d'augus (7 à 16 s, photo 1280 × 960 et
photo de grenier, poids déjà chargés ou non). Processeur : ordre de grandeur, non mesuré."""


class AutoPlanResult(StrictModel):
    run: AutoPlanRun
    report: ValidationReport


def _optional[T](
    build: Callable[[Request], T], request: Request, missing: list[str], *, exposed: bool
) -> T | None:
    try:
        return build(request)
    except AdapterUnavailableError as exc:
        # API exposée : la phrase fixe, sans chemin du serveur (contrôle D2).
        missing.append(public_detail(exc, exposed=exposed))
        return None


def _missing_notice(missing: list[str]) -> list[str]:
    """Une seule phrase pour tout ce qui manque, avec la commande ; le plan est construit
    quand même avec ce qui est installé."""
    if not missing:
        return []
    return [
        "Analyse plus fine non installée (distances, portes et fenêtres) : le plan vient des "
        "lignes de la photo. Pour l'activer : " + " ; ".join(dict.fromkeys(missing))
    ]


@router.post("/projects/{project_id}/levels/{level_id}/images/{image_id}/autoplan", status_code=202)
def autoplan(
    level_id: UUID,
    image_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
    kind: ImageKind | None = None,
) -> JobStatus:
    """Construit le plan depuis l'image déposée, sans question ni choix de méthode : photo
    d'une pièce (lignes, distances, pentes de toit, portes et fenêtres, meilleure méthode
    gardée) ou image d'un plan (redressement, échelle devinée, murs). Répond tout de suite ;
    l'étape en cours se lit dans `GET .../jobs/{id}` (`stage`). Ce qui manque (poids, Tesseract)
    est dit une fois dans `notices`, jamais bloquant. `kind` impose la lecture (photo ou plan)
    quand l'utilisateur corrige celle qui a été devinée."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    project_id = project.id
    device, _ = resolve_device(settings.device)

    def work(progress: Callable[[str], None]) -> AutoPlanResult:
        progress("Préparation…")
        missing: list[str] = []
        exposed = not settings.local_only
        depth: DepthEstimator | None = _optional(
            get_depth_estimator, request, missing, exposed=exposed
        )
        detector: RoomElementDetector | None = _optional(
            get_opening_detector, request, missing, exposed=exposed
        )
        before = store.load(project_id)
        run = run_autoplan(
            before,
            store.project_dir(project_id),
            level_id,
            image_id,
            store.runs_dir(project_id),
            depth=depth,
            openings=detector,
            notices=_missing_notice(missing),
            progress=progress,
            kind=kind,
        )
        report = validate_invariants(run.project)
        if not report.valid:
            msg = "le plan construit est incohérent : rien n'a été enregistré"
            raise DetectionError(msg)
        latest = store.load(project_id)
        if (latest.history_cursor, len(latest.history)) != (
            before.history_cursor,
            len(before.history),
        ):
            msg = (
                "le plan a été modifié pendant l'analyse : rien n'a été écrasé, relancez "
                "l'analyse de la photo"
            )
            raise DetectionError(msg)
        # Une image ajoutée pendant le calcul reste dans le projet.
        after = run.project.model_copy(update={"images": {**run.project.images, **latest.images}})
        store.save(after)
        progress("Plan prêt")
        return AutoPlanResult(run=run.model_copy(update={"project": after}), report=report)

    expected = EXPECTED_S.get(device, EXPECTED_S["cpu"])
    return jobs.submit_staged(
        "autoplan",
        work,
        device=device,
        expected_s=expected,
        message=f"Construction du plan, environ {expected:.0f} s",
        owner=actor,
    )
