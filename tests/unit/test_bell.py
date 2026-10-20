"""KAN-52 : la cloche en fonctions pures (filtrage, regroupement, « lu », pages) et le journal
du projet (anciennes lignes de KAN-51, purge à 90 jours), et l'état « lu » du mode local."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from openplan.api.bell_state import FileReadState
from openplan.pipeline import bell
from openplan.pipeline.events import EVENTS_FILE, ProjectEvents
from openplan.schemas.events import EventType, ProjectEvent

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
PID = uuid4()
ME, FANNY, GILLES = "me", "fanny", "gilles"


def _ev(kind: EventType, actor: str, minutes_ago: float, **extra: object) -> ProjectEvent:
    return ProjectEvent(
        type=kind,
        project_id=PID,
        actor=actor,
        at=NOW - timedelta(minutes=minutes_ago),
        **extra,  # type: ignore[arg-type]
    )


def _feed(events: list[ProjectEvent], *, owner: bool = False, **kw: object) -> bell.ProjectFeed:
    return bell.ProjectFeed(
        project_id=PID,
        name="Grenier",
        owner=owner,
        events=events,
        **kw,  # type: ignore[arg-type]
    )


def test_never_my_own_actions_except_a_finished_render() -> None:
    events = [
        _ev("board.changed", ME, 30),
        _ev("plan.changed", ME, 20),
        _ev("member.joined", ME, 10),
        _ev("render.done", ME, 5),
    ]
    lines = bell.lines_for(ME, [_feed(events, owner=True)], NOW)
    assert [line.type for line in lines] == ["render.done"]  # « Votre rendu est prêt »


def test_comments_go_to_the_thread_writers_and_the_owner_never_grouped() -> None:
    thread = uuid4()
    events = [
        _ev("comment.created", FANNY, 3, thread_id=thread),
        _ev("comment.replied", GILLES, 2, thread_id=thread),
        _ev("comment.replied", GILLES, 1, thread_id=thread),
        _ev("comment.resolved", GILLES, 0.5, thread_id=thread),
    ]
    writers = {thread: {FANNY, GILLES}}
    # Le propriétaire : tout sauf « résolu » ; jamais regroupé.
    owner = bell.lines_for(ME, [_feed(events, owner=True, thread_writers=writers)], NOW)
    assert [line.type for line in owner] == [
        "comment.replied",
        "comment.replied",
        "comment.created",
    ]
    assert all(line.count == 1 for line in owner)
    # Fanny a écrit dans le fil : elle reçoit les réponses de Gilles, pas son propre message.
    fanny = bell.lines_for(FANNY, [_feed(events, thread_writers=writers)], NOW)
    assert [line.type for line in fanny] == ["comment.replied", "comment.replied"]
    # Un membre qui n'a rien écrit dans le fil, non propriétaire : rien.
    assert bell.lines_for("kim", [_feed(events, thread_writers=writers)], NOW) == []


def test_changes_grouped_by_author_within_10_minutes() -> None:
    # 12 gestes de Gilles en 9 min : une ligne ; puis 11 min d'écart : une nouvelle ligne.
    burst = [_ev("board.changed", GILLES, 60 - i * (9 / 11)) for i in range(12)]
    later = _ev("board.changed", GILLES, 60 - 9 - 11)
    other = _ev("board.changed", FANNY, 55)
    plan = _ev("plan.changed", GILLES, 58)
    lines = bell.lines_for(ME, [_feed([*burst, later, other, plan])], NOW)
    by = {(line.actor, line.type, line.count) for line in lines}
    assert (GILLES, "board.changed", 12) in by
    assert (GILLES, "board.changed", 1) in by  # 11 min plus tard
    assert (FANNY, "board.changed", 1) in by  # un autre auteur : sa ligne
    assert (GILLES, "plan.changed", 1) in by  # une autre sorte : sa ligne
    assert len(lines) == 4
    grouped = next(line for line in lines if line.count == 12)
    assert grouped.id == burst[-1].id and grouped.at == burst[-1].at  # le geste le plus récent


def test_read_marks_one_line_without_touching_older_ones_then_all() -> None:
    events = [_ev("member.joined", f"m{i}", 10 - i) for i in range(3)]
    lines = bell.lines_for(ME, [_feed(events)], NOW)
    marks = bell.ReadMarks(ids=frozenset({lines[0].id}))
    assert bell.unread_count(lines, marks) == 2
    assert [line.id for line in lines if marks.unread(line)] == [lines[1].id, lines[2].id]
    assert bell.unread_count(lines, bell.ReadMarks(until=NOW)) == 0


def test_older_than_90_days_is_dropped_and_pages_of_50() -> None:
    events = [_ev("member.joined", f"m{i}", i) for i in range(60)]
    events.append(_ev("member.joined", "old", 60 * 24 * 91))
    lines = bell.lines_for(ME, [_feed(events)], NOW)
    assert len(lines) == 60
    first, cursor = bell.page(lines, bell.ReadMarks(), unread_only=False)
    assert len(first) == 50 and cursor is not None
    rest, end = bell.page(lines, bell.ReadMarks(), unread_only=False, before=cursor)
    assert len(rest) == 10 and end is None
    assert not {line.id for line in first} & {line.id for line in rest}


def test_journal_reads_kan51_lines_and_purges_after_90_days(tmp_path: Path) -> None:
    journal = ProjectEvents(tmp_path)
    old = {
        "type": "comment.created",
        "project_id": str(PID),
        "actor": FANNY,
        "thread_id": str(uuid4()),
        "item_id": None,
        "at": (NOW - timedelta(days=95)).isoformat(),
    }
    (tmp_path / EVENTS_FILE).write_text(json.dumps(old) + "\n", encoding="utf-8")
    first = journal.read()
    assert len(first) == 1 and journal.read()[0].id == first[0].id  # id stable, sans `id`
    # Ajouter une ligne purge celles de plus de 90 jours (la plus vieille a dépassé 91 jours).
    journal.append(_ev("member.joined", GILLES, 1), now=NOW)
    kept = journal.read()
    assert [e.type for e in kept] == ["member.joined"]
    raw = (tmp_path / EVENTS_FILE).read_text(encoding="utf-8")
    assert "comment.created" not in raw and '"label"' not in raw  # rien de vide écrit


def test_local_read_state_in_a_file(tmp_path: Path) -> None:
    state = FileReadState(tmp_path)
    one, two = uuid4(), uuid4()
    state.mark("local", one, PID, NOW)
    state.mark("local", two, PID, NOW - timedelta(minutes=1))
    assert state.marks("local").ids == {one, two}
    state.forget_project("local", PID)
    assert state.marks("local").ids == frozenset()
    state.mark_all("local", NOW)
    assert state.marks("local").until == NOW
    saved = json.loads((tmp_path / "notifications.json").read_text(encoding="utf-8"))
    assert set(saved["users"]) == {"local"}
