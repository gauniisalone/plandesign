"""Tableau du projet (B1) et fichiers joints : la disposition se lit et s'enregistre à part de
la géométrie ; un fichier joint est gardé tel quel et servi en pièce jointe (sauf les images
courantes, montrées)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile

from openplan.api import change_events
from openplan.api.access import accounts_of, usage_event
from openplan.api.board_rules import board_now
from openplan.api.change_events import author_of
from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.api.files import served_file
from openplan.api.guards import role_of
from openplan.pipeline import board as b
from openplan.pipeline import board_links as bl
from openplan.pipeline.board import MediaError
from openplan.pipeline.board_views import ViewStore, bounded
from openplan.schemas.board import Board, BoardPatch, BoardView, MediaRef
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["board"])


class ViewIn(StrictModel):
    """KAN-43 : une vue reçue, bornée avant d'être gardée (`board_views.bounded`)."""

    x: float
    y: float
    zoom: float


def _mine(request: Request, actor: str, project_dir: Path, board: Board) -> Board:
    """KAN-43 : le tableau tel que je le vois, avec MA vue. Sans vue enregistrée : le
    propriétaire reprend l'ancienne vue commune (une seule fois, tant qu'il n'en a pas
    enregistré une) ; les autres, le cadrage de départ (vue nulle : le front cadre tout)."""
    view = ViewStore(project_dir).get(author_of(actor))
    if view is None:
        owner = accounts_of(request) is None or role_of(request, board_project(request)) == "owner"
        view = bounded(board.view) if owner and board.view != BoardView() else BoardView()
    return board.model_copy(update={"view": view})


def board_project(request: Request) -> UUID:
    return UUID(request.path_params["project_id"])


@router.get("/projects/{project_id}/board")
def get_board(project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request) -> Board:
    """Le tableau, à jour de ce que le projet contient (mise en page de départ la première
    fois ; photos, rendus, plan et demande ajoutés ensuite dans leur cadre), avec ma vue."""
    project_dir = store.project_dir(project.id)
    with store.lock(project.id):
        board, changed = b.sync_board(
            b.load_board(project_dir),
            project,
            store.runs_dir(project.id),
            b.list_media(project_dir),
        )
        if changed:
            board = b.save_board(project_dir, board)
        return _mine(request, actor, project_dir, board)


@router.get("/projects/{project_id}/view")
def get_view(project: ProjectDep, store: StoreDep, actor: ActorDep) -> BoardView | None:
    """KAN-43 : ma vue de l'espace de travail (null : jamais enregistrée)."""
    return ViewStore(store.project_dir(project.id)).get(author_of(actor))


@router.put("/projects/{project_id}/view")
def put_view(
    body: ViewIn, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> BoardView:
    """KAN-43 : enregistre ma vue, et seulement la mienne (tout membre, lecteur compris). Pas
    une modification : ni journal, ni cloche, ni avis aux autres."""
    request.state.quiet = True
    raw = BoardView.model_construct(x=body.x, y=body.y, zoom=body.zoom)
    with store.lock(project.id):
        return ViewStore(store.project_dir(project.id)).set(author_of(actor), raw)


@router.put("/projects/{project_id}/board")
def put_board(
    body: Board, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> Board:
    """Enregistre la disposition (positions, tailles, ordre, verrous, cadres, notes, vue). Ne
    touche jamais la géométrie du plan. Ce qui a été retiré parmi ce que le projet fournit
    n'est pas remis ensuite. 409 si l'envoi part d'une révision périmée (le tableau a été
    enregistré ailleurs entre-temps, deux onglets) : rien n'est écrasé, l'atelier recharge.

    Depuis B2c, compatibilité seulement (onglets ouverts sur une version d'avant B2c) : le
    front écrit par `PATCH …/board/items`. Gardé jusqu'au déploiement de B2d, et au moins
    jusqu'au 2 novembre 2026."""
    project_dir = store.project_dir(project.id)
    frames = {i.id for i in body.items if i.kind == "frame"}
    items = [
        i if i.frame is None or i.frame in frames else i.model_copy(update={"frame": None})
        for i in body.items
    ]
    with store.lock(project.id):
        before = b.load_board(project_dir)
        # KAN-43 : la vue envoyée est la mienne (gardée seulement si l'envoi passe) ; la vue
        # commune ne bouge plus.
        kept = before.view if before is not None else BoardView()
        after = b.replaced(before, body.model_copy(update={"items": items, "view": kept}))
        try:
            saved = b.save_board(project_dir, after, expect=body.revision)
        except b.StaleBoardError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        ViewStore(project_dir).set(author_of(actor), body.view)
        _journal(request, project_dir, project.id, actor, before, saved)
        return _mine(request, actor, project_dir, saved)


def _journal(
    request: Request,
    project_dir: Path,
    project_id: UUID,
    actor: str,
    before: Board | None,
    after: Board,
) -> None:
    """KAN-58 : le geste entre au journal avec son détail (rien pour une vue seule) ; les
    cloches des autres membres sonnent ensuite (intergiciel des comptes)."""
    if change_events.record_board(project_dir, project_id, author_of(actor), before, after):
        request.state.journaled = True


@router.patch("/projects/{project_id}/board/items")
def patch_board_items(
    body: BoardPatch, project: ProjectDep, store: StoreDep, actor: ActorDep, request: Request
) -> Board:
    """B2c : les écritures d'un geste (éléments posés ou modifiés, éléments retirés, vue), en
    une fois sous le verrou du projet. La dernière écriture reçue gagne, élément par élément :
    deux personnes qui déplacent deux cartes différentes ne se gênent plus, et rien n'est
    refusé en 409. Le serveur horodate chaque élément écrit et note son acteur."""
    project_dir = store.project_dir(project.id)
    with store.lock(project.id):
        before = b.load_board(project_dir)
        # KAN-43 : la vue envoyée (onglets d'avant KAN-43) est la mienne, pas celle du projet.
        if body.view is not None:
            ViewStore(project_dir).set(author_of(actor), body.view)
        if not body.set and not body.delete and not body.links_set and not body.links_delete:
            # Une vue seule : rien n'est écrit dans le tableau, personne n'est prévenu.
            request.state.quiet = True
            current = before if before is not None else Board()
            return _mine(request, actor, project_dir, current)
        try:
            after = b.patched(before, body.model_copy(update={"view": None}), actor)
        except b.TooManyItemsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except bl.LinkError as exc:
            # KAN-91 : lien entre cartes qui ne se relient pas (422), deuxième plan relié (409),
            # carte qui n'accepte pas cet élément (422) ; rien n'est écrit.
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
        saved = b.save_board(project_dir, after)
        _journal(request, project_dir, project.id, actor, before, saved)
        # KAN-109 L2 : un Moodboard relié à la main à un node Rendus (un lien nouveau).
        had = {(k.source, k.target) for k in (before.links if before else [])}
        for link in saved.links:
            if (
                link.kind == "guides_style"
                and not link.auto
                and (link.source, link.target) not in had
            ):
                usage_event(request, "moodboard_linked", project.id)
        return _mine(request, actor, project_dir, saved)


class InspirationOut(StrictModel):
    item: UUID
    kind: str
    ref: str | None
    card: UUID
    ignored_tags: list[str]


class GenerationInputsOut(StrictModel):
    """KAN-91 : ce que lit la génération d'une carte (C1), tel que le serveur le lira."""

    target: UUID
    kind: str
    photos: list[str]
    photo_card: UUID | None
    plan_level: str | None
    plan_item: UUID | None
    inspirations: list[InspirationOut]
    inspiration_cards: list[UUID]
    can_render: bool
    reason: str | None
    """Pourquoi une carte Rendus ne rend pas (« Reliez un plan pour rendre », D2)."""


class LinkSummary(StrictModel):
    """KAN-91 : la bulle de survol d'un lien."""

    id: UUID
    kind: str
    sentence: str
    """Ce que le lien fait, recalculé depuis les membres (`generation_inputs`)."""
    auto: bool
    updated_by: str | None
    rights: str


def inputs_out(board: Board, target_id: UUID) -> GenerationInputsOut:
    found = bl.generation_inputs(board, target_id)
    return GenerationInputsOut(
        target=found.target,
        kind=found.kind,
        photos=found.photos,
        photo_card=found.photo_card,
        plan_level=found.plan_level,
        plan_item=found.plan_item,
        inspirations=[
            InspirationOut(
                item=i.item, kind=i.kind, ref=i.ref, card=i.card, ignored_tags=list(i.ignored_tags)
            )
            for i in found.inspirations
        ],
        inspiration_cards=found.inspiration_cards,
        can_render=found.can_render,
        reason=bl.NO_PLAN if found.kind == "renders" and not found.can_render else None,
    )


@router.get("/projects/{project_id}/board/inputs/{target_id}")
def board_inputs(target_id: UUID, project: ProjectDep, store: StoreDep) -> GenerationInputsOut:
    """KAN-91 : ce que lit la génération d'un plan (ses photos) ou d'une carte Rendus (son plan,
    ses inspirations). 404 si la carte n'existe pas."""
    board = board_now(project, store)
    if not any(i.id == target_id for i in board.items):
        raise HTTPException(status_code=404, detail="carte inconnue")
    return inputs_out(board, target_id)


@router.get("/projects/{project_id}/board/links/{link_id}")
def board_link(link_id: UUID, project: ProjectDep, store: StoreDep) -> LinkSummary:
    """KAN-91 : la bulle de survol d'un lien, tirée de ce que la génération lira vraiment."""
    board = board_now(project, store)
    link = next((k for k in board.links if k.id == link_id), None)
    if link is None:
        raise HTTPException(status_code=404, detail="lien inconnu")
    return LinkSummary(
        id=link.id,
        kind=link.kind,
        sentence=bl.link_sentence(board, link),
        auto=link.auto,
        updated_by=link.updated_by,
        rights="modifiable par tous les éditeurs du projet",
    )


@router.get("/projects/{project_id}/exports/archive")
def export_archive(project: ProjectDep, store: StoreDep) -> Response:
    """Sauvegarde complète du projet en un zip : le projet, le tableau, les photos, les
    fichiers joints et les images et réglages des rendus (pas les fichiers de travail)."""
    with store.lock(project.id):
        data = b.project_archive(store.project_dir(project.id))
    day = datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="projet-{day}.zip"'},
    )


@router.get("/projects/{project_id}/media")
def media_list(project: ProjectDep, store: StoreDep) -> list[MediaRef]:
    return b.list_media(store.project_dir(project.id))


@router.post("/projects/{project_id}/media", status_code=201)
async def media_upload(
    file: UploadFile, project: ProjectDep, store: StoreDep, placed: bool = False
) -> MediaRef:
    """Joint un fichier au projet, gardé sur cette machine : une vidéo (mp4, webm) jusqu'à
    200 Mo, tout autre fichier jusqu'à 50 Mo ; un PDF reçoit la vignette de sa première page.
    `placed` : l'espace de travail pose lui-même sa carte (la synchronisation n'en fait pas
    de copie dans « Fichiers »)."""
    data = await file.read(b.MAX_VIDEO_BYTES + 1)
    try:
        with store.lock(project.id):
            return b.add_media(
                store.project_dir(project.id), data, file.filename or "fichier", placed=placed
            )
    except MediaError as exc:
        too_big = len(data) > b.max_bytes(b.media_kind_of(b.media_type_of(data[:64])))
        raise HTTPException(status_code=413 if too_big else 400, detail=str(exc)) from exc


@router.get("/projects/{project_id}/media/{media_id}/thumb")
def media_thumb(media_id: UUID, project: ProjectDep, store: StoreDep) -> Response:
    """Vignette d'un fichier joint (première page d'un PDF), rendue sur la machine."""
    try:
        path = b.media_thumb(store.project_dir(project.id), media_id)
    except MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return served_file(
        store, path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"}
    )


@router.get("/projects/{project_id}/media/{media_id}/file")
def media_download(media_id: UUID, project: ProjectDep, store: StoreDep) -> Response:
    """Une image courante, une vidéo (mp4, webm) ou un PDF, reconnus à leurs premiers octets,
    sont montrés ; tout autre fichier part en pièce jointe, comme des octets, sans que le
    navigateur l'interprète."""
    try:
        ref, path = b.media_file(store.project_dir(project.id), media_id)
    except MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    inline = ref.media_type != "application/octet-stream"
    return served_file(
        store,
        path,
        media_type=ref.media_type,
        filename=ref.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


@router.delete("/projects/{project_id}/media/{media_id}", status_code=204)
def media_delete(media_id: UUID, project: ProjectDep, store: StoreDep) -> None:
    try:
        with store.lock(project.id):
            b.remove_media(store.project_dir(project.id), media_id)
    except MediaError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
