"""État « lu » de la cloche (KAN-52) : un repère « tout lu jusqu'à » par personne, et les lignes
lues une par une (comme GitHub ou Linear, sans toucher aux plus anciennes). Même interface en
local (fichier `notifications.json` du dossier des données) et avec les comptes (tables
`notification_reads` et `notification_read_items`, `accounts.bell_state`), donc le même
comportement visible ; avec les comptes, l'état suit la personne sur tous ses appareils."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from openplan.pipeline.bell import RETENTION, ReadMarks
from openplan.schemas.common import now_utc

STATE_FILE = "notifications.json"


class ReadState(Protocol):
    def marks(self, user: str) -> ReadMarks: ...

    def mark(self, user: str, event_id: UUID, project_id: UUID, event_at: datetime) -> None: ...

    def mark_all(self, user: str, now: datetime) -> None: ...

    def forget_project(self, user: str, project_id: UUID) -> None: ...


class FileReadState:
    """Mode local : un seul fichier, sous un verrou ; purge des lectures de plus de 90 jours à
    chaque écriture."""

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / STATE_FILE
        self._lock = threading.Lock()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"users": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {"users": {}}
        return (
            data
            if isinstance(data, dict) and isinstance(data.get("users"), dict)
            else {"users": {}}
        )

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{STATE_FILE}.{uuid4().hex}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _user(data: dict[str, Any], user: str) -> dict[str, Any]:
        entry = data["users"].setdefault(user, {})
        entry.setdefault("until", None)
        entry.setdefault("items", {})
        return entry  # type: ignore[no-any-return]

    def marks(self, user: str) -> ReadMarks:
        with self._lock:
            entry = self._load()["users"].get(user) or {}
        until = entry.get("until")
        return ReadMarks(
            until=datetime.fromisoformat(until) if until else None,
            ids=frozenset(UUID(i) for i in (entry.get("items") or {})),
        )

    def mark(self, user: str, event_id: UUID, project_id: UUID, event_at: datetime) -> None:
        with self._lock:
            data = self._load()
            entry = self._user(data, user)
            entry["items"][str(event_id)] = {
                "project_id": str(project_id),
                "at": event_at.isoformat(),
            }
            self._purge(entry, now_utc())
            self._save(data)

    def mark_all(self, user: str, now: datetime) -> None:
        with self._lock:
            data = self._load()
            entry = self._user(data, user)
            entry["until"] = now.isoformat()
            # Tout est lu jusque-là : les lectures une par une plus anciennes ne servent plus.
            entry["items"] = {
                k: v for k, v in entry["items"].items() if datetime.fromisoformat(v["at"]) > now
            }
            self._save(data)

    def forget_project(self, user: str, project_id: UUID) -> None:
        with self._lock:
            data = self._load()
            entry = self._user(data, user)
            entry["items"] = {
                k: v for k, v in entry["items"].items() if v.get("project_id") != str(project_id)
            }
            self._save(data)

    @staticmethod
    def _purge(entry: dict[str, Any], now: datetime) -> None:
        oldest = now - RETENTION
        entry["items"] = {
            k: v for k, v in entry["items"].items() if datetime.fromisoformat(v["at"]) >= oldest
        }
