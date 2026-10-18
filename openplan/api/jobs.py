"""Tâches longues (génération d'image, plusieurs minutes sur processeur) hors de la requête.

Une requête HTTP qui attend des minutes finit coupée par le navigateur (« Failed to fetch »)
et bloque le serveur. Ici la route lance la tâche dans un fil dédié et répond tout de suite
avec un état ; le front interroge `GET .../jobs/{id}` et affiche « génération en cours »,
le périphérique et la durée attendue. Un seul fil : les modèles locaux se partagent la même
mémoire GPU. Registre en mémoire, vidé au redémarrage : les résultats, eux, sont des
artefacts sur disque et restent listés dans les runs du projet.

File d'attente : une tâche lancée pendant qu'une autre calcule n'est plus refusée ; elle
attend (`queued`, avec sa place dans la file) et démarre seule dès que le fil se libère, dans
l'ordre d'arrivée, tous projets et utilisateurs confondus (le fil est partagé). Une tâche en
attente s'annule (`cancel`) ; une tâche déjà lancée, non. Une seconde demande identique
(même utilisateur, même sorte, même clé `dedup`, par exemple le chemin automatique d'un même
plan) remplace celle qui attend encore, à sa place et sous son identifiant : la dernière
demande porte l'intention la plus récente (dernière photo, dernière consigne) et rien n'est
calculé deux fois. Les tâches en attente sont perdues au redémarrage, comme le registre.

Préparation des postes de calcul (lot B3, note 14 § 3.2 et § 7) : les états suivent déjà ceux
de la future table `jobs` (`queued` → `running` → `done` / `failed`, plus `cancelled`) ; il
manquera `claimed` entre `queued` et `running` quand un poste prendra la tâche. Chaque tâche
porte son projet, son utilisateur, sa sorte et sa clé `dedup` : ce sont les colonnes de la
table. Ce qui reste local ici est la charge utile, une fonction Python : B3 la remplacera par
une description sérialisable (projet, vue, options) qu'un poste sait exécuter.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from itertools import count
from threading import Lock
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, JsonValue

from openplan.api.public_errors import public_detail
from openplan.schemas.common import StrictModel

JobState = Literal["queued", "running", "done", "failed", "cancelled"]

MAX_QUEUED_PER_OWNER = 8
"""Tâches en attente au plus par utilisateur : au-delà, refus (429). Protège l'API exposée
d'une file sans fin ; le front n'en met jamais plus de quelques-unes."""


class JobNotFoundError(KeyError):
    """Identifiant de tâche inconnu (ou serveur redémarré depuis)."""


class JobBusyError(RuntimeError):
    """La file d'attente de cet utilisateur est pleine (`MAX_QUEUED_PER_OWNER`)."""

    def __init__(self, message: str, retry_after_s: float) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class JobNotCancellableError(RuntimeError):
    """La tâche a déjà démarré ou fini : seule une tâche en attente s'annule (409)."""


class JobStatus(StrictModel):
    id: UUID
    kind: str
    status: JobState
    device: str
    """Périphérique effectif : cuda ou cpu."""
    expected_s: float | None
    """Durée attendue en secondes, d'après les mesures du protocole ; None si inconnue."""
    elapsed_s: float
    """Temps de calcul depuis le démarrage (0 tant que la tâche attend)."""
    message: str
    """Phrase affichable : ce qui tourne, où, combien de temps."""
    stage: str = ""
    """Étape en cours d'une tâche à étapes (« Recherche des murs… »), sinon vide."""
    remaining_s: float | None = None
    """Temps restant estimé, recalculé à chaque lecture (jamais 0 tant qu'une étape tourne) ;
    None si la tâche ne l'annonce pas."""
    queue_position: int | None = None
    """Place dans la file quand `status` vaut `queued` (1 = la prochaine à démarrer), sinon
    None."""
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
    work: Callable[[], BaseModel]
    seq: int
    """Rang d'arrivée : l'ordre de la file."""
    owner: str = "user"
    stage: _Stage | None = None
    project_id: UUID | None = None
    dedup: Hashable | None = None
    started: float | None = None
    """Heure de démarrage (horloge du registre) ; None tant que la tâche attend."""
    finished: float | None = None
    future: Future[BaseModel] = field(default_factory=Future)

    @property
    def queued(self) -> bool:
        return self.started is None and not self.future.done()


class JobRegistry:
    def __init__(
        self,
        max_workers: int = 1,
        *,
        exposed: bool = False,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="openplan")
        self._workers = max_workers
        self.exposed = exposed
        """API exposée : l'erreur d'une tâche est dite sans chemin du serveur."""
        self._clock = clock
        self._jobs: dict[UUID, _Job] = {}
        self._lock = Lock()
        self._seq = count()

    def running_for(self, owner: str) -> _Job | None:
        """La tâche qui calcule pour cet utilisateur (pas celles qui attendent)."""
        with self._lock:
            for job in self._jobs.values():
                if job.owner == owner and job.started is not None and not job.future.done():
                    return job
        return None

    def queued_for(self, owner: str) -> list[UUID]:
        """Les tâches en attente de cet utilisateur, dans l'ordre de la file."""
        with self._lock:
            waiting = [(j.seq, i) for i, j in self._jobs.items() if j.owner == owner and j.queued]
        return [i for _, i in sorted(waiting)]

    def elapsed(self, job: _Job) -> float:
        if job.started is None:
            return 0.0
        end = job.finished if job.finished is not None else self._clock()
        return max(0.0, end - job.started)

    def _run(self, job: _Job) -> BaseModel:
        """Exécuté par le fil : marque le démarrage, puis la charge utile (lue à cet instant,
        pour qu'une demande identique arrivée pendant l'attente la remplace)."""
        with self._lock:
            if job.started is None:
                job.started = self._clock()
            work = job.work
        try:
            return work()
        finally:
            job.finished = self._clock()

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
        project_id: UUID | None = None,
        dedup: Hashable | None = None,
    ) -> JobStatus:
        """Lance la tâche, ou la met en file si le fil calcule déjà : elle démarrera seule,
        dans l'ordre d'arrivée. `dedup` : une tâche en attente du même utilisateur, de même
        sorte et de même clé est remplacée (même identifiant, même place) au lieu d'empiler
        un second calcul identique. Refus (`JobBusyError`, 429) seulement si la file de cet
        utilisateur est pleine."""
        with self._lock:
            twin_id = (
                next(
                    (
                        i
                        for i, j in self._jobs.items()
                        if j.queued and (j.owner, j.kind, j.dedup) == (owner, kind, dedup)
                    ),
                    None,
                )
                if dedup is not None
                else None
            )
            if twin_id is not None:
                twin = self._jobs[twin_id]
                twin.work, twin.stage = work, stage
                twin.device, twin.expected_s, twin.message = device, expected_s, message
                return self._status(twin_id, twin)
            waiting = sum(1 for j in self._jobs.values() if j.owner == owner and j.queued)
            if waiting >= MAX_QUEUED_PER_OWNER:
                msg = (
                    f"file d'attente pleine : {waiting} calculs attendent déjà. "
                    "Réessayer quand l'un d'eux aura démarré."
                )
                raise JobBusyError(msg, self._ahead_s())
            busy = sum(1 for j in self._jobs.values() if not j.future.done())
            job_id = uuid4()
            job = _Job(
                kind=kind,
                device=device,
                expected_s=expected_s,
                message=message,
                work=work,
                seq=next(self._seq),
                owner=owner,
                stage=stage,
                project_id=project_id,
                dedup=dedup,
                # Fil libre : la tâche démarre tout de suite, elle n'est jamais dite en attente.
                started=self._clock() if busy < self._workers else None,
            )
            self._jobs[job_id] = job
            job.future = self._executor.submit(self._run, job)
            return self._status(job_id, job)

    def submit_staged(
        self,
        kind: str,
        work: Callable[[Callable[..., None]], BaseModel],
        *,
        device: str,
        expected_s: float | None,
        message: str,
        owner: str = "user",
        project_id: UUID | None = None,
        dedup: Hashable | None = None,
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
            project_id=project_id,
            dedup=dedup,
        )

    def cancel(self, job_id: UUID, owner: str) -> JobStatus:
        """Annule une tâche en attente de cet utilisateur ; elle ne démarrera pas."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.owner != owner:
                raise JobNotFoundError(str(job_id))
            if not job.queued or not job.future.cancel():
                msg = "seul un calcul en attente s'annule : celui-ci a déjà démarré"
                raise JobNotCancellableError(msg)
            return self._status(job_id, job)

    def _ahead_s(self) -> float:
        """Attente estimée avant que le fil se libère (pour Retry-After)."""
        total = 0.0
        for job in self._jobs.values():
            if not job.future.done():
                total += max(1.0, (job.expected_s or 60.0) - self.elapsed(job))
        return max(1.0, total)

    def _position(self, job: _Job) -> int:
        return sum(1 for j in self._jobs.values() if j.queued and j.seq <= job.seq)

    def get(self, job_id: UUID) -> JobStatus:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError(str(job_id))
            return self._status(job_id, job)

    def _status(self, job_id: UUID, job: _Job) -> JobStatus:
        """État lisible ; appelé sous le verrou."""
        base = {
            "id": job_id,
            "kind": job.kind,
            "device": job.device,
            "expected_s": job.expected_s,
            "elapsed_s": round(self.elapsed(job), 1),
            "message": job.message,
            "stage": job.stage.text if job.stage is not None else "",
        }
        if job.future.cancelled():
            return JobStatus(status="cancelled", **base)  # type: ignore[arg-type]
        if job.queued:
            base["queue_position"] = self._position(job)
            return JobStatus(status="queued", **base)  # type: ignore[arg-type]
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
