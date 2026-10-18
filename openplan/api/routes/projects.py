"""Projets : création, liste, lecture, suppression complète."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request, Response
from pydantic import Field

from openplan.adapters.gateway import ModelGateway
from openplan.api.deps import LanguageModelDep, ProjectDep, SettingsDep, StoreDep
from openplan.api.files import served_file
from openplan.api.guards import forget_project, register_project, role_of, visible_summaries
from openplan.pipeline import home_settings
from openplan.pipeline.cover import project_cover
from openplan.pipeline.generated import generate_plan
from openplan.pipeline.guided import AreaGuard, area_guard, undo_area_guard
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.pipeline.instruction import MAX_INSTRUCTION
from openplan.pipeline.naming import auto_name, listed_name, looks_like_file_name, rename
from openplan.pipeline.programme import Programme, read_programme
from openplan.pipeline.starter import MAX_AREA_M2, MIN_AREA_M2, area_in_text, starter_plan
from openplan.scene.factory import new_project
from openplan.scene.history import USER_ACTOR
from openplan.scene.store import ProjectNotFoundError, ProjectSummary
from openplan.schemas.common import StrictModel
from openplan.schemas.home import GuidedAnswers
from openplan.schemas.scene import Project

router = APIRouter(tags=["projects"])


NAMING_ACTOR = "pipeline:naming"
NEW_PROJECT = "Nouveau projet"


class ProjectCreate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    remote_text_consent: bool = False
    """Accueil (L2) : la case « Mieux comprendre mon texte avec Gemini » est cochée ; le projet
    reçoit l'accord, texte seul (jamais une photo), si la machine l'a bien retenu."""
    name_auto: bool = False
    """Accueil (L1a) : nom choisi par OpenPlan (« Projet · 6 oct. », puis « Grenier · 6 oct. »
    une fois la pièce connue) ; il reste renommable, et un nom donné à la main gagne."""


@router.post("/projects", status_code=201)
def create_project(
    body: ProjectCreate, store: StoreDep, settings: SettingsDep, request: Request
) -> Project:
    """Avec les comptes, l'utilisateur en devient propriétaire (`owner`)."""
    project = new_project(body.name or NEW_PROJECT).model_copy(update={"name_auto": False})
    if body.name_auto or body.name is None:
        taken = [s.name for s in visible_summaries(request, store)]
        project = project.model_copy(update={"name_auto": True})
        project = project.model_copy(update={"name": auto_name(project, taken)})
    if not settings.auto_path:
        project = project.model_copy(
            update={"render": project.render.model_copy(update={"auto_path": False})}
        )
    if body.remote_text_consent and home_settings.load(store.root).gemini_text:
        # Même accord que l'assistant, mais texte seul : la passerelle refuse toute image.
        privacy = project.privacy.model_copy(
            update={"allow_remote_models": True, "remote_consent": True, "remote_text_only": True}
        )
        project = project.model_copy(update={"privacy": privacy})
    register_project(request, project.id, project.name)
    store.save(project)
    return project


@router.get("/projects")
def list_projects(store: StoreDep, request: Request) -> list[ProjectSummary]:
    """Projets du plus récent au plus ancien (avec les comptes : ceux dont l'utilisateur est
    membre). Les anciens noms de fichier (« WhatsApp Image… ») deviennent « Grenier · 25 août »
    à l'affichage, une fois pour toutes. API exposée ou comptes actifs : sans le chemin du
    dossier sur le serveur (`path` vide), qui ne regarde que la machine locale."""
    summaries = visible_summaries(request, store)
    if not any(looks_like_file_name(s.name) for s in summaries):
        return _without_server_path(request, summaries)
    taken = [s.name for s in summaries]
    for summary in summaries:
        if not looks_like_file_name(summary.name):
            continue
        with store.lock(summary.id):
            try:
                project = store.load(summary.id)
            except (ProjectNotFoundError, ValueError):
                continue
            named = listed_name(project, taken)
            if named is None:
                continue
            store.save(named)
        if project.name in taken:
            taken[taken.index(project.name)] = named.name
        else:
            taken.append(named.name)
    return _without_server_path(request, visible_summaries(request, store))


def _without_server_path(request: Request, summaries: list[ProjectSummary]) -> list[ProjectSummary]:
    """Correctif a (intégration des comptes) : le chemin du dossier sur le serveur ne sort pas
    quand l'API est exposée ou que les comptes sont actifs."""
    settings = request.app.state.settings
    if settings.local_only and not settings.accounts_enabled:
        return summaries
    return [s.model_copy(update={"path": None}) for s in summaries]


@router.get("/projects/{project_id}")
def get_project(project: ProjectDep) -> Project:
    return project


class ProjectRole(StrictModel):
    role: str
    """viewer, commenter, editor ou owner (toujours owner sans comptes)."""


@router.get("/projects/{project_id}/role")
def project_role(project_id: UUID, request: Request) -> ProjectRole:
    """B1d : le rôle de l'utilisateur, pour masquer à l'écran ce qu'il ne peut pas faire (la
    vraie barrière reste la garde de chaque route)."""
    return ProjectRole(role=role_of(request, project_id))


@router.post("/projects/{project_id}/name/auto")
def auto_rename(project: ProjectDep, store: StoreDep, request: Request) -> Project:
    """À l'ouverture (L1a) : un nom automatique suit le type de la pièce ; un ancien nom
    encore tiré d'un fichier (« WhatsApp Image… ») devient « Grenier · 6 oct. ». Un nom donné
    à la main ne change jamais. Un pas d'historique attribué au pipeline, s'il y a lieu."""
    taken = [s.name for s in visible_summaries(request, store) if s.id != project.id]
    # Sous le verrou du projet : deux ouvertures simultanées ne renomment qu'une fois.
    with store.lock(project.id):
        current = store.load(project.id)
        after = rename(current, taken, actor=NAMING_ACTOR)
        if after is None:
            return current
        store.save(after)
    return after


STARTER_ACTOR = "pipeline:starter"


class StarterBody(StrictModel):
    area_m2: float = Field(ge=MIN_AREA_M2, le=MAX_AREA_M2)


@router.post("/projects/{project_id}/levels/{level_id}/starter")
def starter(level_id: UUID, body: StarterBody, project: ProjectDep, store: StoreDep) -> Project:
    """Accueil (L1b) : texte sans fichier mais avec une surface → une pièce rectangulaire
    4:3 de cette surface, murs `generated`, marquée « plan de départ simplifié », en un pas
    d'historique attribué au pipeline. 409 si le niveau a déjà des murs."""
    with store.lock(project.id):
        current = store.load(project.id)
        after = starter_plan(current, level_id, body.area_m2, actor=STARTER_ACTOR)
        store.save(after)
    return after


PROGRAMME_ACTOR = "pipeline:programme"


class ProgrammeBody(StrictModel):
    text: str = Field(default="", max_length=MAX_INSTRUCTION)
    answers: GuidedAnswers | None = None


class ProgrammeResult(StrictModel):
    """Accueil sans fichier (L3) : plan généré depuis le programme d'un logement, plan de départ
    simplifié pour une pièce seule avec sa surface, ou rien (la consigne est alors gardée)."""

    kind: Literal["generated", "starter", "none"]
    project: Project
    programme: Programme | None = None
    notes: list[str] = Field(default_factory=list)
    understanding: Literal["model", "phrases"] = "phrases"
    notice: str = ""


@router.post("/projects/{project_id}/levels/{level_id}/programme")
def programme_route(
    level_id: UUID,
    body: ProgrammeBody,
    project: ProjectDep,
    store: StoreDep,
    model: LanguageModelDep,
) -> ProgrammeResult:
    """Un logement décrit (« un T3 de 60 m², deux chambres… », ou les réponses guidées) →
    plan généré par découpage, en un pas d'historique attribué au pipeline ; une pièce seule
    avec sa surface → plan de départ simplifié (L1b) ; sinon rien. Le programme est lu par les
    règles, ou par le modèle distant si le projet a l'accord (case de l'accueil), texte seul."""
    privacy = project.privacy
    consented = model is not None and (
        not model.remote or (privacy.allow_remote_models and privacy.remote_consent)
    )
    reading = read_programme(
        body.text,
        body.answers,
        model=model if consented else None,
        gateway=ModelGateway(store.project_dir(project.id) / "network_log.jsonl"),
        project=project,
    )
    with store.lock(project.id):
        current = store.load(project.id)
        if reading.programme is not None:
            after, notes = generate_plan(
                current, level_id, reading.programme, actor=PROGRAMME_ACTOR
            )
            store.save(after)
            return ProgrammeResult(
                kind="generated",
                project=after,
                programme=reading.programme,
                notes=notes,
                understanding=reading.understanding,
                notice=reading.notice,
            )
        area = (body.answers.area_m2 if body.answers else None) or area_in_text(body.text)
        if area is not None:
            after = starter_plan(current, level_id, area, actor=STARTER_ACTOR)
            store.save(after)
            return ProgrammeResult(kind="starter", project=after)
    return ProgrammeResult(kind="none", project=current)


GUARD_ACTOR = "pipeline:area-guard"


class AreaGuardBody(StrictModel):
    area_m2: float = Field(ge=MIN_AREA_M2, le=MAX_AREA_M2)


@router.post("/projects/{project_id}/levels/{level_id}/area-guard")
def area_guard_route(
    level_id: UUID, body: AreaGuardBody, project: ProjectDep, store: StoreDep
) -> AreaGuard:
    """L2 : la surface donnée sur l'accueil recale le plan d'une photo d'une seule pièce
    quand l'écart dépasse 15 % (facteur borné de 0,6 à 1,6), en un pas du pipeline."""
    with store.lock(project.id):
        current = store.load(project.id)
        after, result = area_guard(current, level_id, body.area_m2, actor=GUARD_ACTOR)
        if result.applied:
            store.save(after)
    return result


@router.post("/projects/{project_id}/levels/{level_id}/area-guard/undo")
def area_guard_undo(level_id: UUID, project: ProjectDep, store: StoreDep) -> Project:
    """Annule le recalage en un clic (échelle inverse), même si d'autres pas ont suivi."""
    with store.lock(project.id):
        current = store.load(project.id)
        after = undo_area_guard(current, level_id, actor=USER_ACTOR)
        if after is None:
            return current
        store.save(after)
    return after


@router.get("/projects/{project_id}/thumbnail")
def project_thumbnail(project: ProjectDep, store: StoreDep) -> Response:
    """Vignette (première photo réduite) pour la liste des projets ; 404 sans photo."""
    path = store.thumbnail_path(project.id)
    if not path.is_file():
        msg = "ce projet n'a pas de vignette (aucune photo importée)"
        raise ImageNotFoundError(msg)
    return served_file(store, path, media_type="image/jpeg")


@router.get("/projects/{project_id}/cover")
def project_cover_image(
    project: ProjectDep, store: StoreDep, theme: Literal["light", "dark"] = "light"
) -> Response:
    """Vignette de la galerie de tableaux (T5) : le rendu retenu ou le plus récent (WebP
    480 px, en cache), sinon le plan en SVG, sinon la première photo, sinon une carte
    « Exigences ». Toujours une image : jamais de 404 pour un projet existant."""
    cover = project_cover(
        project,
        store.project_dir(project.id),
        store.runs_dir(project.id),
        store.thumbnail_path(project.id),
        theme,
    )
    headers = {"Cache-Control": "private, max-age=60", "X-Cover-Kind": cover.kind}
    if cover.path is not None:
        return served_file(store, cover.path, media_type=cover.media_type, headers=headers)
    return Response(content=cover.content or b"", media_type=cover.media_type, headers=headers)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: UUID, store: StoreDep, request: Request) -> Response:
    """Supprime la scène, les images et les runs : rien ne subsiste sur disque ; avec les
    comptes, la ligne du projet et ses membres aussi (propriétaire seulement)."""
    try:
        store.delete(project_id)
    finally:
        forget_project(request, project_id)
    return Response(status_code=204)
