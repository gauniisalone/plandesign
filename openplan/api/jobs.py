"""Tâches longues (génération d'image, plusieurs minutes sur processeur) hors de la requête.

Une requête HTTP qui attend des minutes finit coupée par le navigateur (« Failed to fetch »)
et bloque le serveur. Ici la route lance la tâche dans un fil dédié et répond tout de suite
avec un état ; le front interroge `GET .../jobs/{id}` et affiche « génération en cours »,
le périphérique et la durée attendue. Un seul fil : les modèles locaux se partagent la même
mémoire GPU. Registre en mémoire, vidé au redémarrage : les résultats, eux, sont des
artefacts sur disque et restent listés dans les runs du projet.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, JsonValue

from openplan.api.public_errors import public_detail
from openplan.schemas.common import StrictModel

JobState = Literal["running", "done", "failed"]


class JobNotFoundError(KeyError):
    """Identifiant de tâche inconnu (ou serveur redémarré depuis)."""


class JobBusyError(RuntimeError):
    """Une tâche lourde du même utilisateur tourne déjà (4.6 : une à la fois par jeton)."""

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class JobStatus(StrictModel):
    id: UUID
    kind: str
    status: JobState
    device: str
    """Périphérique effectif : cuda ou cpu."""
    expected_s: float | None
    """Durée attendue en secondes, d'après les mesures du protocole ; None si inconnue."""
    elapsed_s: float
    message: str
    """Phrase affichable : ce qui tourne, où, combien de temps."""
    stage: str = ""
    """Étape en cours d'une tâche à étapes (« Recherche des murs… »), sinon vide."""
    remaining_s: float | None = None
    """Temps restant estimé, recalculé à chaque lecture (jamais 0 tant qu'une étape tourne) ;
    None si la tâche ne l'annonce pas."""
    result: dict[str, JsonValue] | None = None
    """Résultat de l'étape (modèle Pydantic sérialisé) quand `status` vaut `done`."""
    error: str | None = None


class _Stage:
    """Étape en cours, écrite par le fil de la tâche, lue par les requêtes d'état."""

    def __init__(self) -> None:
        self.text = ""
        self.remaining: Callable[[], float] | None = None

    def set(self, text: str, remaining: Callable[[], float] | None = None) -> None:
        """`remaining` : fonction qui donne le temps restant estimé, appelée à chaque lecture
        de l'état (une tâche à étapes longues l'annonce en direct)."""
        self.text = text
        if remaining is not None:
            self.remaining = remaining


@dataclass
class _Job:
    kind: str
    device: str
    expected_s: float | None
    message: str
    started: float
    future: Future[BaseModel]
    owner: str = "user"
    stage: _Stage | None = None


class JobRegistry:
    def __init__(self, max_workers: int = 1, *, exposed: bool = False) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="openplan")
        self.exposed = exposed
        """API exposée : l'erreur d'une tâche est dite sans chemin du serveur."""
        self._jobs: dict[UUID, _Job] = {}
        self._lock = Lock()

    def running_for(self, owner: str) -> _Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.owner == owner and not job.future.done():
                    return job
        return None

    def submit(
        self,
        kind: str,
        work: Callable[[], BaseModel],
        *,
        device: str,
        expected_s: float | None,
        message: str,
        owner: str = "user",
        stage: _Stage | None = None,
    ) -> JobStatus:
        """Une tâche lourde à la fois par utilisateur (`owner` = acteur du jeton) : la seconde
        est refusée tout de suite (`JobBusyError`, 429) au lieu d'attendre en silence."""
        busy = self.running_for(owner)
        if busy is not None:
            elapsed = time.perf_counter() - busy.started
            remaining = max(1.0, (busy.expected_s or 60.0) - elapsed)
            msg = (
                f"une tâche lourde à la fois : « {busy.kind} » tourne depuis {elapsed:.0f} s "
                f"(environ {remaining:.0f} s restantes). Réessayer ensuite."
            )
            raise JobBusyError(msg, remaining)
        job_id = uuid4()
        job = _Job(
            kind=kind,
            device=device,
            expected_s=expected_s,
            message=message,
            started=time.perf_counter(),
            future=self._executor.submit(work),
            owner=owner,
            stage=stage,
        )
        with self._lock:
            self._jobs[job_id] = job
        return self.get(job_id)

    def submit_staged(
        self,
        kind: str,
        work: Callable[[Callable[..., None]], BaseModel],
        *,
        device: str,
        expected_s: float | None,
        message: str,
        owner: str = "user",
    ) -> JobStatus:
        """Comme `submit`, pour une tâche qui annonce ses étapes : `work` reçoit la
        fonction à appeler avec la phrase de l'étape en cours (`JobStatus.stage`)."""
        stage = _Stage()
        return self.submit(
            kind,
            lambda: work(stage.set),
            device=device,
            expected_s=expected_s,
            message=message,
            owner=owner,
            stage=stage,
        )

    def get(self, job_id: UUID) -> JobStatus:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(str(job_id))
        elapsed = time.perf_counter() - job.started
        base = {
            "id": job_id,
            "kind": job.kind,
            "device": job.device,
            "expected_s": job.expected_s,
            "elapsed_s": round(elapsed, 1),
            "message": job.message,
            "stage": job.stage.text if job.stage is not None else "",
        }
        if not job.future.done():
            remaining = job.stage.remaining if job.stage is not None else None
            if remaining is not None:
                base["remaining_s"] = round(remaining(), 0)
            return JobStatus(status="running", **base)  # type: ignore[arg-type]
        exc = job.future.exception()
        if exc is not None:
            error = public_detail(exc, exposed=self.exposed)
            return JobStatus(status="failed", error=error, **base)  # type: ignore[arg-type]
        result = job.future.result().model_dump(mode="json")
        return JobStatus(status="done", result=result, **base)  # type: ignore[arg-type]

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
