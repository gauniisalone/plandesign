"""Galerie des rendus (US2) : liste avec titre, favoris, vue et ambiance ; renommer, marquer
en favori, supprimer avec corbeille douce et annulation, télécharger les favoris en zip."""

from __future__ import annotations

import threading
from contextlib import suppress
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import Field

from openplan.api.deps import ProjectDep, SettingsDep, StoreDep
from openplan.pipeline import gallery as g
from openplan.pipeline import journal
from openplan.pipeline.gallery import GalleryEntry, GalleryError, Variant
from openplan.pipeline.journal import JournalEvent
from openplan.pipeline.pick import PickError, PickResult, outline_png, pick
from openplan.pipeline.render import RENDER_SIDECAR
from openplan.scene.store import ProjectNotFoundError, ProjectStore
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["gallery"])


class GalleryPatch(StrictModel):
    """Champs absents : inchangés. `title` vide ou null : le nom du point de vue revient."""

    title: str | None = Field(default=None, max_length=200)
    favorites: list[Variant] | None = None


class Trashed(StrictModel):
    run_id: UUID
    purge_after_s: float
    """Le rendu peut être remis (« Annuler ») pendant ce délai ; ensuite il est effacé."""


def purge_project(store: ProjectStore, project_id: UUID, *, older_than_s: float) -> list[UUID]:
    """Efface les rendus de la corbeille assez anciens, et le note au journal V2."""
    gone = g.purge(store.runs_dir(project_id), older_than_s=older_than_s)
    if gone:
        with suppress(ProjectNotFoundError, ValueError):
            project = store.load(project_id)
            for run_id in gone:
                g.record_deleted(store.project_dir(project_id), project, run_id)
    return gone


def purge_all(store: ProjectStore) -> int:
    """Au démarrage et à l'arrêt du serveur : la corbeille de chaque projet est vidée."""
    count = 0
    for summary in store.list():
        count += len(purge_project(store, summary.id, older_than_s=0.0))
    return count


def _conflict(exc: GalleryError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/projects/{project_id}/renders")
def renders(project: ProjectDep, store: StoreDep) -> list[GalleryEntry]:
    """Galerie : les rendus du projet, du plus récent au plus ancien, avec leur titre, leurs
    images favorites, leur vue (ensemble, zone, détail, à la main, photo, variante) et
    l'ambiance du moment."""
    purge_project(store, project.id, older_than_s=g.TRASH_TTL_S)
    return g.gallery(project, store.runs_dir(project.id))


@router.patch("/projects/{project_id}/renders/{run_id}")
def update_render(
    run_id: UUID, body: GalleryPatch, project: ProjectDep, store: StoreDep
) -> GalleryEntry:
    """Renommer un rendu (titre court) et/ou fixer ses images favorites."""
    kwargs: dict[str, object] = {}
    if "title" in body.model_fields_set:
        kwargs["title"] = body.title
    try:
        return g.update(
            project,
            store.runs_dir(project.id),
            run_id,
            favorites=body.favorites,
            **kwargs,
        )
    except GalleryError as exc:
        raise _conflict(exc) from exc


@router.delete("/projects/{project_id}/renders/{run_id}")
def delete_render(run_id: UUID, project: ProjectDep, store: StoreDep) -> Trashed:
    """Corbeille douce : le rendu (toutes ses images) quitte la galerie tout de suite ; il
    peut être remis par `.../restore` pendant `purge_after_s` secondes, puis il est effacé
    pour de bon (ou à l'arrêt du serveur) et le journal V2 le note."""
    runs_dir = store.runs_dir(project.id)
    try:
        g.trash(runs_dir, run_id)
    except GalleryError as exc:
        raise _conflict(exc) from exc
    pid = project.id
    timer = threading.Timer(
        g.TRASH_TTL_S + 1.0,
        lambda: purge_project(store, pid, older_than_s=g.TRASH_TTL_S),
    )
    timer.daemon = True
    timer.start()
    return Trashed(run_id=run_id, purge_after_s=g.TRASH_TTL_S)


@router.post("/projects/{project_id}/renders/{run_id}/restore")
def restore_render(run_id: UUID, project: ProjectDep, store: StoreDep) -> GalleryEntry:
    """« Annuler » une suppression tant que le rendu est encore dans la corbeille."""
    runs_dir = store.runs_dir(project.id)
    try:
        g.restore(runs_dir, run_id)
        return g.one(project, runs_dir, run_id)
    except GalleryError as exc:
        raise _conflict(exc) from exc


@router.get("/projects/{project_id}/renders/{run_id}/pick")
def pick_in_render(
    run_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    settings: SettingsDep,
    x: float = Query(ge=0.0, le=1.0),
    y: float = Query(ge=0.0, le=1.0),
) -> PickResult:
    """P4 : l'élément vu en (x, y) dans un rendu (coordonnées relatives à l'image, 0 en haut à
    gauche), d'après sa carte des éléments ; un rendu plus ancien la reçoit à la demande. 409
    pour un rendu inconnu ou sans scène Blender."""
    render_dir = store.runs_dir(project.id) / str(run_id) / "blender"
    if not (render_dir / RENDER_SIDECAR).is_file():
        raise HTTPException(status_code=409, detail=f"rendu introuvable : {run_id}")
    try:
        result = pick(project, render_dir, x, y, blender_bin=settings.blender_bin)
    except PickError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # P4c : chaque désignation au journal local (sorte trouvée, jamais la position du clic).
    what = result.element_type or result.decor_kind or "rien"
    journal.record(
        store.project_dir(project.id),
        project,
        JournalEvent(
            project_id=project.id,
            kind="render_picked",
            note=f"{result.kind}:{what}",
            element_ids=[result.element_id] if result.element_id else [],
            run_ids=[run_id],
        ),
    )
    return result


@router.get("/projects/{project_id}/renders/{run_id}/outline.png")
def outline_in_render(
    run_id: UUID,
    project: ProjectDep,
    store: StoreDep,
    x: float = Query(ge=0.0, le=1.0),
    y: float = Query(ge=0.0, le=1.0),
) -> Response:
    """P4b : contour de ce qui se voit en (x, y), PNG transparent à poser sur l'image (après
    `pick`, qui fait la carte si besoin)."""
    render_dir = store.runs_dir(project.id) / str(run_id) / "blender"
    try:
        data = outline_png(render_dir, x, y)
    except PickError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png")


@router.get("/projects/{project_id}/renders/favorites.zip")
def favorites_zip(project: ProjectDep, store: StoreDep) -> Response:
    """Les images favorites du projet en un zip. 409 s'il n'y en a aucune."""
    try:
        data, _ = g.favorites_zip(project, store.runs_dir(project.id))
    except GalleryError as exc:
        raise _conflict(exc) from exc
    day = datetime.now().strftime("%Y-%m-%d")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="rendus-favoris-{day}.zip"'},
    )
