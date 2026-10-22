"""Routes des rendus réalistes calculés par le poste d'augus (KAN-89), avec les comptes seulement.

Côté personne (session) : réservé aux comptes de OPENPLAN_RENDER_ACCOUNTS ; les autres
reçoivent 403 (« Rendus réalistes : bientôt en ligne »), même membres du projet.
Côté poste (`/api/worker/*`, jeton du poste) : prendre une demande, télécharger le projet, le
bake et la caméra de la vue (JSON validé, taille plafonnée), déposer l'image, signaler un
échec, battre. Le serveur n'envoie jamais de commande ni de chemin.
"""

from __future__ import annotations

import contextlib
import json
import os
import struct
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Body,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from PIL import Image
from pydantic import ValidationError

from openplan.api.access import AuthUser, accounts_of, signed_in_user
from openplan.api.accounts.models import RenderRequest as RenderRow
from openplan.api.accounts.quota import LAUNCH_RESERVE, full, quota_bytes, used_bytes
from openplan.api.accounts.render_queue import (
    LeaseError,
    QuotaExceededError,
    RenderQueue,
    public_request,
)
from openplan.api.board_rules import render_inputs
from openplan.api.deps import ActorDep
from openplan.api.files import served_file
from openplan.api.routes.comments import YOU, AuthorNames, author_of
from openplan.pipeline.cover import CHOSEN_COVER, COVER_QUALITY, COVER_WIDTH
from openplan.pipeline.events import ProjectEvents
from openplan.pipeline.reactions import ReactionStore
from openplan.pipeline.render import NoCameraError, camera_view
from openplan.pipeline.scene3d import bake_level
from openplan.scene.store import ProjectNotFoundError, ProjectStore
from openplan.schemas.elements import Camera
from openplan.schemas.events import ProjectEvent
from openplan.schemas.reactions import EMOJI, Reaction, ReactionKey
from openplan.schemas.render_queue import (
    JSON_FILES,
    MAX_IMAGE_BYTES,
    RealisticCover,
    RealisticCreate,
    RealisticFromPhoto,
    RealisticList,
    RealisticReaction,
    RealisticRequest,
    RealisticStatus,
    WorkerClaim,
    WorkerFailure,
    WorkerJob,
    WorkerResult,
)
from openplan.schemas.scene import Project

router = APIRouter(tags=["rendus réalistes"])

P = "/api/projects/{project_id}"
L = P + "/levels/{level_id}"
NOT_YET = "Rendus réalistes : bientôt en ligne"
QUOTA_TEXT = "Vous avez atteint le nombre de rendus réalistes du jour : réessayez demain."
LEASE_REFUSED = "demande inconnue, ou plus à ce poste"
IMAGES_DIR = "realistic"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
NO_PHOTO_VIEW = "Cette photo n'a pas encore de point de vue estimé : analysez-la d'abord."
COVER_META = Path(IMAGES_DIR) / "cover.json"
"""KAN-91 : quel rendu est la couverture (à côté de sa réduction, `CHOSEN_COVER`)."""
RENDER_LABEL = "Rendu réaliste"


def queue_of(request: Request) -> RenderQueue:
    queue: RenderQueue = request.app.state.render_queue
    return queue


def store_of(request: Request) -> ProjectStore:
    store: ProjectStore = request.app.state.store
    return store


def allowed_user(request: Request) -> AuthUser:
    user = signed_in_user(request)
    if not queue_of(request).config.allows(user.id):
        raise HTTPException(status_code=403, detail=NOT_YET)
    return user


def image_path(store: ProjectStore, project_id: UUID, request_id: UUID) -> Path:
    return store.project_dir(project_id) / IMAGES_DIR / f"{request_id}.png"


def _status(request: Request, user: AuthUser) -> RealisticStatus:
    queue = queue_of(request)
    if not queue.config.allows(user.id):
        return RealisticStatus(allowed=False)
    return RealisticStatus(
        allowed=True,
        worker=queue.worker_status(),  # type: ignore[arg-type]
        quota=queue.config.daily_quota,
        used_today=queue.used_today(user.id),
    )


# --- côté personne ----------------------------------------------------------------------


@router.get("/api/realistic/status")
def realistic_status(request: Request) -> RealisticStatus:
    """Pour tout compte : autorisé ou non ; pour un compte autorisé, l'état du poste."""
    return _status(request, signed_in_user(request))


def photo_cameras(project: Project) -> dict[UUID, UUID]:
    """KAN-91 (D5) : caméra → photo, pour les caméras estimées d'une photo encore présente."""
    return {
        element.id: element.image_id
        for level in project.levels.values()
        for element in level.elements.values()
        if isinstance(element, Camera)
        and element.image_id is not None
        and element.image_id in project.images
    }


def _load(store: ProjectStore, project_id: UUID) -> Project:
    try:
        return store.load(project_id)
    except ProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="projet introuvable") from exc


def _cover_id(store: ProjectStore, project_id: UUID) -> UUID | None:
    meta = store.project_dir(project_id) / COVER_META
    if not (store.project_dir(project_id) / CHOSEN_COVER).is_file():
        return None
    try:
        return UUID(json.loads(meta.read_text(encoding="utf-8"))["request_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _reactions(
    request: Request, project_id: UUID, me: str, ids: set[UUID]
) -> list[RealisticReaction]:
    """Les pastilles (KAN-78) des rendus `ids`, dans l'ordre des six emojis."""
    store = store_of(request)
    names = AuthorNames(request, project_id, me)
    order = list(EMOJI)
    grouped: dict[tuple[UUID, str], list[Reaction]] = {}
    for r in ReactionStore(store.project_dir(project_id)).load().reactions:
        if r.item_id in ids:
            grouped.setdefault((r.item_id, r.key), []).append(r)
    out = []
    for (item_id, key), group in sorted(
        grouped.items(), key=lambda kv: (str(kv[0][0]), order.index(kv[0][1]))
    ):
        group.sort(key=lambda r: r.at)
        mine = any(r.author == me for r in group)
        who = [names(r.author)[0] for r in group if r.author != me]
        out.append(
            RealisticReaction(
                item_id=item_id,
                key=key,
                emoji=EMOJI[key],
                count=len(group),
                mine=mine,
                names=([YOU] if mine else []) + who,
            )
        )
    return out


def _own_done(request: Request, user: AuthUser, project_id: UUID, request_id: UUID) -> RenderRow:
    """Un rendu fait de cette personne dans ce projet, avec son image ; 404 sinon."""
    row = queue_of(request).get(request_id)
    store = store_of(request)
    if (
        row is None
        or row.project_id != project_id
        or row.user_id != user.id
        or row.state != "done"
        or not image_path(store, project_id, request_id).is_file()
    ):
        raise HTTPException(status_code=404, detail="rendu introuvable")
    return row


@router.get(P + "/realistic")
def list_realistic(project_id: UUID, request: Request, actor: ActorDep) -> RealisticList:
    user = allowed_user(request)
    store = store_of(request)
    queue = queue_of(request)
    rows = queue.list_for(user.id, project_id)
    try:
        photos = photo_cameras(store.load(project_id))
    except ProjectNotFoundError:
        photos = {}
    return RealisticList(
        status=_status(request, user),
        requests=[
            public_request(
                r, image_path(store, project_id, r.id).is_file(), photos.get(r.camera_id)
            )
            for r in rows
        ],
        typical_seconds=queue.typical_seconds(),
        cover_id=_cover_id(store, project_id),
        reactions=_reactions(
            request, project_id, author_of(actor), {r.id for r in rows if r.state == "done"}
        ),
    )


@router.post(L + "/realistic", status_code=201)
def create_realistic(
    project_id: UUID, level_id: UUID, body: RealisticCreate, request: Request, response: Response
) -> RealisticRequest:
    """Une demande pour une vue, stockée sans calcul ; la même vue déjà en attente ou en cours
    est rendue telle quelle (double clic)."""
    user = allowed_user(request)
    return _create(request, response, user, project_id, level_id, body.camera_id)


@router.post(L + "/realistic/from-photo", status_code=201)
def create_realistic_from_photo(
    project_id: UUID,
    level_id: UUID,
    body: RealisticFromPhoto,
    request: Request,
    response: Response,
) -> RealisticRequest:
    """KAN-91 (D5) : « Rendre depuis la photo ». La vue de la demande est la caméra estimée de
    la photo (KAN-66, KAN-79), à la taille de la photo : l'avant/après avec elle est fidèle."""
    user = allowed_user(request)
    project = _load(store_of(request), project_id)
    level = project.levels.get(level_id)
    if level is None or body.image_id not in project.images:
        raise HTTPException(status_code=404, detail="photo inconnue")
    cameras = [
        e for e in level.elements.values() if isinstance(e, Camera) and e.image_id == body.image_id
    ]
    if not cameras:
        raise HTTPException(status_code=409, detail=NO_PHOTO_VIEW)
    camera = next((c for c in cameras if c.visible), cameras[0])
    return _create(request, response, user, project_id, level_id, camera.id, project=project)


@router.post(P + "/realistic/{request_id}/variant", status_code=201)
def create_realistic_variant(
    project_id: UUID, request_id: UUID, request: Request, response: Response
) -> RealisticRequest:
    """KAN-91 : « Nouvelle variante » d'un rendu fait : même vue, autre tirage (le poste reçoit
    un tirage tiré de la nouvelle demande)."""
    user = allowed_user(request)
    row = _own_done(request, user, project_id, request_id)
    return _create(request, response, user, project_id, row.level_id, row.camera_id)


@router.put(P + "/realistic/{request_id}/cover")
def set_realistic_cover(project_id: UUID, request_id: UUID, request: Request) -> RealisticCover:
    """KAN-91 : ce rendu devient la couverture du projet, celle de la liste « Mes projets »
    (réduction WebP de 480 px, pour tous les membres). Le projet est marqué modifié : la liste
    relit sa vignette."""
    user = allowed_user(request)
    _own_done(request, user, project_id, request_id)
    store = store_of(request)
    project_dir = store.project_dir(project_id)
    source = image_path(store, project_id, request_id)
    target = project_dir / CHOSEN_COVER
    partial = target.with_suffix(".part")
    with store.lock(project_id):
        project = _load(store, project_id)
        try:
            with Image.open(source) as img:
                rgb = img.convert("RGB")
                height = max(1, round(rgb.height * COVER_WIDTH / rgb.width))
                rgb.resize((COVER_WIDTH, height), Image.Resampling.LANCZOS).save(
                    partial, format="WEBP", quality=COVER_QUALITY
                )
        except (OSError, ValueError) as exc:
            with contextlib.suppress(OSError):
                partial.unlink()
            raise HTTPException(status_code=422, detail="image du rendu illisible") from exc
        os.replace(partial, target)
        meta = project_dir / COVER_META
        meta.write_text(json.dumps({"request_id": str(request_id)}), encoding="utf-8")
        store.save(project)
    return RealisticCover(cover_id=request_id)


@router.delete(P + "/realistic/cover")
def clear_realistic_cover(project_id: UUID, request: Request) -> RealisticCover:
    """KAN-91 : la couverture revient au choix automatique (rendu, plan, photo, exigences)."""
    allowed_user(request)
    store = store_of(request)
    project_dir = store.project_dir(project_id)
    with store.lock(project_id):
        project = _load(store, project_id)
        if (project_dir / CHOSEN_COVER).is_file():
            for path in (project_dir / CHOSEN_COVER, project_dir / COVER_META):
                with contextlib.suppress(OSError):
                    path.unlink()
            store.save(project)
    return RealisticCover(cover_id=None)


@router.put(P + "/realistic/{request_id}/reactions/{key}")
def add_realistic_reaction(
    project_id: UUID, request_id: UUID, key: ReactionKey, request: Request, actor: ActorDep
) -> list[RealisticReaction]:
    """KAN-91 : réagir à un rendu réaliste, avec les réactions de KAN-78 (même fichier, même
    journal : la cloche prévient le lanceur du rendu, jamais celui qui réagit)."""
    user = allowed_user(request)
    row = _own_done(request, user, project_id, request_id)
    me = author_of(actor)
    store = store_of(request)
    project_dir = store.project_dir(project_id)
    reactions = ReactionStore(project_dir)
    with store.lock(project_id):
        data = reactions.load()
        if not any(
            r.item_id == request_id and r.key == key and r.author == me for r in data.reactions
        ):
            data.reactions.append(Reaction(item_id=request_id, key=key, author=me))
            reactions.save(data)
            ProjectEvents(project_dir).append(
                ProjectEvent(
                    type="reaction.added",
                    project_id=project_id,
                    actor=me,
                    item_id=request_id,
                    item_kind="render",
                    label=RENDER_LABEL,
                    emoji=EMOJI[key],
                    item_author=str(row.user_id),
                )
            )
    return _reactions(request, project_id, me, {request_id})


@router.delete(P + "/realistic/{request_id}/reactions/{key}")
def remove_realistic_reaction(
    project_id: UUID, request_id: UUID, key: ReactionKey, request: Request, actor: ActorDep
) -> list[RealisticReaction]:
    """Retire ma réaction (seulement la mienne)."""
    user = allowed_user(request)
    _own_done(request, user, project_id, request_id)
    me = author_of(actor)
    store = store_of(request)
    reactions = ReactionStore(store.project_dir(project_id))
    with store.lock(project_id):
        data = reactions.load()
        kept = [
            r
            for r in data.reactions
            if not (r.item_id == request_id and r.key == key and r.author == me)
        ]
        if len(kept) != len(data.reactions):
            data.reactions = kept
            reactions.save(data)
    return _reactions(request, project_id, me, {request_id})


def _create(
    request: Request,
    response: Response,
    user: AuthUser,
    project_id: UUID,
    level_id: UUID,
    camera_id: UUID,
    *,
    project: Project | None = None,
) -> RealisticRequest:
    """La demande d'une vue (création, D5, variante) : vue connue, plan présent (D2), la même
    vue déjà en attente ou en cours rendue telle quelle (double clic), place et quota."""
    store = store_of(request)
    project = project or _load(store, project_id)
    try:
        camera_view(project, level_id, camera_id)
    except NoCameraError as exc:
        raise HTTPException(status_code=404, detail="vue inconnue") from exc
    # KAN-91 (D2) : la carte Rendus de ce niveau doit avoir un plan relié, sinon 409
    # « Reliez un plan pour rendre ». Ses inspirations (C1) remplacent les images cochées
    # « Référence ».
    render_inputs(project, store, level_id)
    photo = photo_cameras(project).get(camera_id)
    queue = queue_of(request)
    for row in queue.list_for(user.id, project_id):
        if (row.level_id, row.camera_id) == (level_id, camera_id) and row.state in (
            "pending",
            "taken",
        ):
            response.status_code = 200
            return public_request(row, False, photo)
    accounts = accounts_of(request)
    limit = quota_bytes(request)
    payer = None if accounts is None else accounts.payer_of(project_id)
    if accounts is not None and limit is not None and payer is not None:
        # KAN-12 : l'image déposée plus tard compte dans la place du propriétaire.
        used = used_bytes(request, accounts, payer)
        if used + min(LAUNCH_RESERVE, limit) > limit:
            raise full(user, payer, used, limit)
    try:
        row = queue.create(user.id, project_id, level_id, camera_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=QUOTA_TEXT) from exc
    return public_request(row, False, photo)


@router.get(P + "/realistic/{request_id}/image")
def realistic_image(project_id: UUID, request_id: UUID, request: Request) -> Response:
    user = allowed_user(request)
    row = queue_of(request).get(request_id)
    if row is None or row.project_id != project_id or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="rendu introuvable")
    store = store_of(request)
    return served_file(
        store,
        image_path(store, project_id, request_id),
        media_type="image/png",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


# --- côté poste -------------------------------------------------------------------------

LeaseHeader = Annotated[UUID | None, Header(alias="X-Render-Lease")]


@router.post("/api/worker/claim", response_model=None)
def worker_claim(
    request: Request, body: Annotated[WorkerClaim | None, Body()] = None
) -> WorkerJob | Response:
    """L'appel périodique du poste, qui vaut battement de cœur : la plus ancienne demande en
    attente (prise atomique) si le poste est libre (corps absent = libre), sinon 204."""
    state = "idle" if body is None else body.state
    queue = queue_of(request)
    queue.beat(state)
    job = queue.claim() if state == "idle" else None
    if job is None:
        return Response(status_code=204)
    queue.beat("busy")
    # KAN-91 : `seed` n'est envoyé que pour une variante (un premier rendu garde le message de
    # KAN-89, qu'un poste d'avant KAN-91 lit toujours).
    return Response(content=job.model_dump_json(exclude_none=True), media_type="application/json")


@router.get("/api/worker/jobs/{request_id}/files/{name}")
def worker_file(
    request_id: UUID,
    name: Literal["project", "bake", "camera"],
    request: Request,
    lease: LeaseHeader = None,
) -> Response:
    """Le projet, le bake (style réaliste) ou la caméra de la vue, en JSON validé par leur
    schéma, sous leur plafond de taille."""
    try:
        row = queue_of(request).leased(request_id, lease)
    except LeaseError as exc:
        raise HTTPException(status_code=404, detail=LEASE_REFUSED) from exc
    try:
        project = store_of(request).load(row.project_id)
        if name == "project":
            payload = project.model_dump_json()
        elif name == "bake":
            payload = bake_level(project, row.level_id, "realistic").model_dump_json()
        else:
            payload = camera_view(project, row.level_id, row.camera_id)[1].model_dump_json()
    except (ProjectNotFoundError, NoCameraError, KeyError) as exc:
        raise HTTPException(status_code=410, detail="la vue n'existe plus") from exc
    data = payload.encode()
    if len(data) > JSON_FILES[name]:
        raise HTTPException(status_code=413, detail="fichier trop gros pour le poste")
    return Response(content=data, media_type="application/json")


def png_size(head: bytes) -> tuple[int, int] | None:
    """Largeur et hauteur lues dans l'en-tête IHDR d'un PNG ; None si ce n'en est pas un."""
    if len(head) < 24 or not head.startswith(PNG_SIGNATURE) or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


@router.post("/api/worker/jobs/{request_id}/result")
async def worker_result(
    request_id: UUID,
    request: Request,
    meta: Annotated[str, Form(max_length=2000)],
    image: Annotated[UploadFile, File()],
    lease: LeaseHeader = None,
) -> RealisticRequest:
    """Dépôt de l'image (PNG, plafond MAX_IMAGE_BYTES, taille conforme à `meta`)."""
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        length = 0
    if length > MAX_IMAGE_BYTES + 64 * 1024:
        raise HTTPException(status_code=413, detail="image trop grosse")
    try:
        result = WorkerResult.model_validate_json(meta)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="résultat invalide") from exc
    queue = queue_of(request)
    try:
        row = queue.leased(request_id, lease)
    except LeaseError as exc:
        raise HTTPException(status_code=404, detail=LEASE_REFUSED) from exc
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="image trop grosse")
    if png_size(data[:24]) != (result.width, result.height):
        raise HTTPException(status_code=422, detail="image PNG attendue, à la taille annoncée")
    store = store_of(request)
    target = image_path(store, row.project_id, request_id)
    if not store.project_dir(row.project_id).is_dir():
        raise HTTPException(status_code=410, detail="la vue n'existe plus")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    partial.write_bytes(data)
    os.replace(partial, target)
    try:
        done = queue.complete(request_id, lease, result)  # type: ignore[arg-type]
    except LeaseError as exc:
        with contextlib.suppress(OSError):
            target.unlink()
        raise HTTPException(status_code=404, detail=LEASE_REFUSED) from exc
    queue.beat("idle")
    _announce_done(request, done)
    return public_request(done, True)


def _announce_done(request: Request, row: RenderRow) -> None:
    """KAN-91 : un rendu réaliste fait entre au journal du projet (`render.done`, au nom de son
    lanceur) : la cloche (KAN-52) dit « Votre rendu est prêt » et sonne pour les membres à
    l'écoute. Un échec ici ne perd jamais le rendu déposé."""
    store = store_of(request)
    try:
        ProjectEvents(store.project_dir(row.project_id)).append(
            ProjectEvent(
                type="render.done",
                project_id=row.project_id,
                actor=str(row.user_id),
                level_id=row.level_id,
                item_kind="render",
                label=RENDER_LABEL,
            )
        )
    except (OSError, ValueError):
        return
    with contextlib.suppress(Exception):
        from openplan.api.accounts.live import ring_project_from_thread

        ring_project_from_thread(request.app, row.project_id)


@router.post("/api/worker/jobs/{request_id}/fail", status_code=204)
def worker_fail(
    request_id: UUID, body: WorkerFailure, request: Request, lease: LeaseHeader = None
) -> Response:
    queue = queue_of(request)
    try:
        if lease is None:
            raise LeaseError
        queue.fail(request_id, lease, body.reason)
    except LeaseError as exc:
        raise HTTPException(status_code=404, detail=LEASE_REFUSED) from exc
    queue.beat("idle")
    return Response(status_code=204)
