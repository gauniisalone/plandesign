"""KAN-109 L1 : back-office d'augus (politique « admin », comptes actifs seulement).

- `GET /api/admin/audience?days=7|30|90` : audience du site public (voir `audience`) ;
- `GET /api/admin/health` : version déployée, démarrage, poste de rendu, erreurs 5xx et
  latence de l'API sur 24 h, disque.

Les erreurs et la latence se mesurent DANS l'app (seaux de 5 min en mémoire, 24 h glissantes) :
elles repartent de zéro à chaque redémarrage, ce que la page dit. Rien n'est lu du journal
d'accès de l'app (IP complètes), qui n'est pas monté.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request, Response

from openplan import __version__
from openplan.api.access import AdminDep

router = APIRouter(tags=["admin"])

BUCKET_S = 300
WINDOW_S = 24 * 3600
MAX_SAMPLES = 2000
"""Durées gardées par seau (au-delà, les comptes restent justes ; les centiles s'appuient sur
un échantillon)."""
SKIPPED = ("/api/health", "/api/worker/")
"""Sondes et appels du poste : ils ne disent rien de ce que vivent les personnes."""


@dataclass
class _Bucket:
    start: int
    requests: int = 0
    errors_5xx: int = 0
    durations: list[float] = field(default_factory=list)


class ApiMetrics:
    """Requêtes, 5xx et durées de l'API, par seaux de 5 min sur 24 h (en mémoire)."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.started_at = datetime.fromtimestamp(clock(), UTC)
        self._buckets: deque[_Bucket] = deque()
        self._lock = threading.Lock()

    def record(self, status: int, seconds: float) -> None:
        now = int(self.clock())
        start = now - now % BUCKET_S
        with self._lock:
            if not self._buckets or self._buckets[-1].start != start:
                self._buckets.append(_Bucket(start))
            while self._buckets and self._buckets[0].start <= now - WINDOW_S:
                self._buckets.popleft()
            bucket = self._buckets[-1]
            bucket.requests += 1
            if status >= 500:
                bucket.errors_5xx += 1
            if len(bucket.durations) < MAX_SAMPLES:
                bucket.durations.append(seconds)

    def snapshot(self) -> dict[str, object]:
        now = int(self.clock())
        with self._lock:
            live = [b for b in self._buckets if b.start > now - WINDOW_S]
            durations = sorted(d for b in live for d in b.durations)

        def centile(q: float) -> float | None:
            if not durations:
                return None
            return round(durations[min(len(durations) - 1, int(q * len(durations)))] * 1000, 1)

        return {
            "since": self.started_at.isoformat(),
            "requests": sum(b.requests for b in live),
            "errors_5xx": sum(b.errors_5xx for b in live),
            "p50_ms": centile(0.5),
            "p95_ms": centile(0.95),
        }


def install_metrics(app: FastAPI) -> ApiMetrics:
    """Mesure chaque requête de l'API (hors sondes et poste) ; une exception compte en 500."""
    metrics = ApiMetrics()

    @app.middleware("http")
    async def _measure(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/") or path.startswith(SKIPPED):
            return await call_next(request)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            metrics.record(500, time.perf_counter() - start)
            raise
        metrics.record(response.status_code, time.perf_counter() - start)
        return response

    return metrics


def _size(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


PERIODS = (7, 30, 90)


@router.get("/admin/audience")
def admin_audience(
    request: Request,
    admin: AdminDep,
    days: Annotated[int, Query()] = 30,
) -> dict[str, object]:
    """Audience du site public sur `days` jours (7, 30 ou 90 ; agrégats, aucune donnée
    personnelle)."""
    del admin
    if days not in PERIODS:
        raise HTTPException(status_code=422, detail="période : 7, 30 ou 90 jours")
    summary: dict[str, object] = request.app.state.audience.summary(days)
    return summary


@router.get("/admin/health")
def admin_health(request: Request, admin: AdminDep) -> dict[str, object]:
    """Santé technique : version, démarrage, poste, 5xx et latence sur 24 h, disque."""
    del admin
    settings = request.app.state.settings
    data_dir = Path(settings.data_dir)
    disk = shutil.disk_usage(data_dir)
    queue = getattr(request.app.state, "render_queue", None)
    return {
        "version": os.environ.get("OPENPLAN_GIT_SHA", "").strip() or __version__,
        "api": request.app.state.metrics.snapshot(),
        "worker": queue.worker_status() if queue is not None else "offline",
        "disk": {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "data": _size(data_dir),
        },
    }
