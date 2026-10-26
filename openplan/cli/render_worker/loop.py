"""Boucle du poste : un seul appel périodique (`claim`, toutes les 10 à 15 s avec une gigue),
qui vaut battement de cœur ; un rendu à la fois.

- Avant chaque prise : drapeau de pause, puis garde-fous (`checks`) ; s'ils disent non, le
  poste appelle quand même `claim` en `paused` ou `waiting` (il reste « en ligne ») sans rien
  prendre.
- Temporisation (exigence de l'Infra, pour que fail2ban ne bannisse pas le domicile) :
  401/403 → 1 min, puis 5 min, et 5 min ensuite ; 429, 5xx ou réseau → 15 s, puis 30 s
  (KAN-96 : plafond de 30 s, validé par l'Infra). Jamais de boucle serrée. Un appel réussi
  remet les compteurs à zéro.
- KAN-96 : pendant le calcul et l'envoi, un fil bat pour la prise (si le serveur l'a demandé),
  et l'envoi coupé est réessayé avec la même image, sans recalcul, tant que la prise est à ce
  poste (au plus `UPLOAD_FOR_S`).
- Arrêt : fichier drapeau `worker.stop`, ou Ctrl+C (le calcul en cours est arrêté, la demande
  revient d'elle-même dans la file après le délai de reprise du serveur).
"""

from __future__ import annotations

import random
import shutil
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, TracebackType
from typing import Literal

from openplan.cli.render_worker import checks
from openplan.cli.render_worker.client import (
    ApiBusyError,
    ApiRefusedError,
    InvalidJobError,
    LeaseLostError,
    WorkerClient,
)
from openplan.cli.render_worker.engine import EngineFailedError
from openplan.cli.render_worker.sandbox import (
    BAKE,
    CAMERA,
    OUT,
    PROJECT,
    SEED,
    STYLE_DIR,
    Sandbox,
)
from openplan.schemas.render_queue import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_SIDE,
    MAX_STYLE_WORDS,
    STYLE,
    WorkerFailure,
    WorkerJob,
    WorkerResult,
)

POLL_MIN_S, POLL_MAX_S = 10.0, 15.0
SAY_EVERY_S = 60.0
"""Une ligne au plus par minute pour dire que le poste attend (il passe son tour plus souvent)."""
AUTH_BACKOFF_S = (60.0, 300.0)
BUSY_BASE_S, BUSY_MAX_S = 15.0, 30.0
UPLOAD_FOR_S = 600.0
"""KAN-96 : un envoi coupé est réessayé pendant 10 min au plus ; au-delà, la demande revient
d'elle-même dans la file (bail du serveur)."""


@dataclass
class Backoff:
    auth_failures: int = 0
    busy_failures: int = 0

    def refused(self) -> float:
        delay = AUTH_BACKOFF_S[min(self.auth_failures, len(AUTH_BACKOFF_S) - 1)]
        self.auth_failures += 1
        return delay

    def busy(self) -> float:
        delay = min(BUSY_MAX_S, BUSY_BASE_S * float(2**self.busy_failures))
        self.busy_failures = min(self.busy_failures + 1, 16)
        return delay

    def reset(self) -> None:
        self.auth_failures = self.busy_failures = 0


class Heartbeat:
    """KAN-96 : bat pour une prise toutes les `every` secondes, dans un fil à part, le temps du
    bloc (calcul et envoi). Une erreur passagère est ignorée (le battement suivant réessaie) ;
    une prise perdue (404) est notée dans `lost`. `every` None : le serveur ne tient pas le bail,
    rien ne bat."""

    def __init__(self, client: WorkerClient, job: WorkerJob, every: float | None) -> None:
        self.client = client
        self.job = job
        self.every = every
        self.lost = threading.Event()
        self.beats = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Heartbeat:
        if self.every is not None:
            self._thread = threading.Thread(target=self._run, name="battement", daemon=True)
            self._thread.start()
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _trace: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        assert self.every is not None
        while not self._stop.wait(self.every):
            try:
                self.client.beat(self.job)
            except LeaseLostError:
                self.lost.set()
                return
            except (ApiBusyError, ApiRefusedError, InvalidJobError):
                continue
            self.beats += 1


@dataclass
class Worker:
    client: WorkerClient
    engine: str
    pause_flag: Path
    stop_flag: Path
    sandbox: Sandbox = field(default_factory=Sandbox)
    skip: Callable[[], str | None] | None = None
    say: Callable[[str], None] = print
    sleep: Callable[[float], None] = time.sleep
    jitter: Callable[[float, float], float] = random.uniform
    backoff: Backoff = field(default_factory=Backoff)
    clock: Callable[[], float] = time.monotonic
    stopping: bool = False
    rendered: int = 0
    said_at: float | None = None
    """Quand le poste a dit pour la dernière fois pourquoi il passe son tour."""
    beat_every_s: float | None = None
    """Cadence du battement imposée (tests) ; sinon celle annoncée par le serveur."""

    def __post_init__(self) -> None:
        if self.skip is None:
            needs_gpu = self.engine != "fake"
            self.skip = lambda: checks.skip_reason(needs_gpu=needs_gpu)

    # --- arrêt ----------------------------------------------------------------------

    def interrupt(self, _signum: int = 0, _frame: FrameType | None = None) -> None:
        if not self.stopping:
            self.say("Arrêt demandé : le calcul en cours s'arrête, puis le poste s'arrête.")
        self.stopping = True
        self.sandbox.kill()

    def should_stop(self) -> bool:
        return self.stopping or self.stop_flag.exists()

    def wait(self, seconds: float) -> None:
        """Attente découpée en pas d'une seconde : un arrêt est pris en compte aussitôt."""
        end = time.monotonic() + seconds
        while not self.should_stop():
            left = end - time.monotonic()
            if left <= 0:
                return
            self.sleep(min(1.0, left))

    # --- boucle ---------------------------------------------------------------------

    def run(self, *, once: bool = False) -> int:
        previous = signal.signal(signal.SIGINT, self.interrupt)
        previous_term = signal.signal(signal.SIGTERM, self.interrupt)
        try:
            while not self.should_stop():
                delay = self.cycle()
                if once:
                    break
                self.wait(self.pace(delay))
        finally:
            signal.signal(signal.SIGINT, previous)
            signal.signal(signal.SIGTERM, previous_term)
        self.say(f"Poste arrêté ({self.rendered} rendu(s) déposé(s)).")
        return 0

    @staticmethod
    def pace(delay: float) -> float:
        """Retours de la MEP 14 (429 sur `claim`) : l'attente avant le prochain appel, celle
        demandée, jamais moins de POLL_MIN_S, quel que soit le chemin (après un rendu, un
        dépôt, un échec) ; le serveur refuse plus d'un appel par 5 s et par jeton."""
        return max(delay, POLL_MIN_S)

    def state(self) -> Literal["idle", "paused", "waiting"]:
        if self.pause_flag.exists():
            return "paused"
        assert self.skip is not None
        reason = self.skip()
        if reason is None:
            self.said_at = None  # la prochaine attente se dira aussitôt
            return "idle"
        # Une ligne par minute au plus (le poste passe son tour toutes les 10 à 15 s).
        now = self.clock()
        if self.said_at is None or now - self.said_at >= SAY_EVERY_S:
            self.said_at = now
            if reason.startswith("en attente"):
                self.say(f"{reason[0].upper()}{reason[1:]}.")
            else:
                self.say(f"Tour passé : {reason}.")
        return "waiting"

    def cycle(self) -> float:
        """Un appel `claim` (et le rendu s'il y en a un) ; renvoie l'attente avant le suivant."""
        try:
            job = self.client.claim(self.state())
        except ApiRefusedError:
            delay = self.backoff.refused()
            self.say(f"Jeton refusé par le serveur : nouvel essai dans {delay:.0f} s.")
            return delay
        except (ApiBusyError, LeaseLostError):
            delay = self.backoff.busy()
            self.say(f"Serveur occupé ou injoignable : nouvel essai dans {delay:.0f} s.")
            return delay
        self.backoff.reset()
        if job is not None:
            return self.process(job)
        return self.jitter(POLL_MIN_S, POLL_MAX_S)

    def process(self, job: WorkerJob) -> float:
        work = Path(tempfile.mkdtemp(prefix="plandesign-rendu-"))
        try:
            return self._process(job, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _process(self, job: WorkerJob, work: Path) -> float:
        every = self.beat_every_s or getattr(self.client, "beat_every_s", None)
        with Heartbeat(self.client, job, every) as heart:
            return self._render(job, work, heart)

    def _fetch_style(self, job: WorkerJob, work: Path) -> bool:
        """KAN-97 : les images de style de la demande, si le serveur sert « style » ; vrai
        quand le poste doit renvoyer des mots (même aucun : « [] » dit qu'il a lu)."""
        served: frozenset[str] = getattr(self.client, "served", frozenset())
        if not job.style or STYLE not in served:
            return False
        for index in range(len(job.style)):
            self.client.fetch_style(job, index, work / STYLE_DIR)
        return True

    def _render(self, job: WorkerJob, work: Path, heart: Heartbeat) -> float:  # noqa: PLR0911 - une sortie par issue
        short = str(job.request_id)[:8]
        self.say(f"Rendu {short} pris.")
        try:
            for name, target in (("project", PROJECT), ("bake", BAKE), ("camera", CAMERA)):
                self.client.fetch(job, name, work / target)  # type: ignore[arg-type]
            if job.seed is not None:  # KAN-91 : variante, autre tirage
                (work / SEED).write_text(str(job.seed), encoding="utf-8")
            styled = self._fetch_style(job, work)
            outcome = self.sandbox.run(work, self.engine)
            image = (work / OUT).read_bytes()
            if len(image) > MAX_IMAGE_BYTES or max(outcome.width, outcome.height) > MAX_IMAGE_SIDE:
                raise EngineFailedError("engine_error")
            if self.stopping:
                return 0.0
            result = WorkerResult(
                seconds=round(outcome.seconds, 2),
                vram_peak_mib=outcome.vram_peak_mib,
                width=outcome.width,
                height=outcome.height,
                engine=outcome.engine,
                # KAN-97 : les mots ne partent que vers un serveur qui sert « style ».
                style_words=list(outcome.style_words or [])[:MAX_STYLE_WORDS] if styled else None,
            )
            if not self._deliver(job, result, image, heart):
                self.say(
                    f"Rendu {short} : envoi impossible pour l'instant ; la demande reviendra "
                    "dans la file."
                )
                return self.backoff.busy()
        except InvalidJobError:
            return self._fail(job, "invalid_input")
        except EngineFailedError as exc:
            if self.stopping:  # Ctrl+C : la demande reviendra dans la file
                return 0.0
            return self._fail(job, exc.reason)
        except OSError:
            return self._fail(job, "engine_error")
        except ApiRefusedError:
            return self.backoff.refused()
        except ApiBusyError:
            return self.backoff.busy()
        except LeaseLostError:
            self.say(f"Rendu {short} : la demande n'est plus à ce poste.")
            return self.jitter(POLL_MIN_S, POLL_MAX_S)
        self.rendered += 1
        self.say(f"Rendu {short} déposé ({outcome.seconds:.0f} s).")
        return self.jitter(POLL_MIN_S, POLL_MAX_S)

    def _deliver(
        self, job: WorkerJob, result: WorkerResult, image: bytes, heart: Heartbeat
    ) -> bool:
        """KAN-96 : dépose l'image ; un envoi coupé (réseau, 5xx, 429) est réessayé avec la même
        image, sans recalcul, après le recul plafonné, tant que la prise est à ce poste et au
        plus `UPLOAD_FOR_S`. Le serveur reconnaît un second dépôt du même rendu. False : on
        laisse la demande au bail du serveur."""
        short = str(job.request_id)[:8]
        deadline = self.clock() + UPLOAD_FOR_S
        while True:
            try:
                self.client.upload(job, result, image)
            except ApiBusyError:
                if heart.lost.is_set() or self.should_stop() or self.clock() >= deadline:
                    return False
                delay = self.backoff.busy()
                self.say(f"Envoi du rendu {short} coupé : nouvel essai dans {delay:.0f} s.")
                self.wait(delay)
                continue
            self.backoff.reset()
            return True

    def _fail(self, job: WorkerJob, reason: str) -> float:
        self.say(f"Rendu {str(job.request_id)[:8]} échoué : {reason}.")
        try:
            self.client.fail(job, WorkerFailure.model_validate({"reason": reason}))
        except ApiRefusedError:
            return self.backoff.refused()
        except ApiBusyError:
            return self.backoff.busy()
        except (LeaseLostError, InvalidJobError):  # demande déjà reprise ou supprimée
            pass
        return self.jitter(POLL_MIN_S, POLL_MAX_S)
