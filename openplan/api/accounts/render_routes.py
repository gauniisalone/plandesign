"""Routes des rendus réalistes calculés par le poste d'augus (KAN-89), avec les comptes seulement.

Côté personne (session) : réservé aux comptes de OPENPLAN_RENDER_ACCOUNTS ; les autres
reçoivent 403 (« Rendus réalistes : bientôt en ligne »), même membres du projet.
Côté poste (`/api/worker/*`, jeton du poste) : prendre une demande, télécharger le projet, le
bake et la caméra de la vue (JSON validé, taille plafonnée), déposer l'image, signaler un
échec, battre. Le serveur n'envoie jamais de commande ni de chemin.
"""

from __future__ import annotations

import contextlib
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
from pydantic import ValidationError

from openplan.api.access import AuthUser, accounts_of, signed_in_user
from openplan.api.accounts.quota import LAUNCH_RESERVE, full, quota_bytes, used_bytes
from openplan.api.accounts.render_queue import (
    LeaseError,
    QuotaExceededError,
    RenderQueue,
    public_request,
)
from openplan.api.board_rules import render_inputs
from openplan.api.files import served_file
from openplan.pipeline.render import NoCameraError, camera_view
from openplan.pipeline.scene3d import bake_level
from openplan.scene.store import ProjectNotFoundError, ProjectStore
from openplan.schemas.render_queue import (
    JSON_FILES,
    MAX_IMAGE_BYTES,
    RealisticCreate,
    RealisticList,
    RealisticRequest,
    RealisticStatus,
    WorkerClaim,
    WorkerFailure,
    WorkerJob,
    WorkerResult,
)

router = APIRouter(tags=["rendus réalistes"])

P = "/api/projects/{project_id}"
L = P + "/levels/{level_id}"
NOT_YET = "Rendus réalistes : bientôt en ligne"
QUOTA_TEXT = "Vous avez atteint le nombre de rendus réalistes du jour : réessayez demain."
LEASE_REFUSED = "demande inconnue, ou plus à ce poste"
IMAGES_DIR = "realistic"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


@router.get(P + "/realistic")
def list_realistic(project_id: UUID, request: Request) -> RealisticList:
    user = allowed_user(request)
    store = store_of(request)
    rows = queue_of(request).list_for(user.id, project_id)
    return RealisticList(
        status=_status(request, user),
        requests=[public_request(r, image_path(store, project_id, r.id).is_file()) for r in rows],
    )


@router.post(L + "/realistic", status_code=201)
def create_realistic(
    project_id: UUID, level_id: UUID, body: RealisticCreate, request: Request, response: Response
) -> RealisticRequest:
    """Une demande pour une vue, stockée sans calcul ; la même vue déjà en attente ou en cours
    est rendue telle quelle (double clic)."""
    user = allowed_user(request)
    store = store_of(request)
    project = store.load(project_id)
    try:
        camera_view(project, level_id, body.camera_id)
    except NoCameraError as exc:
        raise HTTPException(status_code=404, detail="vue inconnue") from exc
    # KAN-91 (D2) : la carte Rendus de ce niveau doit avoir un plan relié, sinon 409 « Reliez un
    # plan pour rendre ». Ses inspirations (C1) remplacent les images cochées « Référence ».
    render_inputs(project, store, level_id)
    queue = queue_of(request)
    for row in queue.list_for(user.id, project_id):
        if (row.level_id, row.camera_id) == (level_id, body.camera_id) and row.state in (
            "pending",
            "taken",
        ):
            response.status_code = 200
            return public_request(row, False)
    accounts = accounts_of(request)
    limit = quota_bytes(request)
    payer = None if accounts is None else accounts.payer_of(project_id)
    if accounts is not None and limit is not None and payer is not None:
        # KAN-12 : l'image déposée plus tard compte dans la place du propriétaire.
        used = used_bytes(request, accounts, payer)
        if used + min(LAUNCH_RESERVE, limit) > limit:
            raise full(user, payer, used, limit)
    try:
        row = queue.create(user.id, project_id, level_id, body.camera_id)
    except QuotaExceededError as exc:
        raise HTTPException(status_code=429, detail=QUOTA_TEXT) from exc
    return public_request(row, False)


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
    return job


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
    return public_request(done, True)


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
