"""KAN-109 L2 : événements d'usage de l'application et indicateurs du back-office.

Aucune donnée de contenu : ni texte, ni nom, ni adresse, ni IP. Un événement a un nom pris
dans une liste fermée, une date, un pseudonyme du compte et, au besoin, du projet (HMAC avec
la clé de `data/events.secret`, jamais affichée ni exportée), et un nombre.
- un compte supprimé : ses événements partent (`forget_account`) ;
- après 13 mois : purgés (`purge`) ;
- les comptes administrateurs sont exclus des indicateurs, au calcul ;
- un indicateur calculé sur moins de 5 comptes (ou 5 rendus) ne s'affiche pas.

L'émission ne bloque jamais la requête qui l'émet : une erreur d'écriture est journalisée."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import statistics
import threading
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.models import Event, SiteDay

log = logging.getLogger("openplan.events")

NAMES = frozenset(
    {
        "account_created",
        "signed_in",
        "active",
        "project_created",
        "plan_built",
        "render_requested",
        "render_done",
        "render_failed",
        "invite_sent",
        "invite_accepted",
        "moodboard_linked",
    }
)
"""La liste fermée : un autre nom est refusé (erreur de programmation, jamais écrit)."""
CAUSES = ("lost", "vram", "refused", "other")
"""Causes d'un rendu en échec (`value` = rang + 1)."""
SECRET_FILE = "events.secret"
KEEP = timedelta(days=396)
"""13 mois."""
MIN_BASE = 5
"""En dessous, un indicateur ne s'affiche pas (on ne déduit jamais une personne)."""


def emit_from(
    holder: object,
    name: str,
    user_id: UUID | str | None,
    project_id: UUID | str | None = None,
    value: int | None = None,
) -> None:
    """Émet depuis une requête ou l'application (sans journal d'événements, ou sans compte
    connu, rien)."""
    app = getattr(holder, "app", holder)
    events = getattr(getattr(app, "state", None), "events", None)
    if isinstance(events, EventLog) and user_id is not None:
        events.emit(name, user_id, project_id, value)


WORKER_CAUSES = {"vram": "vram", "invalid_input": "refused", "lost": "lost"}
"""Les codes d'échec du poste ramenés aux causes du back-office ; le reste : `other`."""


def cause_code(cause: str) -> int:
    """Le code (1 à 4) d'une cause du poste ou du serveur (`lost`)."""
    kept = WORKER_CAUSES.get(cause, cause)
    return CAUSES.index(kept) + 1 if kept in CAUSES else CAUSES.index("other") + 1


def load_secret(data_dir: Path) -> bytes:
    """La clé des pseudonymes : créée au premier démarrage (600), jamais affichée."""
    path = data_dir / SECRET_FILE
    if not path.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(secrets.token_hex(32))
    return path.read_text(encoding="ascii").strip().encode()


def _week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _ratio(part: int, base: int) -> float | None:
    return round(part / base, 3) if base >= MIN_BASE else None


class EventLog:
    """Écriture des événements et calcul des indicateurs (mode comptes)."""

    def __init__(
        self,
        engine: Engine,
        secret: bytes,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self._secret = secret
        self.clock = clock
        self._active: dict[str, date] = {}
        self._lock = threading.Lock()

    def session(self) -> Session:
        return self.sessions()

    def pseudonym(self, key: UUID | str, prefix: str = "u:") -> str:
        digest = hmac.new(self._secret, f"{prefix}{key}".encode(), hashlib.sha256)
        return digest.hexdigest()[:32]

    # --- écriture -------------------------------------------------------------------------

    def emit(
        self,
        name: str,
        user_id: UUID | str,
        project_id: UUID | str | None = None,
        value: int | None = None,
    ) -> None:
        """Écrit un événement ; ne lève jamais (sauf un nom hors de la liste)."""
        if name not in NAMES:
            msg = f"événement inconnu : {name}"
            raise ValueError(msg)
        try:
            with self.session() as db:
                db.add(
                    Event(
                        at=self.clock(),
                        name=name,
                        actor=self.pseudonym(user_id),
                        project=None if project_id is None else self.pseudonym(project_id, "p:"),
                        value=value,
                    )
                )
                db.commit()
        except Exception:
            log.exception("événement %s non écrit", name)

    def active(self, user_id: UUID | str) -> None:
        """Au plus un « active » par compte et par jour (cache en mémoire)."""
        key = str(user_id)
        today = self.clock().date()
        with self._lock:
            if self._active.get(key) == today:
                return
            self._active[key] = today
        self.emit("active", user_id)

    def forget_account(self, user_id: UUID | str) -> int:
        """Droit à l'effacement : les événements de ce compte partent."""
        with self.session() as db:
            gone = db.execute(delete(Event).where(Event.actor == self.pseudonym(user_id)))
            db.commit()
        return int(getattr(gone, "rowcount", 0) or 0)

    def purge(self) -> None:
        """Les événements de plus de 13 mois partent."""
        with self.session() as db:
            db.execute(delete(Event).where(Event.at < self.clock() - KEEP))
            db.commit()

    def start(self) -> None:
        """Purge au démarrage, puis une fois par jour, dans un fil à part."""
        stop = threading.Event()
        self._stop = stop

        def loop() -> None:
            while not stop.is_set():
                try:
                    self.purge()
                except Exception:
                    log.exception("purge des événements en échec")
                stop.wait(24 * 3600)

        threading.Thread(target=loop, name="events-purge", daemon=True).start()

    # --- indicateurs ----------------------------------------------------------------------

    def usage(self, weeks: int, admins: Iterable[UUID | str] = ()) -> dict[str, object]:
        """Les indicateurs de KAN-109, semaine par semaine (lundi, UTC), sur `weeks` semaines.
        Comptes administrateurs exclus ; un taux sur moins de 5 comptes vaut None."""
        now = self.clock()
        last = _week(now.date())
        first = last - timedelta(weeks=weeks - 1)
        since = datetime.combine(first, datetime.min.time(), UTC)
        hidden = {self.pseudonym(a) for a in admins}
        with self.session() as db:
            rows = [
                (
                    e.at if e.at.tzinfo else e.at.replace(tzinfo=UTC),
                    e.name,
                    e.actor,
                    e.project,
                    e.value,
                )
                for e in db.scalars(select(Event).where(Event.at >= since).order_by(Event.at))
                if e.actor not in hidden
            ]
            visits = dict(
                db.execute(
                    select(SiteDay.day, SiteDay.views).where(SiteDay.day >= first.isoformat())
                ).all()
            )
            oldest = db.scalar(select(func.min(Event.at)))
        created: dict[str, datetime] = {}
        firsts: dict[tuple[str, str], datetime] = {}
        active_days: dict[str, set[date]] = defaultdict(set)
        for at, name, actor, _, _ in rows:
            if name == "account_created":
                created.setdefault(actor, at)
            if name in ("plan_built", "render_done"):
                firsts.setdefault((name, actor), at)
            if name == "active":
                active_days[actor].add(at.date())

        out = []
        for i in range(weeks):
            start = first + timedelta(weeks=i)
            end = start + timedelta(days=7)
            in_week = [r for r in rows if start <= r[0].date() < end]
            names = Counter(r[1] for r in in_week)
            cohort = [a for a, at in created.items() if start <= at.date() < end]
            out.append(
                {
                    "week": start.isoformat(),
                    "signups": names["account_created"],
                    "activation": self._activation(cohort, created, firsts),
                    "first_render_hours": self._first_render(cohort, created, firsts),
                    "renders": self._renders(in_week),
                    "retention": self._retention(cohort, created, active_days, now),
                    "usage": self._usage(in_week),
                    "active": self._active_counts(
                        in_week, start, min(end, now.date() + timedelta(days=1))
                    ),
                    "site": {
                        "visits": sum(
                            v for d, v in visits.items() if start.isoformat() <= d < end.isoformat()
                        ),
                        "signups": names["account_created"],
                    },
                }
            )
        return {"weeks": out, "since": oldest.isoformat() if oldest else None}

    @staticmethod
    def _activation(
        cohort: list[str], created: dict[str, datetime], firsts: dict[tuple[str, str], datetime]
    ) -> dict[str, object]:
        def within(name: str, actor: str) -> bool:
            at = firsts.get((name, actor))
            return at is not None and at - created[actor] <= timedelta(days=7)

        plan = [a for a in cohort if within("plan_built", a)]
        render = [a for a in plan if within("render_done", a)]
        return {
            "cohort": len(cohort),
            "plan": _ratio(len(plan), len(cohort)),
            "render": _ratio(len(render), len(cohort)),
        }

    @staticmethod
    def _first_render(
        cohort: list[str], created: dict[str, datetime], firsts: dict[tuple[str, str], datetime]
    ) -> float | None:
        hours = [
            (firsts[("render_done", a)] - created[a]).total_seconds() / 3600
            for a in cohort
            if ("render_done", a) in firsts
        ]
        return round(statistics.median(hours), 1) if len(hours) >= MIN_BASE else None

    @staticmethod
    def _renders(
        in_week: list[tuple[datetime, str, str, str | None, int | None]],
    ) -> dict[str, object]:
        requested = sum(1 for r in in_week if r[1] == "render_requested")
        done = sum(1 for r in in_week if r[1] == "render_done")
        failed = [r[4] for r in in_week if r[1] == "render_failed"]
        causes = Counter(CAUSES[v - 1] if v and 1 <= v <= len(CAUSES) else "other" for v in failed)
        durations = [r[4] for r in in_week if r[1] == "render_done" and r[4] is not None]
        return {
            "requested": requested,
            "done": done,
            "failed": len(failed),
            "success": _ratio(done, done + len(failed)),
            "causes": {c: causes[c] for c in CAUSES},
            "median_seconds": statistics.median(durations) if len(durations) >= MIN_BASE else None,
        }

    @staticmethod
    def _retention(
        cohort: list[str],
        created: dict[str, datetime],
        active_days: dict[str, set[date]],
        now: datetime,
    ) -> dict[str, object]:
        def kept(after: int) -> float | None:
            due = [a for a in cohort if created[a] + timedelta(days=after + 7) <= now]
            if len(due) != len(cohort):
                return None  # la cohorte n'a pas encore eu le temps
            back = [
                a
                for a in due
                if any(
                    created[a].date() + timedelta(days=after)
                    <= d
                    < created[a].date() + timedelta(days=after + 7)
                    for d in active_days.get(a, ())
                )
            ]
            return _ratio(len(back), len(due))

        return {"cohort": len(cohort), "d7": kept(7), "d30": kept(30)}

    @staticmethod
    def _usage(
        in_week: list[tuple[datetime, str, str, str | None, int | None]],
    ) -> dict[str, object]:
        projects = {r[3] for r in in_week if r[3]}
        plans = sum(1 for r in in_week if r[1] == "plan_built")
        requested = [r for r in in_week if r[1] == "render_requested"]
        guided = sum(1 for r in requested if r[4] == 1)
        n = len(projects)
        return {
            "projects": n,
            "plans_per_project": round(plans / n, 2) if n >= MIN_BASE else None,
            "renders_per_project": round(len(requested) / n, 2) if n >= MIN_BASE else None,
            "guided": _ratio(guided, len(requested)),
            "moodboards_linked": sum(1 for r in in_week if r[1] == "moodboard_linked"),
        }

    @staticmethod
    def _active_counts(
        in_week: list[tuple[datetime, str, str, str | None, int | None]], start: date, end: date
    ) -> dict[str, object]:
        by_day: dict[date, set[str]] = defaultdict(set)
        for at, name, actor, _, _ in in_week:
            if name == "active":
                by_day[at.date()].add(actor)
        days = max(1, (end - start).days)
        accounts = {a for s in by_day.values() for a in s}
        return {
            "weekly": len(accounts),
            "daily_mean": round(sum(len(s) for s in by_day.values()) / days, 1),
        }
