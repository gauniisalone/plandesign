"""KAN-96 côté poste : l'envoi coupé est réessayé avec la même image (sans recalcul), le
battement continue pendant un envoi lent, et rien ne bat face à un serveur qui ne le demande pas."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from openplan.cli.render_worker.client import ApiBusyError, LeaseLostError
from openplan.cli.render_worker.loop import UPLOAD_FOR_S, Heartbeat, Worker
from openplan.schemas.render_queue import WorkerJob, WorkerResult

JOB = WorkerJob(request_id=uuid4(), lease=uuid4(), created_at=datetime(2026, 10, 26, tzinfo=UTC))
RESULT = WorkerResult(seconds=95.0, vram_peak_mib=0, width=64, height=48, engine="fake")
IMAGE = b"\x89PNG-rendu"


class Uplink:
    """Un faux client : `fails` envois coupés, chacun long de `slow` secondes, puis un envoi
    réussi ; les battements sont comptés avec l'heure."""

    def __init__(self, fails: int = 0, slow: float = 0.0, beat_error: Exception | None = None):
        self.fails = fails
        self.slow = slow
        self.beat_error = beat_error
        self.sent: list[bytes] = []
        self.beats: list[float] = []
        self.uploading: list[tuple[float, float]] = []

    def upload(self, job: WorkerJob, result: WorkerResult, image: bytes) -> None:
        start = time.monotonic()
        time.sleep(self.slow)
        self.uploading.append((start, time.monotonic()))
        self.sent.append(image)
        if len(self.sent) <= self.fails:
            raise ApiBusyError("WriteTimeout")

    def beat(self, job: WorkerJob) -> None:
        if self.beat_error is not None:
            raise self.beat_error
        self.beats.append(time.monotonic())


def _worker(tmp_path: Path, client: object, clock: object = time.monotonic) -> Worker:
    worker = Worker(
        client=client,  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: None,
        say=lambda _text: None,
        sleep=lambda _s: None,
        clock=clock,  # type: ignore[arg-type]
    )
    worker.waits = []  # type: ignore[attr-defined]
    worker.wait = worker.waits.append  # type: ignore[attr-defined, method-assign]
    return worker


def test_a_cut_upload_is_retried_with_the_same_image(tmp_path: Path) -> None:
    """Deux envois coupés, puis le dépôt : la même image trois fois, après 15 puis 30 s (recul
    plafonné à 30 s), et le recul remis à zéro ensuite."""
    uplink = Uplink(fails=2)
    worker = _worker(tmp_path, uplink)
    with Heartbeat(uplink, JOB, None) as heart:  # type: ignore[arg-type]
        assert worker._deliver(JOB, RESULT, IMAGE, heart)
    assert uplink.sent == [IMAGE] * 3
    assert worker.waits == [15.0, 30.0]  # type: ignore[attr-defined]
    assert worker.backoff.busy_failures == 0


def test_the_retries_stop_when_the_claim_is_lost_or_too_old(tmp_path: Path) -> None:
    # La prise n'est plus à ce poste (battement refusé) : on n'insiste pas.
    uplink = Uplink(fails=5, beat_error=LeaseLostError("404"))
    worker = _worker(tmp_path, uplink)
    with Heartbeat(uplink, JOB, 0.01) as heart:  # type: ignore[arg-type]
        assert heart.lost.wait(2.0)
        assert not worker._deliver(JOB, RESULT, IMAGE, heart)
    assert uplink.sent == [IMAGE]
    # Au-delà de UPLOAD_FOR_S, la demande est laissée au bail du serveur.
    now = [0.0]

    def clock() -> float:
        now[0] += UPLOAD_FOR_S / 3
        return now[0]

    uplink = Uplink(fails=10)
    worker = _worker(tmp_path, uplink, clock)
    with Heartbeat(uplink, JOB, None) as heart:  # type: ignore[arg-type]
        assert not worker._deliver(JOB, RESULT, IMAGE, heart)
    assert 1 < len(uplink.sent) < 10


def test_the_heartbeat_goes_on_during_a_slow_upload(tmp_path: Path) -> None:
    """Le lien montant saturé : un envoi de 0,3 s (25,7 s chez augus) ; le battement, dans son
    fil, continue pendant l'envoi, et s'arrête à la fin du bloc."""
    uplink = Uplink(fails=1, slow=0.3)
    worker = _worker(tmp_path, uplink)
    with Heartbeat(uplink, JOB, 0.02) as heart:  # type: ignore[arg-type]
        assert worker._deliver(JOB, RESULT, IMAGE, heart)
    done = time.monotonic()
    for start, end in uplink.uploading:
        assert sum(1 for t in uplink.beats if start <= t <= end) >= 3
    count = len(uplink.beats)
    time.sleep(0.1)
    assert len(uplink.beats) == count and all(t <= done for t in uplink.beats)


def test_a_passing_network_error_does_not_stop_the_heartbeat() -> None:
    errors = iter([ApiBusyError("ConnectError"), ApiBusyError("502")])
    beats: list[float] = []
    seen = threading.Event()

    class Flaky:
        def beat(self, job: WorkerJob) -> None:
            error = next(errors, None)
            if error is not None:
                raise error
            beats.append(time.monotonic())
            seen.set()

    with Heartbeat(Flaky(), JOB, 0.01) as heart:  # type: ignore[arg-type]
        assert seen.wait(2.0)
    assert beats and not heart.lost.is_set()


def test_nothing_beats_against_a_server_that_does_not_ask() -> None:
    uplink = Uplink()
    with Heartbeat(uplink, JOB, None) as heart:  # type: ignore[arg-type]
        time.sleep(0.05)
    assert uplink.beats == [] and heart.beats == 0
