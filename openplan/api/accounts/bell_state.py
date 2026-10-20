"""État « lu » de la cloche avec les comptes (KAN-52) : tables `notification_reads` et
`notification_read_items` ; même interface que le fichier du mode local (`api.bell_state`)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from openplan.api.accounts.db import aware
from openplan.api.accounts.models import NotificationRead, NotificationReadItem
from openplan.pipeline.bell import RETENTION, ReadMarks
from openplan.schemas.common import now_utc


def _uid(user: str) -> UUID | None:
    try:
        return UUID(user)
    except ValueError:
        return None


class SqlReadState:
    def __init__(self, session: Callable[[], Session]) -> None:
        self.session = session

    def marks(self, user: str) -> ReadMarks:
        uid = _uid(user)
        if uid is None:
            return ReadMarks()
        with self.session() as db:
            row = db.get(NotificationRead, uid)
            ids = db.scalars(
                select(NotificationReadItem.event_id).where(NotificationReadItem.user_id == uid)
            )
            return ReadMarks(
                until=aware(row.read_until) if row is not None else None, ids=frozenset(ids)
            )

    def mark(self, user: str, event_id: UUID, project_id: UUID, event_at: datetime) -> None:
        uid = _uid(user)
        if uid is None:
            return
        with self.session() as db, db.begin():
            if db.get(NotificationReadItem, (uid, event_id)) is None:
                db.add(
                    NotificationReadItem(
                        user_id=uid, event_id=event_id, project_id=project_id, event_at=event_at
                    )
                )
            db.execute(
                delete(NotificationReadItem).where(
                    NotificationReadItem.user_id == uid,
                    NotificationReadItem.event_at < now_utc() - RETENTION,
                )
            )

    def mark_all(self, user: str, now: datetime) -> None:
        uid = _uid(user)
        if uid is None:
            return
        with self.session() as db, db.begin():
            row = db.get(NotificationRead, uid)
            if row is None:
                db.add(NotificationRead(user_id=uid, read_until=now))
            else:
                row.read_until = now
            # Tout est lu jusque-là : les lectures une par une plus anciennes ne servent plus.
            db.execute(
                delete(NotificationReadItem).where(
                    NotificationReadItem.user_id == uid, NotificationReadItem.event_at <= now
                )
            )

    def forget_project(self, user: str, project_id: UUID) -> None:
        uid = _uid(user)
        if uid is None:
            return
        with self.session() as db, db.begin():
            db.execute(
                delete(NotificationReadItem).where(
                    NotificationReadItem.user_id == uid,
                    NotificationReadItem.project_id == project_id,
                )
            )
