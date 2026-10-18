"""3D : GLB généré par trimesh, exécution Blender optionnelle, artefacts des runs."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import Field

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.registry import resolve_device
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    LanguageModelDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
    get_image_finisher,
)
from openplan.api.jobs import JobStatus
from openplan.api.routes.scene import EditResult, _result
from openplan.pipeline import journal
from openplan.pipeline.autopilot import ACTOR as AUTOPILOT_ACTOR
from openplan.pipeline.autopilot import (
    FINISH_EXPECTED_S,
    INSPIRATION_EXPECTED_S,
    AutopilotRun,
    HomeRequest,
    Workspace,
    plan_estimate,
    run_autopilot,
)
from openplan.pipeline.blender_bridge import BlenderError, find_blender, new_run_dir, run_blender
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.finish import FinishEntry, FinishOptions, finish_render
from openplan.pipeline.inspire import (
    CATALOGUE_OPTIONS_MODE,
    DEFAULT_SCALE,
    InspirationEntry,
    InspirationOptions,
    inspire_render,
)
from openplan.pipeline.instruction import MAX_INSTRUCTION, Reading, read_instruction
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.render import (
    Daylight,
    NoCameraError,
    architect_view,
    camera_view,
    list_renders,
    render_camera_view,
    render_expectation,
    render_photo_view,
    section_view,
)
from openplan.pipeline.rerender import MAX_VIEWS, RerenderRun, plan_views, rerender
from openplan.pipeline.scene3d import bake_level, glb_bytes
from openplan.pipeline.timings import TIMINGS_FILE, Timings
from openplan.pipeline.views import AutoViews, apply_auto_views, views_to_render
from openplan.scene.catalog import load_catalog
from openplan.scene.ops import AddCamera, OpError
from openplan.schemas.bake import Bake, BakeStyle
from openplan.schemas.common import StrictModel
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.results import StepResult
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["scene3d"])
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class BlenderStatus(StrictModel):
    available: bool
    binary: str | None


class BlenderRun(StrictModel):
    run_id: UUID
    result: StepResult


class RenderEstimate(StrictModel):
    """Ce que l'écran Rendre annonce avant de lancer : Blender présent ou non, périphérique,
    durée attendue pour la qualité choisie."""

    blender_available: bool
    device: str
    device_label: str
    samples: int
    expected_s: float
    message: str


@router.get("/projects/{project_id}/levels/{level_id}/render/estimate")
def render_estimate(
    level_id: UUID,
    project: ProjectDep,
    settings: SettingsDep,
    samples: int = Query(default=64, ge=1, le=1024),
) -> RenderEstimate:
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    device, label = resolve_device(settings.device)
    expected, message = render_expectation(device, samples)
    available = find_blender(settings.blender_bin) is not None
    if not available:
        message = (
            "Blender n'est pas installé (ou introuvable, OPENPLAN_BLENDER_BIN) : pas de rendu "
            "réaliste ; la vignette 3D de chaque point de vue reste disponible."
        )
    return RenderEstimate(
        blender_available=available,
        device=device,
        device_label=label,
        samples=samples,
        expected_s=expected,
        message=message,
    )


@router.get("/projects/{project_id}/levels/{level_id}/cameras/architect")
def architect_camera(level_id: UUID, project: ProjectDep) -> AddCamera:
    """D3 : la vue d'architecte proposée (frontale, centrée, grand angle), en opération à
    appliquer par le client : l'ajout reste un geste de l'utilisateur, annulable."""
    return architect_view(project, level_id)


@router.get("/projects/{project_id}/levels/{level_id}/cameras/section")
def section_camera(
    level_id: UUID,
    project: ProjectDep,
    axis: Literal["x", "y"],
    value: float = Query(ge=-100.0, le=100.0),
) -> AddCamera:
    """E3b : la perspective de coupe (perpendiculaire au plan vertical, face à la tranche),
    en opération à appliquer par le client : geste de l'utilisateur, annulable."""
    return section_view(project, level_id, axis, value)


class AutoViewsBody(StrictModel):
    aspect: Literal["4:3", "4:5"] | None = None
    """Format voulu (écrit dans l'intention de la pièce) ; None = celui de l'intention."""


class AutoViewsResult(EditResult):
    views: AutoViews


@router.post("/projects/{project_id}/levels/{level_id}/views/auto")
def auto_views(
    level_id: UUID, body: AutoViewsBody, project: ProjectDep, store: StoreDep, actor: ActorDep
) -> AutoViewsResult:
    """V1 : les trois vues automatiques (ensemble depuis l'arrivée, zone, détail), posées en un
    pas d'historique annulable ; les vues d'avant sont remplacées sauf celles modifiées à la
    main. 409 sans pièce fermée."""
    after, views = apply_auto_views(project, level_id, actor=actor, aspect=body.aspect)
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(after)
    edit = _result(after, level_id, report)
    return AutoViewsResult(**edit.model_dump(), views=views)


class AutopilotBody(StrictModel):
    """Accueil (L1a) : la consigne écrite avant le dépôt, traduite à l'étape Ambiance."""

    instruction: str | None = Field(default=None, max_length=MAX_INSTRUCTION)
    answers: GuidedAnswers | None = None
    """Questions guidées (L2) : elles priment sur le texte."""


@router.post("/projects/{project_id}/levels/{level_id}/autopilot", status_code=202)
def autopilot(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
    model: LanguageModelDep,
    gpu: bool = True,
    body: AutopilotBody | None = None,
) -> JobStatus:
    """V1, point 6 : le chemin « zéro geste » après le plan automatique, en une tâche de fond
    à étapes (ambiance déduite, aménagement par zones, trois vues, rendus, finition si elle
    n'est pas coupée dans les réglages). Chaque modification est un pas d'historique
    annulable ; rien de choisi par l'utilisateur n'est réécrit ; l'éditeur reste utilisable
    (le projet est relu avant chaque modification). L'étape et le temps restant se lisent
    dans `stage`."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    device, _ = resolve_device(settings.device)
    device = device if gpu else "cpu"
    finish = project.render.auto_finish
    inspiration = project.render.auto_inspiration
    timings = Timings(store.root / TIMINGS_FILE)
    expected = plan_estimate(
        project, level_id, device, finish=finish, inspiration=inspiration, timings=timings
    )
    pid = project.id
    space = Workspace(lambda: store.load(pid), store.save, store.lock(pid))
    instruction = (body.instruction or "").strip() if body is not None else ""
    # Consigne gardée depuis l'accueil (L1b) : le chemin l'applique s'il n'en reçoit pas.
    instruction = instruction or (project.pending_instruction or "")
    # Réponses de la note Exigences du tableau (T1) : le chemin les lit s'il n'en reçoit pas.
    answers = (body.answers if body is not None else None) or project.pending_answers
    gateway = ModelGateway(store.project_dir(pid) / "network_log.jsonl")

    def read(text: str, current: Project) -> Reading:
        return read_instruction(
            text, load_catalog().categories, model=model, gateway=gateway, project=current
        )

    def taken() -> list[str]:
        return [s.name for s in store.list() if s.id != pid]

    def work(stage: Callable[..., None]) -> AutopilotRun:
        run = run_autopilot(
            space,
            level_id,
            store.runs_dir(pid),
            stage=stage,
            blender_bin=settings.blender_bin,
            device=device,
            gpu=gpu,
            finisher=(lambda: get_image_finisher(request)) if finish or inspiration else None,
            timings=timings,
            home=(
                HomeRequest(instruction, read=read, actor=actor, answers=answers)
                if instruction or answers is not None
                else None
            ),
            taken_names=taken,
        )
        # Journal V2 : ce que le chemin a posé (identifiants), pour rattacher ensuite ce que
        # l'utilisateur en garde, corrige ou annule.
        after = store.load(pid)
        level = after.levels.get(level_id)
        added = set(level.elements) - set(project.levels[level_id].elements) if level else set()
        if level is not None:  # la pièce dont l'ambiance a été déduite
            added |= {e.id for e in level.elements.values() if e.type == "room"}
        journal.record(
            store.project_dir(pid),
            after,
            JournalEvent(
                project_id=pid,
                kind="autopilot",
                text=instruction or None,
                translation={
                    "steps": [
                        {"step": s.name.split(":", 1)[0], "status": s.status} for s in run.steps
                    ]
                },
                by=AUTOPILOT_ACTOR,
                element_ids=sorted(added, key=str),
                run_ids=run.run_ids,
                history_ids=[
                    e.id
                    for e in after.history[project.history_cursor : after.history_cursor]
                    if e.actor == AUTOPILOT_ACTOR
                ],
            ),
        )
        return run

    return jobs.submit_staged(
        "autopilot",
        work,
        device=device,
        expected_s=expected,
        message=f"Chemin automatique : environ {expected:.0f} s",
        owner=actor,
        project_id=project.id,
        dedup=(project.id, level_id),
    )


class RenderAllRun(StrictModel):
    runs: list[BlenderRun]


@router.post("/projects/{project_id}/levels/{level_id}/render/all", status_code=202)
def render_all(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    samples: int = Query(default=64, ge=1, le=1024),
    gpu: bool = True,
    daylight: Daylight | None = None,
) -> JobStatus:
    """V1 : « Rendre toutes les vues », enchaînées dans une seule tâche de fond (vues
    automatiques d'abord, puis les autres points de vue libres) ; chaque rendu rejoint la
    galerie dès qu'il est fini, l'étape en cours se lit dans `stage`."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    cameras = views_to_render(level)
    if not cameras:
        msg = "aucun point de vue à rendre : posez d'abord les vues automatiques"
        raise NoCameraError(msg)
    if find_blender(settings.blender_bin) is None:
        msg = "Blender introuvable : installez-le ou renseignez OPENPLAN_BLENDER_BIN"
        raise BlenderError(msg)
    device, _ = resolve_device(settings.device)
    expected = 0.0
    for camera in cameras:
        _, spec = camera_view(project, level_id, camera.id)
        one, _ = render_expectation(
            device if gpu else "cpu", samples, spec.width_px * spec.height_px
        )
        expected += one * 1.3  # sonde d'exposition comprise
    runs_dir = store.runs_dir(project.id)
    blender_bin = settings.blender_bin

    def work(stage: Callable[[str], None]) -> RenderAllRun:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        fresh = store.load(project.id)
        runs: list[BlenderRun] = []
        for i, camera in enumerate(cameras, start=1):
            stage(f"Vue {i}/{len(cameras)} : {camera.name or 'point de vue'}")
            run_id, out_dir = new_run_dir(runs_dir)
            result = render_camera_view(
                fresh,
                level_id,
                camera.id,
                out_dir,
                blender_bin=blender_bin,
                samples=samples,
                gpu=gpu,
                run_id=run_id,
                daylight=daylight,
            )
            runs.append(BlenderRun(run_id=run_id, result=result))
        return RenderAllRun(runs=runs)

    return jobs.submit_staged(
        "render_all",
        work,
        device=device,
        expected_s=expected,
        message=f"{len(cameras)} vues à rendre, environ {expected:.0f} s",
        owner=actor,
        project_id=project.id,
        dedup=(project.id, level_id),
    )


class RerenderBody(StrictModel):
    camera_ids: list[UUID] = Field(min_length=1, max_length=MAX_VIEWS)


@router.post("/projects/{project_id}/levels/{level_id}/render/views", status_code=202)
def render_views(
    level_id: UUID,
    body: RerenderBody,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """P2 : « Re-rendre les n vues » périmées, en une tâche de fond. Chaque vue est refaite
    comme son dernier rendu (qualité, finition, image d'inspiration) ; la lumière d'une vue
    automatique suit l'ambiance du moment. Chaque rendu rejoint la galerie dès qu'il est fini."""
    plans = plan_views(
        project,
        level_id,
        body.camera_ids,
        list_renders(store.runs_dir(project.id)),
        auto_finish=project.render.auto_finish,
        auto_inspiration=project.render.auto_inspiration,
    )
    if find_blender(settings.blender_bin) is None:
        msg = "Blender introuvable : installez-le ou renseignez OPENPLAN_BLENDER_BIN"
        raise BlenderError(msg)
    device, _ = resolve_device(settings.device)
    timings = Timings(store.root / TIMINGS_FILE)
    expected = 0.0
    for plan in plans:
        _, spec = camera_view(project, level_id, plan.camera.id)
        one, _ = render_expectation(
            device if plan.gpu else "cpu", plan.samples, spec.width_px * spec.height_px
        )
        expected += one * 1.3
        if plan.finish is not None:
            expected += timings.estimate("finish", FINISH_EXPECTED_S)
        if plan.inspiration is not None:
            expected += timings.estimate("inspiration", INSPIRATION_EXPECTED_S)
    runs_dir = store.runs_dir(project.id)
    blender_bin = settings.blender_bin
    images = any(p.finish is not None or p.inspiration is not None for p in plans)

    def work(stage: Callable[[str], None]) -> RerenderRun:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        return rerender(
            store.load(project.id),
            level_id,
            plans,
            runs_dir,
            blender_bin=blender_bin,
            stage=stage,
            finisher=(lambda: get_image_finisher(request)) if images else None,
        )

    count = len(plans)
    return jobs.submit_staged(
        "render_views",
        work,
        device=device,
        expected_s=expected,
        message=f"{count} vue{'s' if count > 1 else ''} à re-rendre, environ {expected:.0f} s",
        owner=actor,
        project_id=project.id,
    )


@router.post("/projects/{project_id}/levels/{level_id}/cameras/{camera_id}/render", status_code=202)
def render_camera(  # noqa: PLR0913, PLR0917 - paramètres de requête FastAPI
    level_id: UUID,
    camera_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
    samples: int = Query(default=64, ge=1, le=1024),
    gpu: bool = True,
    daylight: Daylight | None = None,
    section_axis: Literal["z", "x", "y"] | None = None,
    section_value: float | None = Query(default=None, ge=-100.0, le=100.0),
) -> JobStatus:
    """Rendu Cycles depuis un point de vue (caméra d'une photo ou point de vue libre) en tâche
    de fond : la route répond tout de suite, l'état se suit par `GET .../jobs/{id}` ; le
    résultat rejoint la galerie (`GET .../renders`). `daylight` (matin, midi, soir, nuit)
    place le soleil d'après le nord du niveau ; sans lui, éclairage d'atelier neutre. 409 sans
    Blender ou sans ce point de vue."""
    camera_view(project, level_id, camera_id)  # 409 explicite avant de lancer quoi que ce soit
    if find_blender(settings.blender_bin) is None:
        msg = "Blender introuvable : installez-le ou renseignez OPENPLAN_BLENDER_BIN"
        raise BlenderError(msg)
    device, _ = resolve_device(settings.device)
    expected, message = render_expectation(device if gpu else "cpu", samples)
    run_id, out_dir = new_run_dir(store.runs_dir(project.id))
    blender_bin = settings.blender_bin

    def work() -> BlenderRun:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        result = render_camera_view(
            store.load(project.id),
            level_id,
            camera_id,
            out_dir,
            blender_bin=blender_bin,
            samples=samples,
            gpu=gpu,
            run_id=run_id,
            daylight=daylight,
            section=(section_axis, section_value)
            if section_axis is not None and section_value is not None
            else None,
        )
        return BlenderRun(run_id=run_id, result=result)

    return jobs.submit(
        "render",
        work,
        device=device,
        expected_s=expected,
        message=message,
        owner=actor,
        project_id=project.id,
    )


@router.get("/projects/{project_id}/exports/glb")
def export_glb(level_id: UUID, project: ProjectDep, style: BakeStyle = "provenance") -> Response:
    """`style=realistic` : teintes plates par matière (aperçu sans Blender)."""
    return Response(content=glb_bytes(project, level_id, style), media_type="model/gltf-binary")


@router.get("/projects/{project_id}/levels/{level_id}/bake")
def bake(level_id: UUID, project: ProjectDep) -> Bake:
    return bake_level(project, level_id)


@router.get("/blender")
def blender_status(settings: SettingsDep) -> BlenderStatus:
    binary = find_blender(settings.blender_bin)
    return BlenderStatus(available=binary is not None, binary=str(binary) if binary else None)


@router.post("/projects/{project_id}/levels/{level_id}/blender")
def run(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    preview: bool = True,
) -> BlenderRun:
    """Exécute Blender en arrière-plan (bloquant, quelques dizaines de secondes avec aperçu)."""
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    run_id, out_dir = new_run_dir(store.runs_dir(project.id))
    result = run_blender(
        project, level_id, out_dir, blender_bin=settings.blender_bin, preview=preview, run_id=run_id
    )
    return BlenderRun(run_id=run_id, result=result)


@router.post("/projects/{project_id}/levels/{level_id}/render")
def render_photo(
    level_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    samples: int = Query(default=64, ge=1, le=1024),
    gpu: bool = True,
) -> BlenderRun:
    """Rendu Cycles avec matières depuis la caméra de la photo (bloquant : de quelques
    secondes sur GPU à quelques minutes sur CPU)."""
    run_id, out_dir = new_run_dir(store.runs_dir(project.id))
    result = render_photo_view(
        project,
        level_id,
        out_dir,
        blender_bin=settings.blender_bin,
        samples=samples,
        gpu=gpu,
        run_id=run_id,
    )
    return BlenderRun(run_id=run_id, result=result)


@router.post("/projects/{project_id}/renders/{run_id}/finish", status_code=202)
def finish(
    run_id: UUID,
    body: FinishOptions,
    project: ProjectDep,
    store: StoreDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """D5 : « Finition IA » d'un rendu de la galerie, par un geste explicite. Img2img local à
    force bornée (0,35 par défaut, 0,55 au plus), guidé par la profondeur et les arêtes du
    rendu ; l'image Blender reste, l'image finie est `generated`. 409 si le rendu n'existe pas
    ou si les poids manquent (avec la commande à lancer)."""
    render_dir = store.runs_dir(project.id) / str(run_id) / "blender"
    if not (render_dir / "preview.png").is_file():
        msg = f"rendu introuvable : {run_id}"
        raise BlenderError(msg)
    finisher = get_image_finisher(request)

    def work() -> FinishEntry:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        fresh = store.load(project.id)
        return finish_render(fresh, render_dir, run_id, body, finisher=finisher)

    return jobs.submit(
        "finish",
        work,
        device="cuda",
        expected_s=25.0,
        message="Finition IA, environ 25 s",
        owner=actor,
        project_id=project.id,
    )


@router.post("/projects/{project_id}/renders/{run_id}/catalogue", status_code=202)
def catalogue(
    run_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    jobs: JobsDep,
    actor: ActorDep,
    request: Request,
) -> JobStatus:
    """V1.7b : « Version catalogue » d'une vue, à la demande seulement (jamais dans le chemin
    automatique) : txt2img guidé par la profondeur et les arêtes à 1,2, allure la plus
    « catalogue », marquée « Inspiration · matières libres » ; les autres images restent.
    409 si le rendu n'existe pas ou si les poids manquent."""
    render_dir = store.runs_dir(project.id) / str(run_id) / "blender"
    if not (render_dir / "preview.png").is_file():
        msg = f"rendu introuvable : {run_id}"
        raise BlenderError(msg)
    model = get_image_finisher(request)

    def work() -> InspirationEntry:
        # File d'attente : relu au démarrage, pas l'état lu au clic.
        return inspire_render(
            store.load(project.id),
            render_dir,
            run_id,
            InspirationOptions(mode=CATALOGUE_OPTIONS_MODE, control_scale=DEFAULT_SCALE),
            inspirer=model,  # type: ignore[arg-type]
            catalogue=True,
        )

    return jobs.submit(
        "catalogue",
        work,
        device="cuda",
        expected_s=45.0,
        message="Version catalogue, environ 45 s",
        owner=actor,
        project_id=project.id,
    )


@router.get("/projects/{project_id}/runs/{run_id}/blender/{name}")
def run_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> FileResponse:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise OpError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "blender" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise BlenderError(msg)
    return FileResponse(path)
