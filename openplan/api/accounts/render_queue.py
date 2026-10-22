"""File des rendus réalistes (KAN-89), avec les comptes seulement.

Une demande est créée pour une vue (projet, niveau, caméra) et stockée sans calcul. Le poste de
rendu d'augus la prend par un appel sortant (`claim`, atomique : deux prises simultanées ne
reçoivent jamais la même demande), puis la dépose ou signale un échec avec le bail reçu.

États : `pending` (en attente) → `taken` (pris) → `done` (fait) ou `failed` (échoué) ;
`pending` non pris avant `expires_at` → `expired`. Une demande prise dont le poste ne revient
pas avant `TAKE_TTL` repasse en attente (au plus `MAX_ATTEMPTS` prises), puis échoue.

Journal des rendus (`<data>/renders.jsonl`) : date, résultat, durée, moteur, taille ; ni
compte, ni projet, ni identifiant de demande.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.db import aware, utcnow
from openplan.api.accounts.models import RenderRequest, RenderWorker
from openplan.api.realistic_config import RealisticConfig
from openplan.schemas.render_queue import (
    FAILURE_TEXT,
    RealisticRequest,
    WorkerJob,
    WorkerResult,
    WorkerState,
)

TAKE_TTL = timedelta(minutes=30)
"""Une prise sans dépôt ni échec après ce délai est reprise par la file (poste tombé)."""
MAX_ATTEMPTS = 3
ONLINE_WITHIN = timedelta(seconds=90)
"""Le poste bat toutes les 30 s ; sans battement depuis 90 s, il est « hors ligne »."""
QUOTA_WINDOW = timedelta(hours=24)
WORKER_NAME = "poste"
JOURNAL = "renders.jsonl"


class QueueError(Exception):
    """Refus de la file (l'appelant le traduit en réponse HTTP)."""


class QuotaExceededError(QueueError):
    pass


class LeaseError(QueueError):
    """Demande inconnue, pas prise, ou prise par un autre bail."""


class RenderQueue:
    def __init__(
        self,
        engine: Engine,
        config: RealisticConfig,
        journal_dir: Path | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.engine = engine
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.config = config
        self.clock = clock
        self.journal = None if journal_dir is None else journal_dir / JOURNAL
        self._journal_lock = threading.Lock()

    def session(self) -> Session:
        return self.sessions()

    # --- côté personne -----------------------------------------------------------------

    def used_today(self, user_id: UUID) -> int:
        since = self.clock() - QUOTA_WINDOW
        with self.session() as db:
            count = db.scalar(
                select(func.count())
                .select_from(RenderRequest)
                .where(RenderRequest.user_id == user_id, RenderRequest.created_at > since)
            )
            return int(count or 0)

    def create(
        self, user_id: UUID, project_id: UUID, level_id: UUID, camera_id: UUID
    ) -> RenderRequest:
        """La demande, en attente ; QuotaExceededError au-delà du quota de 24 h."""
        now = self.clock()
        if self.used_today(user_id) >= self.config.daily_quota:
            raise QuotaExceededError
        row = RenderRequest(
            id=uuid4(),
            user_id=user_id,
            project_id=project_id,
            level_id=level_id,
            camera_id=camera_id,
            state="pending",
            created_at=now,
            expires_at=now + timedelta(hours=self.config.expire_h),
            attempts=0,
        )
        with self.session() as db, db.begin():
            db.add(row)
        return row

    def list_for(self, user_id: UUID, project_id: UUID, limit: int = 50) -> list[RenderRequest]:
        """Les demandes de cette personne sur ce projet, les plus récentes d'abord."""
        self.sweep()
        with self.session() as db:
            return list(
                db.scalars(
                    select(RenderRequest)
                    .where(RenderRequest.user_id == user_id, RenderRequest.project_id == project_id)
                    .order_by(RenderRequest.created_at.desc())
                    .limit(limit)
                )
            )

    def get(self, request_id: UUID) -> RenderRequest | None:
        with self.session() as db:
            return db.get(RenderRequest, request_id)

    # --- entretien ---------------------------------------------------------------------

    def sweep(self) -> None:
        """Expire les demandes en attente trop vieilles ; reprend les prises abandonnées."""
        now = self.clock()
        expired: list[RenderRequest] = []
        abandoned: list[RenderRequest] = []
        with self.session() as db, db.begin():
            expired = list(
                db.scalars(
                    select(RenderRequest).where(
                        RenderRequest.state == "pending", RenderRequest.expires_at <= now
                    )
                )
            )
            for row in expired:
                row.state, row.finished_at = "expired", now
            stale = db.scalars(
                select(RenderRequest).where(
                    RenderRequest.state == "taken", RenderRequest.taken_at <= now - TAKE_TTL
                )
            )
            for row in stale:
                row.lease_id = None
                if row.attempts >= MAX_ATTEMPTS:
                    row.state, row.finished_at, row.error = "failed", now, "abandoned"
                    abandoned.append(row)
                elif aware(row.expires_at) <= now:
                    row.state, row.finished_at = "expired", now
                    expired.append(row)
                else:
                    row.state, row.taken_at = "pending", None
        for row in expired:
            self._log("expired", row)
        for row in abandoned:
            self._log("failed", row)

    # --- côté poste --------------------------------------------------------------------

    def claim(self) -> WorkerJob | None:
        """Prend la plus ancienne demande en attente d'un compte autorisé (comparer-et-poser :
        une seule prise réussit, sur SQLite comme sur Postgres)."""
        allowed = list(self.config.accounts)
        if not allowed:
            return None
        self.sweep()
        for _ in range(8):
            now = self.clock()
            with self.session() as db, db.begin():
                candidate = db.execute(
                    select(RenderRequest.id, RenderRequest.created_at)
                    .where(
                        RenderRequest.state == "pending",
                        RenderRequest.expires_at > now,
                        RenderRequest.user_id.in_(allowed),
                    )
                    .order_by(RenderRequest.created_at, RenderRequest.id)
                    .limit(1)
                ).first()
                if candidate is None:
                    return None
                lease = uuid4()
                done = db.execute(
                    update(RenderRequest)
                    .where(RenderRequest.id == candidate.id, RenderRequest.state == "pending")
                    .values(
                        state="taken",
                        taken_at=now,
                        lease_id=lease,
                        attempts=RenderRequest.attempts + 1,
                    )
                )
                if getattr(done, "rowcount", 0) == 1:
                    return WorkerJob(
                        request_id=candidate.id, lease=lease, created_at=aware(candidate.created_at)
                    )
        return None

    def leased(self, request_id: UUID, lease: UUID | None) -> RenderRequest:
        """La demande prise sous ce bail ; LeaseError sinon (même refus dans tous les cas)."""
        row = self.get(request_id)
        if row is None or row.state != "taken" or lease is None or row.lease_id != lease:
            raise LeaseError
        return row

    def complete(self, request_id: UUID, lease: UUID, result: WorkerResult) -> RenderRequest:
        now = self.clock()
        with self.session() as db, db.begin():
            done = db.execute(
                update(RenderRequest)
                .where(
                    RenderRequest.id == request_id,
                    RenderRequest.state == "taken",
                    RenderRequest.lease_id == lease,
                )
                .values(
                    state="done",
                    finished_at=now,
                    lease_id=None,
                    seconds=result.seconds,
                    vram_peak_mib=result.vram_peak_mib,
                    width=result.width,
                    height=result.height,
                    engine=result.engine,
                )
            )
            if getattr(done, "rowcount", 0) != 1:
                raise LeaseError
        row = self.get(request_id)
        assert row is not None
        self._log("done", row)
        return row

    def fail(self, request_id: UUID, lease: UUID, reason: str) -> None:
        now = self.clock()
        with self.session() as db, db.begin():
            done = db.execute(
                update(RenderRequest)
                .where(
                    RenderRequest.id == request_id,
                    RenderRequest.state == "taken",
                    RenderRequest.lease_id == lease,
                )
                .values(state="failed", finished_at=now, lease_id=None, error=reason)
            )
            if getattr(done, "rowcount", 0) != 1:
                raise LeaseError
        row = self.get(request_id)
        if row is not None:
            self._log("failed", row)

    def beat(self, state: WorkerState) -> None:
        now = self.clock()
        with self.session() as db, db.begin():
            row = db.get(RenderWorker, WORKER_NAME)
            if row is None:
                db.add(RenderWorker(name=WORKER_NAME, last_seen_at=now, state=state))
            else:
                row.last_seen_at, row.state = now, state

    def worker_status(self) -> str:
        """`online`, `busy`, `paused` ou `offline` (pas de battement depuis ONLINE_WITHIN)."""
        with self.session() as db:
            row = db.get(RenderWorker, WORKER_NAME)
        if row is None or self.clock() - aware(row.last_seen_at) > ONLINE_WITHIN:
            return "offline"
        if row.state == "paused":
            return "paused"
        return "busy" if row.state == "busy" else "online"

    # --- journal -----------------------------------------------------------------------

    def _log(self, result: str, row: RenderRequest) -> None:
        if self.journal is None:
            return
        entry = {
            "ts": self.clock().isoformat(timespec="seconds"),
            "result": result,
            "seconds": None if row.seconds is None else round(row.seconds, 1),
            "engine": row.engine,
            "width": row.width,
            "height": row.height,
            "attempts": row.attempts,
            "error": row.error,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._journal_lock:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(line)


def public_request(row: RenderRequest, has_image: bool) -> RealisticRequest:
    return RealisticRequest(
        id=row.id,
        level_id=row.level_id,
        camera_id=row.camera_id,
        state=row.state,  # type: ignore[arg-type]
        created_at=aware(row.created_at),
        finished_at=None if row.finished_at is None else aware(row.finished_at),
        expires_at=aware(row.expires_at),
        seconds=row.seconds,
        width=row.width,
        height=row.height,
        error=FAILURE_TEXT.get(row.error or "") if row.state == "failed" else None,
        image=has_image and row.state == "done",
    )
