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
from openplan.api.access import usage_event
from openplan.api.board_rules import require_plan_photo
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
from openplan.api.routes.attic_fusion import fuse_members, fuse_project
from openplan.api.settings import Settings
from openplan.pipeline.autoplan import AutoPlanRun, ImageKind, run_autoplan
from openplan.pipeline.client_depth import StoredDepthEstimator, has_stored_depth
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.pipeline.regenerate import cleared, regenerated
from openplan.scene import history
from openplan.scene.store import ProjectStore
from openplan.schemas.common import StrictModel
from openplan.schemas.results import ValidationReport
from openplan.schemas.scene import Project
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


OPENINGS_NOT_ONLINE = (
    "Portes et fenêtres pas encore détectées en ligne : ajoutez-les dans le plan si besoin."
)
"""KAN-79 : distances lues dans la profondeur du navigateur, seul le détecteur manque."""


FINER_NOT_ONLINE = (
    "Analyse plus fine (distances, portes et fenêtres) indisponible en ligne : le plan vient "
    "des lignes de la photo."
)
"""API exposée : rien à installer pour l'utilisateur, ni identifiant de modèle (MEP 15)."""


def _missing_notice(missing: list[str], *, exposed: bool) -> list[str]:
    """Une seule phrase pour tout ce qui manque ; le plan est construit quand même avec ce qui
    est installé. Sur la boucle locale, avec la commande à lancer (l'utilisateur administre sa
    machine) ; en ligne, une phrase fixe."""
    if not missing:
        return []
    if exposed:
        return [FINER_NOT_ONLINE]
    return [
        "Analyse plus fine non installée (distances, portes et fenêtres) : le plan vient des "
        "lignes de la photo. Pour l'activer : " + " ; ".join(dict.fromkeys(missing))
    ]


def announce_written(
    request: Request, settings: Settings, project_id: UUID, conn: str | None
) -> None:
    """KAN-120 : avec les comptes, le plan écrit par la tâche est annoncé aux co-éditeurs à
    l'écriture finale (sinon ils travailleraient sur l'ancien plan et l'écraseraient)."""
    if not settings.accounts_enabled:
        return
    from openplan.api.accounts.live import announce_changed_from_thread

    announce_changed_from_thread(request.app, project_id, exclude=conn)


def _replace(
    store: ProjectStore,
    request: Request,
    actor: str,
    level_id: UUID,
    run: AutoPlanRun,
    before: Project,
    progress: Callable[[str], None],
    depth: DepthEstimator | None,
) -> AutoPlanResult:
    """KAN-120, « Régénérer le plan » : recoupement en mémoire, meubles remis s'ils tiennent
    dans les nouveaux murs, puis UNE écriture et une seule entrée d'historique (Ctrl+Z remet
    l'ancien plan en entier). Rien n'est écrit si le calcul n'aboutit pas."""
    if run.method == "none":
        msg = "aucun mur n'a pu être deviné : le plan actuel est gardé tel quel"
        raise DetectionError(msg)
    rebuilt, notices, photos = run.project, list(run.notices), 1
    try:
        fused = fuse_project(rebuilt, store, level_id, depth, progress)
    except DetectionError:
        fused = None
    if fused is not None:
        if fused.applied:
            rebuilt = fused.project
            photos = max(1, len(fused.fusion.photos) if fused.fusion else 1)
        notices.extend(fused.notices)
    done = regenerated(before, rebuilt, level_id)
    if done.removed_furniture:
        n = done.removed_furniture
        plural = "s" if n > 1 else ""
        notices.append(f"{n} meuble{plural} hors des nouveaux murs retiré{plural}.")
    latest = store.load(before.id)
    if (latest.history_cursor, len(latest.history)) != (
        before.history_cursor,
        len(before.history),
    ):
        msg = (
            "le plan a été modifié pendant le calcul : rien n'a été remplacé, relancez "
            "« Régénérer le plan »"
        )
        raise DetectionError(msg)
    label = f"Plan régénéré à partir de {photos} photo{'s' if photos > 1 else ''}"
    after = history.commit(before, done.project, actor=actor, label=label)
    # Une image ajoutée pendant le calcul reste dans le projet.
    after = after.model_copy(update={"images": {**after.images, **latest.images}})
    report = validate_invariants(after)
    if not report.valid:
        msg = "le plan régénéré est incohérent : le plan actuel est gardé tel quel"
        raise DetectionError(msg)
    store.save(after)
    usage_event(request, "plan_built", before.id, photos)
    progress("Plan prêt")
    result = run.model_copy(update={"project": after, "notices": notices})
    return AutoPlanResult(run=result, report=report)


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
    replace: bool = False,
) -> JobStatus:
    """Construit le plan depuis l'image déposée, sans question ni choix de méthode : photo
    d'une pièce (lignes, distances, pentes de toit, portes et fenêtres, meilleure méthode
    gardée) ou image d'un plan (redressement, échelle devinée, murs). Répond tout de suite ;
    l'étape en cours se lit dans `GET .../jobs/{id}` (`stage`). Ce qui manque (poids, Tesseract)
    est dit une fois dans `notices`, jamais bloquant. `kind` impose la lecture (photo ou plan)
    quand l'utilisateur corrige celle qui a été devinée. KAN-120, `replace` : « Régénérer le
    plan » ; le plan est refait comme sur un niveau vide puis remplacé en une seule étape
    d'annulation, ou laissé intact si le calcul échoue."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if image_id not in project.images:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    # KAN-91 (C1) : seule une photo de la carte Photos reliée à ce plan le construit.
    require_plan_photo(project, store, level_id, image_id)
    project_id = project.id
    device, _ = resolve_device(settings.device)
    conn = request.headers.get("x-live-conn")

    def work(progress: Callable[[str], None]) -> AutoPlanResult:
        progress("Préparation…")
        missing: list[str] = []
        exposed = not settings.local_only
        project_dir = store.project_dir(project_id)
        # KAN-79 : en ligne, la profondeur envoyée par le navigateur est relue, sans modèle.
        from_browser = settings.accounts_enabled and has_stored_depth(project_dir, image_id)
        depth: DepthEstimator | None = (
            StoredDepthEstimator(project_dir)
            if from_browser
            else _optional(get_depth_estimator, request, missing, exposed=exposed)
        )
        detector: RoomElementDetector | None = _optional(
            get_opening_detector, request, missing, exposed=exposed
        )
        notices = (
            [OPENINGS_NOT_ONLINE]
            if from_browser and missing
            else _missing_notice(missing, exposed=exposed)
        )
        before = store.load(project_id)
        run = run_autoplan(
            cleared(before, level_id) if replace else before,
            store.project_dir(project_id),
            level_id,
            image_id,
            store.runs_dir(project_id),
            depth=depth,
            openings=detector,
            notices=notices,
            progress=progress,
            kind=kind,
        )
        report = validate_invariants(run.project)
        if not report.valid:
            msg = "le plan construit est incohérent : rien n'a été enregistré"
            raise DetectionError(msg)
        if replace:
            done = _replace(store, request, actor, level_id, run, before, progress, depth)
            announce_written(request, settings, project_id, conn)
            return done
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
        # KAN-95 : au moins deux photos dans la carte reliée : recoupement (combles, ou pièce
        # droite estimée depuis une photo).
        try:
            fused = fuse_members(store, project_id, level_id, depth, progress)
        except DetectionError:
            fused = None  # le plan de cette photo reste enregistré ; recoupement à relancer
        photos = 1
        if fused is not None:  # appliqué ou non, ce qu'il en est est dit
            if fused.applied:
                after = fused.project
                photos = max(1, len(fused.fusion.photos) if fused.fusion else 1)
            run = run.model_copy(update={"notices": [*run.notices, *fused.notices]})
        # KAN-109 L2 : un plan construit (value : nombre de photos lues).
        usage_event(request, "plan_built", project_id, photos)
        announce_written(request, settings, project_id, conn)
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
        project_id=project.id,
        dedup=(project.id, level_id),
    )
