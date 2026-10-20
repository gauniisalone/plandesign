"""La cloche (KAN-52) : lignes de notification, compteur des non-lus, « marquer comme lu ».

Lit le journal (`events.jsonl`) des seuls projets dont la personne est membre à cet instant
(en local : tous les projets de la machine), filtre et regroupe (`pipeline.bell`), puis applique
l'état « lu » (`api.bell_state`). Même code en local et avec les comptes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from openplan.api.access import accounts_of
from openplan.api.bell_state import ReadState
from openplan.api.deps import ActorDep, ProjectDep, StoreDep
from openplan.api.routes.comments import AuthorNames, author_of
from openplan.pipeline import bell
from openplan.pipeline.comments import CommentStore
from openplan.pipeline.events import ProjectEvents
from openplan.scene.store import ProjectStore
from openplan.schemas.common import StrictModel, now_utc
from openplan.schemas.events import EventType, ProjectEvent

router = APIRouter(tags=["cloche"])


class LineOut(StrictModel):
    id: UUID
    type: EventType
    actor_id: str
    """`local` ou l'id du compte (filtre « par personne » du journal d'activité)."""
    actor_name: str
    actor_color: str
    mine: bool
    project_id: UUID
    project_name: str
    at: datetime
    count: int
    unread: bool
    thread_id: UUID | None
    item_id: UUID | None
    level_id: UUID | None
    run_id: UUID | None
    label: str | None
    item_kind: str | None
    added: int
    changed: int
    removed: int
    recolored: int = 0
    """KAN-65 : parmi les modifiés, ceux dont seule la couleur a changé."""


class BellPage(StrictModel):
    items: list[LineOut]
    next: str | None
    """Curseur de « Voir plus » (None : plus rien)."""
    unread: int


class UnreadCount(StrictModel):
    unread: int


class ReadBody(StrictModel):
    event_id: UUID


def _state(request: Request) -> ReadState:
    state: ReadState = request.app.state.bell_reads
    return state


def _feeds(request: Request, store: ProjectStore, me: str) -> list[bell.ProjectFeed]:
    accounts = accounts_of(request)
    summaries = {s.id: s.name for s in store.list()}
    # En local, une seule personne : tous les projets de la machine sont les siens.
    roles = dict.fromkeys(summaries, "owner") if accounts is None else accounts.roles_of(UUID(me))
    feeds = []
    for project_id, role in roles.items():
        if project_id not in summaries:
            continue
        project_dir = store.project_dir(project_id)
        writers = {
            t.id: {m.author for m in t.messages} for t in CommentStore(project_dir).load().threads
        }
        feeds.append(
            bell.ProjectFeed(
                project_id=project_id,
                name=summaries[project_id],
                owner=role == "owner",
                events=ProjectEvents(project_dir).read(),
                thread_writers=writers,
            )
        )
    return feeds


def _lines(request: Request, store: ProjectStore, me: str) -> list[bell.BellLine]:
    return bell.lines_for(me, _feeds(request, store, me), now_utc())


def _cursor(raw: str | None) -> tuple[datetime, str] | None:
    if not raw:
        return None
    at, _, line_id = raw.partition("|")
    try:
        return datetime.fromisoformat(at), str(UUID(line_id))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="curseur de la cloche invalide") from exc


RENDER_KINDS = frozenset({"render", "render_all", "finish", "catalogue", "contextual"})
"""Les tâches qui livrent un rendu : leur fin publie `render.done` (« Votre rendu est prêt »)."""


def render_done_hook(
    store: ProjectStore, after: Callable[[UUID], None] | None = None
) -> Callable[[str, str, UUID | None, BaseModel], None]:
    """Rappel de fin de tâche (`JobRegistry.on_done`) : un rendu terminé entre au journal du
    projet, au nom de celui qui l'a lancé (notifié à tous les membres, lui compris) ; `after`
    fait sonner leurs cloches (comptes)."""

    def done(kind: str, owner: str, project_id: UUID | None, result: BaseModel) -> None:
        if kind not in RENDER_KINDS or project_id is None or not store.exists(project_id):
            return
        run_id = getattr(result, "run_id", None)
        level_id = getattr(result, "level_id", None)
        ProjectEvents(store.project_dir(project_id)).append(
            ProjectEvent(
                type="render.done",
                project_id=project_id,
                actor=author_of(owner),
                run_id=run_id if isinstance(run_id, UUID) else None,
                level_id=level_id if isinstance(level_id, UUID) else None,
            )
        )
        if after is not None:
            after(project_id)

    return done


@router.get("/notifications")
def notifications(
    request: Request,
    store: StoreDep,
    actor: ActorDep,
    tab: Literal["unread", "all"] = "unread",
    before: str | None = Query(default=None, max_length=120),
) -> BellPage:
    """Les 50 lignes suivantes (« Non lues » ou « Toutes »), de la plus récente."""
    me = author_of(actor)
    lines = _lines(request, store, me)
    marks = _state(request).marks(me)
    shown, cursor = bell.page(lines, marks, unread_only=tab == "unread", before=_cursor(before))
    return BellPage(
        items=_out(request, shown, me, marks),
        next=_next(cursor),
        unread=bell.unread_count(lines, marks),
    )


def _next(cursor: tuple[datetime, str] | None) -> str | None:
    return f"{cursor[0].isoformat()}|{cursor[1]}" if cursor else None


def _out(
    request: Request, shown: list[bell.BellLine], me: str, marks: bell.ReadMarks | None
) -> list[LineOut]:
    """Les lignes telles que les voit la personne : noms et couleurs (« Ancien membre »),
    « mine », lue ou non (le journal d'activité n'a pas d'état « lu »)."""
    names: dict[UUID, AuthorNames] = {}
    items = []
    for line in shown:
        project_names = names.setdefault(line.project_id, AuthorNames(request, line.project_id, me))
        name, color = project_names(line.actor)
        items.append(
            LineOut(
                id=line.id,
                type=line.type,
                actor_id=line.actor,
                actor_name=name,
                actor_color=color,
                mine=line.actor == me,
                project_id=line.project_id,
                project_name=line.project_name,
                at=line.at,
                count=line.count,
                unread=marks.unread(line) if marks is not None else False,
                thread_id=line.thread_id,
                item_id=line.item_id,
                level_id=line.level_id,
                run_id=line.run_id,
                label=line.label,
                item_kind=line.item_kind,
                added=line.added,
                changed=line.changed,
                removed=line.removed,
                recolored=line.recolored,
            )
        )
    return items


class ActivityPage(StrictModel):
    items: list[LineOut]
    next: str | None


@router.get("/projects/{project_id}/activity")
def activity(
    project: ProjectDep,
    request: Request,
    store: StoreDep,
    actor: ActorDep,
    who: str | None = Query(default=None, max_length=120),
    kind: Literal["comments", "board", "plan", "members", "renders"] | None = None,
    before: str | None = Query(default=None, max_length=120),
) -> ActivityPage:
    """KAN-58 : le journal d'activité du projet, 50 lignes puis « Voir plus » ; tout membre le
    lit (lecteur compris), seul le serveur l'écrit."""
    me = author_of(actor)
    project_dir = store.project_dir(project.id)
    feed = bell.ProjectFeed(
        project_id=project.id,
        name=project.name,
        owner=False,
        events=ProjectEvents(project_dir).read(),
    )
    lines = bell.activity_lines(feed, now_utc(), who=who, kind=kind)
    shown, cursor = bell.page(lines, bell.ReadMarks(), unread_only=False, before=_cursor(before))
    return ActivityPage(items=_out(request, shown, me, None), next=_next(cursor))


@router.get("/notifications/count")
def unread_count(request: Request, store: StoreDep, actor: ActorDep) -> UnreadCount:
    """Le badge de la cloche (« 9+ » au-delà de 9 : affiché par le front)."""
    me = author_of(actor)
    return UnreadCount(
        unread=bell.unread_count(_lines(request, store, me), _state(request).marks(me))
    )


@router.post("/notifications/read", status_code=204)
def mark_read(body: ReadBody, request: Request, store: StoreDep, actor: ActorDep) -> Response:
    """Une ligne lue (le clic qui ouvre le projet au bon endroit), sans toucher aux autres."""
    me = author_of(actor)
    line = next((x for x in _lines(request, store, me) if x.id == body.event_id), None)
    if line is None:
        raise HTTPException(status_code=404, detail="notification inconnue")
    _state(request).mark(me, line.id, line.project_id, line.at)
    return Response(status_code=204)


@router.post("/notifications/read-all", status_code=204)
def mark_all_read(request: Request, actor: ActorDep) -> Response:
    """« Tout marquer comme lu » : le compteur revient à 0."""
    _state(request).mark_all(author_of(actor), now_utc())
    return Response(status_code=204)
