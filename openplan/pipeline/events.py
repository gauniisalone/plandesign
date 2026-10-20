"""Journal des gestes d'un projet (`events.jsonl`, KAN-52) : ajout d'une ligne par geste,
lecture, purge à 90 jours. Une seule porte, commune au local et à la prod, pour pouvoir passer
un jour à une table sans toucher aux routes ; le journal garde chaque geste brut (KAN-13), la
cloche regroupe à la lecture (`pipeline.bell`). À ne pas confondre avec `pipeline.journal`, le
journal d'usage local."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import ValidationError

from openplan.schemas.common import now_utc
from openplan.schemas.events import ProjectEvent

EVENTS_FILE = "events.jsonl"
RETENTION = timedelta(days=90)
PURGE_SLACK = timedelta(days=1)
"""La purge réécrit le fichier quand sa plus vieille ligne a 91 jours : au plus une fois par
jour et par projet, jamais à chaque ajout."""

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()
_cache: dict[Path, tuple[tuple[int, int], list[ProjectEvent]]] = {}


def _lock(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(path, threading.Lock())


def _parse(line: str) -> ProjectEvent | None:
    """Une ligne ; celles de KAN-51 n'ont pas d'id : il est tiré de la ligne, donc stable."""
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("id", str(uuid5(NAMESPACE_URL, "openplan-event:" + line.strip())))
    try:
        return ProjectEvent.model_validate(data)
    except ValidationError:
        return None  # une ligne d'un type futur ou abîmée : ignorée, jamais une erreur


class ProjectEvents:
    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / EVENTS_FILE

    def append(self, event: ProjectEvent, *, now: datetime | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with _lock(self.path):
            self._purge_if_due(now or now_utc())
            with self.path.open("a", encoding="utf-8") as out:
                out.write(event.model_dump_json(exclude_none=True) + "\n")

    def read(self) -> list[ProjectEvent]:
        """Toutes les lignes, de la plus ancienne à la plus récente (gardées en mémoire tant
        que le fichier ne change pas)."""
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        key = (stat.st_mtime_ns, stat.st_size)
        cached = _cache.get(self.path)
        if cached is not None and cached[0] == key:
            return cached[1]
        events = [
            e
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip() and (e := _parse(line)) is not None
        ]
        _cache[self.path] = (key, events)
        return events

    def _purge_if_due(self, now: datetime) -> None:
        if not self.path.is_file():
            return
        with self.path.open(encoding="utf-8") as src:
            first = _parse(src.readline())
        if first is not None and first.at < now - RETENTION - PURGE_SLACK:
            self.purge(now)

    def purge(self, now: datetime) -> None:
        """Retire les lignes de plus de 90 jours (sous le verrou du fichier, par l'appelant)."""
        if not self.path.is_file():
            return
        kept = [
            line
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip() and (e := _parse(line)) is not None and e.at >= now - RETENTION
        ]
        tmp = self.path.with_name(f"{EVENTS_FILE}.{uuid4().hex}.tmp")
        tmp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        tmp.replace(self.path)
