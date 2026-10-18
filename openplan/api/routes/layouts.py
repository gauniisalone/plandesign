"""Générateur de dispositions (3.4) : variantes calculées depuis un brief, appliquées au choix."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import FileResponse

from openplan.adapters.gateway import NETWORK_LOG, ModelGateway
from openplan.adapters.registry import resolve_device
from openplan.api.deps import (
    ActorDep,
    JobsDep,
    LanguageModelDep,
    ProjectDep,
    SettingsDep,
    StoreDep,
)
from openplan.api.jobs import JobStatus
from openplan.api.routes.scene import EditResult, _result
from openplan.api.routes.scene3d import BlenderRun
from openplan.pipeline import journal
from openplan.pipeline.blender_bridge import BlenderError, find_blender, new_run_dir
from openplan.pipeline.critique import (
    CritiqueBody,
    CritiquePreview,
    CritiqueRun,
    preview_critique,
    run_critique,
)
from openplan.pipeline.editing import InvalidEditError
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.layouts import (
    ApplyBody,
    ElementTrialBody,
    FurnitureMoveBody,
    FurnitureMoveCheck,
    FurniturePlaceBody,
    LayoutBody,
    LayoutProposal,
    LayoutRun,
    ReviseBody,
    ZoneBriefBody,
    apply_layout,
    brief_from_intent,
    check_element_trial,
    check_furniture_move,
    check_furniture_place,
    generate_layouts,
    revise_proposal,
    write_layout_artifact,
)
from openplan.pipeline.render import (
    VARIANT_SAMPLES,
    VARIANT_SIZE,
    Daylight,
    overview_camera,
    render_expectation,
    render_proposal,
)
from openplan.schemas.common import StrictModel
from openplan.schemas.layout import LayoutBrief
from openplan.validation.invariants import validate_invariants

router = APIRouter(tags=["layouts"])


class RenderProposalBody(StrictModel):
    proposal: LayoutProposal
    samples: int = VARIANT_SAMPLES
    gpu: bool = True
    daylight: Daylight | None = None


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


@router.post("/projects/{project_id}/levels/{level_id}/layouts")
def propose_layouts(
    level_id: UUID, body: LayoutBody, project: ProjectDep, store: StoreDep
) -> LayoutRun:
    """Brief → N variantes valides et distinctes, chacune avec son score et sa phrase de
    synthèse. Moteur géométrique local, sans modèle ; la scène ne change pas (artefact
    `layouts.json`). 409 sans pièce fermée."""
    run = generate_layouts(project, level_id, body)
    write_layout_artifact(run, store.runs_dir(project.id))
    return run


@router.post("/projects/{project_id}/levels/{level_id}/layouts/apply")
def apply_proposal(
    level_id: UUID, body: ApplyBody, project: ProjectDep, store: StoreDep, actor: ActorDep
) -> EditResult:
    """« Appliquer cette variante » : les meubles entrent dans la scène (source `generated`,
    acteur utilisateur, annulable) ; invariants vérifiés, 422 si une violation dure."""
    after = apply_layout(project, level_id, body.proposal, actor=actor)
    report = validate_invariants(after)
    if not report.valid:
        raise InvalidEditError(report)
    store.save(after)
    added = set(after.levels[level_id].elements) - set(project.levels[level_id].elements)
    journal.record(
        store.project_dir(project.id),
        after,
        JournalEvent(
            project_id=project.id,
            kind="layout_applied",
            by="générateur",
            element_ids=sorted(added, key=str),
            history_ids=journal.history_ids(project, after),
            note=f"variante {body.proposal.index} gardée ({len(added)} meubles)",
        ),
    )
    return _result(after, level_id, report)


@router.post("/projects/{project_id}/levels/{level_id}/layouts/critique/preview")
def critique_preview(
    level_id: UUID, body: CritiqueBody, project: ProjectDep, model: LanguageModelDep
) -> CritiquePreview:
    """Ce qui partirait au modèle pour critiquer cette variante (texte seul) et vers où,
    sans rien envoyer."""
    return preview_critique(project, level_id, body, model)


@router.post("/projects/{project_id}/levels/{level_id}/layouts/critique")
def critique_proposal(
    level_id: UUID,
    body: CritiqueBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
) -> CritiqueRun:
    """Rôle Critique (3.5) : remarques triées par importance, chacune citant un meuble et une
    règle, avec une action en un clic quand c'est possible. Règles seules sans modèle (ou avec
    `use_model: false`) ; consentement exigé pour un modèle distant ; la scène ne change pas."""
    gateway = ModelGateway(store.project_dir(project.id) / NETWORK_LOG) if model else None
    return run_critique(
        project, level_id, body, store.runs_dir(project.id), model=model, gateway=gateway
    )


@router.post("/projects/{project_id}/levels/{level_id}/layouts/revise")
def revise(level_id: UUID, body: ReviseBody, project: ProjectDep) -> LayoutProposal:
    """Action d'une remarque → variante révisée à relire (rien n'entre dans la scène) ; 409
    quand aucune position ne convient, la variante d'origine reste."""
    return revise_proposal(project, level_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/furniture/check")
def furniture_place_check(
    level_id: UUID, body: FurniturePlaceBody, project: ProjectDep
) -> FurnitureMoveCheck:
    """Bibliothèque (F3) : un meuble du catalogue essayé sous le curseur, avant de le poser ;
    mêmes règles et même réponse que le glisser, la scène ne change pas."""
    return check_furniture_place(project, level_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/furniture/{element_id}/check")
def furniture_check(
    level_id: UUID, element_id: UUID, body: FurnitureMoveBody, project: ProjectDep
) -> FurnitureMoveCheck:
    """Glisser-déposer d'un meuble (3.6) : les règles à la position essayée, sans modifier la
    scène ; `valid` faux et `message` court dès qu'une règle dure est touchée."""
    return check_furniture_move(project, level_id, element_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/brief/zones")
def zone_brief_route(level_id: UUID, body: ZoneBriefBody, project: ProjectDep) -> LayoutBrief:
    """Brief par zones (V1) d'après l'ambiance de la pièce : zones, kits de meubles, densité
    cible (0,3–0,5 meuble/m²) ; sans modèle, rien n'est placé ni écrit."""
    return brief_from_intent(project, level_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/elements/{element_id}/check")
def element_check(
    level_id: UUID, element_id: UUID, body: ElementTrialBody, project: ProjectDep
) -> FurnitureMoveCheck:
    """Rotation d'un escalier ou retournement d'une porte (E4) : les règles dans l'état
    essayé, sans modifier la scène ; `valid` faux dès qu'une règle dure est touchée."""
    return check_element_trial(project, level_id, element_id, body)


@router.post("/projects/{project_id}/levels/{level_id}/layouts/render", status_code=202)
def render_variant(
    level_id: UUID,
    body: RenderProposalBody,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    jobs: JobsDep,
    actor: ActorDep,
) -> JobStatus:
    """Vignette Blender d'une variante (3.7), sans l'appliquer : tâche de fond (202, suivi par
    `GET …/jobs/{id}`), résultat dans la galerie avec le numéro de la variante. 409 sans
    Blender ou sans meuble."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    if not body.proposal.furniture:
        msg = "cette variante ne contient aucun meuble"
        raise DetectionError(msg)
    overview_camera(level, body.proposal.furniture)  # 409 explicite s'il n'y a rien à cadrer
    if find_blender(settings.blender_bin) is None:
        msg = "Blender introuvable : installez-le ou renseignez OPENPLAN_BLENDER_BIN"
        raise BlenderError(msg)
    samples = max(1, min(256, body.samples))
    device, _ = resolve_device(settings.device)
    expected, message = render_expectation(
        device if body.gpu else "cpu", samples, VARIANT_SIZE[0] * VARIANT_SIZE[1]
    )
    run_id, out_dir = new_run_dir(store.runs_dir(project.id))
    blender_bin = settings.blender_bin
    proposal, gpu, daylight = body.proposal, body.gpu, body.daylight

    def work() -> BlenderRun:
        result = render_proposal(
            project,
            level_id,
            proposal,
            out_dir,
            blender_bin=blender_bin,
            samples=samples,
            gpu=gpu,
            run_id=run_id,
            daylight=daylight,
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


@router.get("/projects/{project_id}/runs/{run_id}/layout/{name}")
def layout_artifact(project: ProjectDep, run_id: UUID, name: str, store: StoreDep) -> FileResponse:
    if not _SAFE_NAME.match(name):
        msg = f"nom d'artefact invalide : {name!r}"
        raise DetectionError(msg)
    path = store.runs_dir(project.id) / str(run_id) / "layout" / name
    if not path.is_file():
        msg = f"artefact introuvable : {name}"
        raise DetectionError(msg)
    return FileResponse(path)
