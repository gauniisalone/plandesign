"""La cloche (KAN-52) : ce qu'une personne reçoit du journal de ses projets, regroupé.

Règles (ticket KAN-52 et décisions PO du 20 oct.) :
- jamais ses propres gestes, sauf `render.done` (« Votre rendu est prêt », pour tous les
  membres, lanceur compris) ;
- un commentaire ou une réponse : à ceux qui ont écrit dans le fil, et au propriétaire ;
  résoudre ou rouvrir restent au journal, sans notification ;
- `member.joined` : à tous les autres membres ;
- `board.changed` / `plan.changed` : à tous les autres membres, regroupés par auteur, projet et
  sorte quand deux gestes se suivent à 10 min au plus (« 12 changements ») ; les commentaires
  ne sont jamais regroupés ;
- 90 jours au plus, et rien d'avant l'arrivée de la personne dans le projet.

Fonctions pures : l'appelant fournit les journaux (projets dont la personne est membre à cet
instant, ce qui fait disparaître un projet quitté) et l'état « lu »."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from openplan.schemas.events import EventType, ProjectEvent

GROUP_GAP = timedelta(minutes=10)
RETENTION = timedelta(days=90)
PAGE = 50
GROUPED: frozenset[EventType] = frozenset({"board.changed", "plan.changed"})
NOTIFIED: frozenset[EventType] = frozenset(
    {"comment.created", "comment.replied", "member.joined", "render.done", *GROUPED}
)


@dataclass(frozen=True)
class ProjectFeed:
    """Un projet de la personne : son journal et ce qu'il faut pour filtrer."""

    project_id: UUID
    name: str
    owner: bool
    events: list[ProjectEvent]
    thread_writers: Mapping[UUID, set[str]] = field(default_factory=dict)
    """Par fil, les auteurs de ses messages (d'après `comments.json` au moment de lire)."""


@dataclass
class BellLine:
    id: UUID
    """Celui du geste le plus récent de la ligne : une ligne regroupée qui grandit redevient
    non lue."""
    type: EventType
    actor: str
    project_id: UUID
    project_name: str
    at: datetime
    count: int = 1
    thread_id: UUID | None = None
    item_id: UUID | None = None
    level_id: UUID | None = None
    run_id: UUID | None = None
    label: str | None = None
    item_kind: str | None = None
    added: int = 0
    changed: int = 0
    removed: int = 0
    """KAN-58 : le détail cumulé des modifications regroupées de l'espace de travail."""
    recolored: int = 0
    """KAN-65 : parmi les modifiés, ceux dont seule la couleur a changé (cumulé)."""


@dataclass(frozen=True)
class ReadMarks:
    until: datetime | None = None
    ids: frozenset[UUID] = frozenset()

    def unread(self, line: BellLine) -> bool:
        return line.id not in self.ids and (self.until is None or line.at > self.until)


def _concerns(event: ProjectEvent, me: str, feed: ProjectFeed) -> bool:
    if event.type not in NOTIFIED:
        return False
    if event.type == "render.done":
        return True
    if event.actor == me:
        return False
    if event.type in ("comment.created", "comment.replied"):
        writers = feed.thread_writers.get(event.thread_id, set()) if event.thread_id else set()
        return feed.owner or me in writers
    return True


def _line(event: ProjectEvent, feed: ProjectFeed) -> BellLine:
    return BellLine(
        id=event.id,
        type=event.type,
        actor=event.actor,
        project_id=feed.project_id,
        project_name=feed.name,
        at=event.at,
        thread_id=event.thread_id,
        item_id=event.item_id,
        level_id=event.level_id,
        run_id=event.run_id,
        label=event.label,
        item_kind=event.item_kind,
        added=event.added or 0,
        changed=event.changed or 0,
        removed=event.removed or 0,
        recolored=event.recolored or 0,
    )


def _grouped(feed: ProjectFeed, events: Iterable[ProjectEvent], out: list[BellLine]) -> None:
    """Les lignes d'un projet, modifications regroupées (même auteur, même sorte, 10 min au
    plus entre deux gestes) ; le détail s'additionne, l'objet ne reste que s'il est unique."""
    groups: dict[tuple[str, str], BellLine] = {}
    for event in sorted(events, key=lambda e: e.at):
        if event.type not in GROUPED:
            out.append(_line(event, feed))
            continue
        key = (event.actor, event.type)
        group = groups.get(key)
        if group is None or event.at - group.at > GROUP_GAP:
            groups[key] = line = _line(event, feed)
            out.append(line)
            continue
        same = group.item_id is not None and group.item_id == event.item_id
        group.count += 1
        group.id, group.at = event.id, event.at
        group.level_id = event.level_id or group.level_id
        group.added += event.added or 0
        group.changed += event.changed or 0
        group.removed += event.removed or 0
        group.recolored += event.recolored or 0
        if not same:
            group.item_id = group.item_kind = group.label = None


def lines_for(me: str, feeds: Iterable[ProjectFeed], now: datetime) -> list[BellLine]:
    """Toutes les lignes de la cloche de la personne, de la plus récente à la plus ancienne."""
    out: list[BellLine] = []
    oldest = now - RETENTION
    for feed in feeds:
        # Un membre arrivé en cours de route ne reçoit pas ce qui précède son arrivée.
        joined = max(
            (e.at for e in feed.events if e.type == "member.joined" and e.actor == me),
            default=oldest,
        )
        since = max(oldest, joined)
        _grouped(feed, (e for e in feed.events if e.at >= since and _concerns(e, me, feed)), out)
    out.sort(key=lambda line: (line.at, str(line.id)), reverse=True)
    return out


ACTIVITY_KINDS: dict[str, frozenset[EventType]] = {
    "comments": frozenset(
        {"comment.created", "comment.replied", "comment.resolved", "comment.reopened"}
    ),
    "board": frozenset({"board.changed"}),
    "plan": frozenset({"plan.changed"}),
    "members": frozenset({"member.joined"}),
    "renders": frozenset({"render.done"}),
}
"""KAN-58 : les filtres du journal d'activité."""


def activity_lines(
    feed: ProjectFeed, now: datetime, *, who: str | None = None, kind: str | None = None
) -> list[BellLine]:
    """KAN-58 : le journal d'activité d'un projet, tout le journal (mes gestes compris, et ce
    que la cloche ne notifie pas), regroupé comme la cloche ; filtré par personne et sorte."""
    types = ACTIVITY_KINDS.get(kind) if kind else None
    events = [
        e
        for e in feed.events
        if e.at >= now - RETENTION
        and (who is None or e.actor == who)
        and (types is None or e.type in types)
    ]
    out: list[BellLine] = []
    _grouped(feed, events, out)
    out.sort(key=lambda line: (line.at, str(line.id)), reverse=True)
    return out


def page(
    lines: list[BellLine],
    marks: ReadMarks,
    *,
    unread_only: bool,
    before: tuple[datetime, str] | None = None,
    size: int = PAGE,
) -> tuple[list[BellLine], tuple[datetime, str] | None]:
    """Une page (« Voir plus » : `before` = le curseur de la page précédente)."""
    chosen = [
        line
        for line in lines
        if (not unread_only or marks.unread(line))
        and (before is None or (line.at, str(line.id)) < before)
    ]
    shown = chosen[:size]
    cursor = (shown[-1].at, str(shown[-1].id)) if len(chosen) > size else None
    return shown, cursor


def unread_count(lines: list[BellLine], marks: ReadMarks) -> int:
    return sum(1 for line in lines if marks.unread(line))
