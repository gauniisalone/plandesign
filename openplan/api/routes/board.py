"""Tableau du projet (B1) et fichiers joints : la disposition se lit et s'enregistre à part de
la géométrie ; un fichier joint est gardé tel quel et servi en pièce jointe (sauf les images
courantes, montrées)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, UploadFile

from openplan.api.deps import ProjectDep, StoreDep
from openplan.api.files import served_file
from openplan.pipeline import board as b
from openplan.pipeline.board import MediaError
from openplan.schemas.board import Board, MediaRef

router = APIRouter(tags=["board"])


@router.get("/projects/{project_id}/board")
def get_board(project: ProjectDep, store: StoreDep) -> Board:
    """Le tableau, à jour de ce que le projet contient (mise en page de départ la première
    fois ; photos, rendus, plan et demande ajoutés ensuite dans leur cadre)."""
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
    return board


@router.put("/projects/{project_id}/board")
def put_board(body: Board, project: ProjectDep, store: StoreDep) -> Board:
    """Enregistre la disposition (positions, tailles, ordre, verrous, cadres, notes, vue). Ne
    touche jamais la géométrie du plan. Ce qui a été retiré parmi ce que le projet fournit
    n'est pas remis ensuite. 409 si l'envoi part d'une révision périmée (le tableau a été
    enregistré ailleurs entre-temps, deux onglets) : rien n'est écrasé, l'atelier recharge."""
    project_dir = store.project_dir(project.id)
    frames = {i.id for i in body.items if i.kind == "frame"}
    items = [
        i if i.frame is None or i.frame in frames else i.model_copy(update={"frame": None})
        for i in body.items
    ]
    with store.lock(project.id):
        after = b.replaced(b.load_board(project_dir), body.model_copy(update={"items": items}))
        try:
            return b.save_board(project_dir, after, expect=body.revision)
        except b.StaleBoardError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc


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
async def media_upload(file: UploadFile, project: ProjectDep, store: StoreDep) -> MediaRef:
    """Joint un fichier au projet, gardé sur cette machine : une vidéo (mp4, webm) jusqu'à
    200 Mo, tout autre fichier jusqu'à 50 Mo ; un PDF reçoit la vignette de sa première page."""
    data = await file.read(b.MAX_VIDEO_BYTES + 1)
    try:
        with store.lock(project.id):
            return b.add_media(store.project_dir(project.id), data, file.filename or "fichier")
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
