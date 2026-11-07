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

import json
import random
import shutil
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import FrameType, TracebackType
from typing import Literal

from openplan.cli.render_worker import checks, etat
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
    STYLE_IGNORED,
    Sandbox,
)
from openplan.schemas.fidelity import FidelityVerdict
from openplan.schemas.render_queue import (
    FAILURE_DETAIL,
    GUARD_REASONS,
    LEGACY_GUARD_REASON,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_SIDE,
    MAX_STYLE_WORDS,
    STYLE,
    STYLE_IGNORE,
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
STYLE_MODEL_MISSING = "SigLIP2 non installé : rendu sans mots."


def style_model_ready() -> bool:
    """KAN-97 : les poids de SigLIP2 sont-ils sur le poste (dossier des poids de Z-Image) ?"""
    from openplan.adapters.neural.siglip import MODEL_ID
    from openplan.adapters.neural.weights import is_installed
    from openplan.pipeline.zimage_render import models_root

    return is_installed(MODEL_ID, models_root())


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


class StateKeeper:
    """KAN-162 : réécrit l'état local pendant un calcul et son envoi (plusieurs minutes), dans un
    fil à part, pour que la garde des agents ne le croie pas périmé (300 s)."""

    def __init__(self, worker: Worker, every: float = etat.REFRESH_S) -> None:
        self.worker = worker
        self.every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> StateKeeper:
        if self.worker.state_file is not None:
            self._thread = threading.Thread(target=self._run, name="etat-poste", daemon=True)
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
        while not self._stop.wait(self.every):
            self.worker.publish(*self.worker.published)


@dataclass
class Worker:
    client: WorkerClient
    engine: str
    pause_flag: Path
    stop_flag: Path
    sandbox: Sandbox = field(default_factory=Sandbox)
    skip: Callable[[], str | checks.Wait | None] | None = None
    say: Callable[[str], None] = print
    note: Callable[[str], None] = lambda _text: None
    """KAN-159 : le journal du poste seul (fichier), pour ce que le terminal tait : chaque tour
    passé, même dans la minute où la raison a déjà été dite."""
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
    _said_no_style: bool = False
    """KAN-97 : « SigLIP2 non installé » n'est dit qu'une fois par lancement du poste."""
    state_file: Path | None = None
    """KAN-162 : l'état local lu par les agents (`etat.STATE_FILE` pour le vrai poste) ; None :
    rien d'écrit (tests)."""
    wall: Callable[[], datetime] = field(default=lambda: datetime.now().astimezone())
    """KAN-162 : l'heure locale, pour « verrou tenu par gpu depuis 14:32 »."""
    _wait: checks.Wait | None = None
    """KAN-162 : la raison du tour passé en cours, dite après la prise (avec le nombre en file)."""
    published: tuple[int | None, bool] = (None, False)
    published_at: float | None = None

    def __post_init__(self) -> None:
        if self.skip is None:
            needs_gpu = self.engine != "fake"
            self.skip = lambda: checks.wait_reason(needs_gpu=needs_gpu)

    # --- état local (KAN-162) -------------------------------------------------------

    def publish(self, en_file: int | None, running: bool) -> None:
        """Réécrit /tmp/pd-poste.etat (atomique, 644) ; jamais d'identifiant de demande."""
        self.published = (en_file, running)
        self.published_at = self.clock()
        if self.state_file is not None:
            etat.write_state(self.state_file, en_file, running)

    def refresh(self) -> None:
        """Réécrit le dernier état s'il date de `etat.REFRESH_S` ou plus (longues attentes,
        calcul) : la garde des agents le tient pour périmé au-delà de 300 s."""
        if self.published_at is None or self.clock() - self.published_at >= etat.REFRESH_S:
            self.publish(*self.published)

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
            self.refresh()  # KAN-162 : un recul de 5 min ne laisse pas l'état périmer
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
            self.publish(None, False)  # KAN-162 : poste arrêté, plus rien en cours
        self.say(f"Poste arrêté ({self.rendered} rendu(s) déposé(s)).")
        return 0

    @staticmethod
    def pace(delay: float) -> float:
        """Retours de la MEP 14 (429 sur `claim`) : l'attente avant le prochain appel, celle
        demandée, jamais moins de POLL_MIN_S, quel que soit le chemin (après un rendu, un
        dépôt, un échec) ; le serveur refuse plus d'un appel par 5 s et par jeton."""
        return max(delay, POLL_MIN_S)

    def state(self, *, tell: bool = True) -> Literal["idle", "paused", "waiting"]:
        """`tell` : dire aussitôt pourquoi le tour est passé ; `cycle` le dit après la prise
        (KAN-162 : avec le nombre de rendus en file que le serveur renvoie)."""
        self._wait = None
        if self.pause_flag.exists():
            return "paused"
        assert self.skip is not None
        reason = self.skip()
        if reason is None:
            self.said_at = None  # la prochaine attente se dira aussitôt
            return "idle"
        self._wait = reason if isinstance(reason, checks.Wait) else checks.Wait(reason)
        if tell:
            self.tell_wait(None)
        return "waiting"

    def wait_line(self, wait: checks.Wait, pending: int | None) -> str:
        """La phrase d'un tour passé. KAN-162 : avec le nombre en file, « En attente : 2 rendu(s)
        en file (verrou tenu par gpu depuis 14:32, banc.py focale). » ; sans (serveur d'avant),
        la phrase d'avant, à l'identique."""
        reason = wait.reason
        if pending is None:
            if reason.startswith("en attente"):
                return f"{reason[0].upper()}{reason[1:]}."
            return f"Tour passé : {reason}."
        if wait.holder is not None:
            detail = wait.holder.text(self.wall())
        else:
            detail = reason.removeprefix("en attente : ")
        return f"En attente : {pending} rendu(s) en file ({detail})."

    def tell_wait(self, pending: int | None) -> None:
        """Une ligne par minute au plus au terminal (le poste passe son tour toutes les 10 à
        15 s) ; chaque tour au journal."""
        if self._wait is None:
            return
        line = self.wait_line(self._wait, pending)
        now = self.clock()
        if self.said_at is None or now - self.said_at >= SAY_EVERY_S:
            self.said_at = now
            self.say(line)  # terminal et journal
        else:
            self.note(line)  # journal seul : chaque tour passé y est écrit

    def after_claim(self, state: str, pending: int | None, *, running: bool = False) -> None:
        """KAN-162 : la raison du tour passé (avec le nombre en file), puis l'état local, réécrit
        à chaque tour quel qu'il soit."""
        if state == "waiting":
            self.tell_wait(pending)
        elif state == "paused" and pending is not None:
            self.note(f"En pause : {pending} rendu(s) en file.")
        self.publish(pending, running)

    def cycle(self) -> float:
        """Un appel `claim` (et le rendu s'il y en a un) ; renvoie l'attente avant le suivant."""
        state = self.state(tell=False)
        try:
            job = self.client.claim(state)
        except ApiRefusedError:
            self.after_claim(state, None)
            delay = self.backoff.refused()
            self.say(f"Jeton refusé par le serveur : nouvel essai dans {delay:.0f} s.")
            return delay
        except (ApiBusyError, LeaseLostError):
            self.after_claim(state, None)
            delay = self.backoff.busy()
            self.say(f"Serveur occupé ou injoignable : nouvel essai dans {delay:.0f} s.")
            return delay
        self.backoff.reset()
        pending: int | None = getattr(self.client, "pending", None)
        self.after_claim(state, pending, running=job is not None)
        if job is not None:
            try:
                return self.process(job)
            finally:
                self.publish(pending, False)
        return self.jitter(POLL_MIN_S, POLL_MAX_S)

    def process(self, job: WorkerJob) -> float:
        work = Path(tempfile.mkdtemp(prefix="plandesign-rendu-"))
        try:
            return self._process(job, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _process(self, job: WorkerJob, work: Path) -> float:
        every = self.beat_every_s or getattr(self.client, "beat_every_s", None)
        with StateKeeper(self), Heartbeat(self.client, job, every) as heart:
            return self._render(job, work, heart)

    def _fetch_style(self, job: WorkerJob, work: Path) -> bool:
        """KAN-97 : les images de style de la demande, si le serveur sert « style » et que le
        poste sait les lire ; vrai quand le poste doit renvoyer ce qu'il a lu (des mots, ou []
        si aucun style net). Sans SigLIP2, rien n'est téléchargé : la carte dit « non lues »."""
        served: frozenset[str] = getattr(self.client, "served", frozenset())
        if not job.style or STYLE not in served:
            return False
        if self.engine == "zimage" and not style_model_ready():
            if not self._said_no_style:
                self.say(STYLE_MODEL_MISSING)
                self._said_no_style = True
            return False
        for index in range(len(job.style)):
            self.client.fetch_style(job, index, work / STYLE_DIR)
        # KAN-130 : les mots désactivés, lus par le calcul (qui n'a pas de réseau).
        if job.style_ignored and STYLE_IGNORE in served:
            (work / STYLE_IGNORED).write_text(json.dumps(job.style_ignored), encoding="utf-8")
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
                # KAN-97 : les mots ne partent que vers un serveur qui sert « style », et
                # seulement si le calcul a lu les images (None : SigLIP2 indisponible).
                # KAN-154 : le verdict de fidélité, seulement drapeau allumé (sinon None, et
                # l'envoi reste celui d'avant pour un serveur qui ne le connaît pas).
                fidelity=(
                    FidelityVerdict.model_validate(outcome.fidelity)
                    if outcome.fidelity is not None
                    else None
                ),
                style_words=(
                    list(outcome.style_words)[:MAX_STYLE_WORDS]
                    if styled and outcome.style_words is not None
                    else None
                ),
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
            return self._fail(job, exc.reason, exc.measured, exc.threshold)
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

    def _fail(
        self,
        job: WorkerJob,
        reason: str,
        measured: int | None = None,
        threshold: int | None = None,
    ) -> float:
        values = ""
        if measured is not None:
            values = f" ({measured}" + ("" if threshold is None else f", seuil {threshold}") + ")"
        self.say(f"Rendu {str(job.request_id)[:8]} échoué : {reason}{values}.")
        failure: dict[str, object] = {"reason": reason}
        served: frozenset[str] = getattr(self.client, "served", frozenset())
        if reason in GUARD_REASONS and FAILURE_DETAIL not in served:
            # KAN-159 : un serveur d'avant ne connaît que « vram » (corps strict).
            failure["reason"] = LEGACY_GUARD_REASON
        elif reason in GUARD_REASONS:
            failure.update(measured=measured, threshold=threshold)
        try:
            self.client.fail(job, WorkerFailure.model_validate(failure))
        except ApiRefusedError:
            return self.backoff.refused()
        except ApiBusyError:
            return self.backoff.busy()
        except (LeaseLostError, InvalidJobError):  # demande déjà reprise ou supprimée
            pass
        return self.jitter(POLL_MIN_S, POLL_MAX_S)
