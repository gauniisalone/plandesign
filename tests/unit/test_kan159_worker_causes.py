"""KAN-159 côté poste : une cause par arrêt du garde-fou (avec la valeur relevée et le seuil, sans
texte), « vram » vers un serveur d'avant, journal du poste qui tourne (14 jours), et la garde
« tests en cours » qui ne compte plus un simple `grep pytest`. Aucun GPU."""

from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from uuid import uuid4

import pytest

from openplan.adapters.neural import zimage
from openplan.adapters.neural.zimage import Stop, ZImageStoppedError, start_check, watch
from openplan.cli.render_worker import checks, engine, journal
from openplan.cli.render_worker.client import WorkerClient
from openplan.cli.render_worker.engine import EngineFailedError
from openplan.cli.render_worker.loop import Worker
from openplan.cli.render_worker.sandbox import BAKE, CAMERA, OUT, PROJECT, RESULT, read_result
from openplan.schemas.render_queue import (
    FAILURE_DETAIL,
    FAILURE_TEXT,
    GUARD_REASONS,
    WorkerClaim,
    WorkerFailure,
    WorkerJob,
)

JOB = WorkerJob(request_id=uuid4(), lease=uuid4(), created_at=datetime(2026, 11, 7, tzinfo=UTC))


@pytest.fixture(autouse=True)
def _own_lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """KAN-162 : aucun test ne lit les verrous réels de la machine (un e2e d'une autre copie
    tenait /tmp/pd-gpu.lock et faisait échouer le garde « tests en cours »)."""
    folder = tmp_path / "verrous"
    folder.mkdir()
    monkeypatch.setenv(checks.LOCK_DIR_ENV, str(folder))
    return folder


class _Proc:
    def __init__(self, polls: int) -> None:
        self.polls = polls

    def poll(self) -> int | None:
        self.polls -= 1
        return None if self.polls >= 0 else 0


def _watch(**kw: object) -> Stop | None:
    defaults: dict[str, object] = {
        "kill": lambda: None,
        "read_vram": lambda: 3000,
        "read_ram": lambda: 8000,
        "sleep": lambda _s: None,
    }
    stop, _ = watch(_Proc(100), **{**defaults, **kw})  # type: ignore[arg-type]
    return stop


# ------------------------------------------------------------------ une cause par arrêt
def test_each_guard_stop_has_its_own_code_value_and_threshold() -> None:
    over = _watch(read_vram=lambda: 5230)
    assert over is not None
    assert (over.code, over.measured, over.threshold) == ("vram_depassee", 5230, 5000)
    blind = _watch(read_vram=lambda: None)
    assert blind is not None
    assert (blind.code, blind.measured, blind.threshold) == ("nvidia_smi_illisible", 3, 3)
    low = _watch(read_ram=lambda: 900)
    assert low is not None and (low.code, low.measured, low.threshold) == ("ram_basse", 900, 1500)
    ticks = iter(range(0, 10_000, 400))
    slow = _watch(clock=lambda: float(next(ticks)))
    assert slow is not None and slow.code == "duree_max"
    assert slow.threshold == 900 and slow.measured is not None and slow.measured > 900


def test_the_start_check_names_a_busy_or_unreadable_card() -> None:
    assert start_check(120) is None
    busy = start_check(812)
    assert busy is not None
    assert (busy.code, busy.measured, busy.threshold) == ("carte_occupee", 812, 500)
    blind = start_check(None)
    assert blind is not None
    assert (blind.code, blind.measured, blind.threshold) == (
        "nvidia_smi_illisible_depart",
        None,
        None,
    )


def test_every_guard_code_is_a_known_failure_with_a_text() -> None:
    for code in GUARD_REASONS:
        assert len(code) <= 32  # colonne `render_requests.error` (String(32)) : pas de migration
        assert FAILURE_TEXT[code].endswith("Ce rendu ne compte pas dans votre quota du jour.")
        assert WorkerFailure(reason=code, measured=1, threshold=2).reason == code  # type: ignore[arg-type]
    assert "vue 3D ouverte ?" in FAILURE_TEXT["carte_occupee"]
    # L'ancien code reste lu, avec sa phrase d'avant.
    assert WorkerFailure(reason="vram").model_dump(exclude_none=True) == {"reason": "vram"}


def _fake_engine(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    fake = types.ModuleType("kan159_fake_zimage")

    def render_scene(*_args: object, **_kwargs: object) -> object:
        raise error

    fake.render_scene = render_scene  # type: ignore[attr-defined]
    fake.ZImageUnavailable = zimage.AdapterUnavailableError  # type: ignore[attr-defined]
    fake.ZImageVramGuard = ZImageStoppedError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kan159_fake_zimage", fake)
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "kan159_fake_zimage")


def test_the_engine_passes_the_cause_and_values_never_the_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = (tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT)
    stop = Stop("carte_occupee", "la carte graphique sert déjà (812 Mio occupés)", 812, 500)
    _fake_engine(monkeypatch, ZImageStoppedError(stop.text, stop))
    with pytest.raises(EngineFailedError) as caught:
        engine.render("zimage", *args)
    assert (caught.value.reason, caught.value.measured, caught.value.threshold) == (
        "carte_occupee",
        812,
        500,
    )
    assert "Mio" not in str(caught.value)  # le texte du garde-fou reste dans le calcul
    # Un garde-fou d'avant KAN-159 (sans cause) : « vram », comme avant.
    _fake_engine(monkeypatch, ZImageStoppedError("arrêté"))
    with pytest.raises(EngineFailedError) as caught:
        engine.render("zimage", *args)
    assert (caught.value.reason, caught.value.measured) == ("vram", None)


def test_the_sandbox_answer_keeps_only_codes_and_integers(tmp_path: Path) -> None:
    def answer(data: object) -> EngineFailedError:
        (tmp_path / RESULT).write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(EngineFailedError) as caught:
            read_result(tmp_path, 1)
        return caught.value

    got = answer({"ok": False, "reason": "ram_basse", "measured": 900, "threshold": 1500})
    assert (got.reason, got.measured, got.threshold) == ("ram_basse", 900, 1500)
    got = answer({"ok": False, "reason": "ram_basse", "measured": "900 Mio", "threshold": True})
    assert (got.reason, got.measured, got.threshold) == ("ram_basse", None, None)
    got = answer({"ok": False, "reason": "vram_depassee", "measured": -1, "threshold": 10**9})
    assert (got.measured, got.threshold) == (None, None)
    assert answer({"ok": False, "reason": "/home/augus"}).reason == "engine_error"
    assert answer(["pas", "un", "objet"]).reason == "engine_error"


# ------------------------------------------------------------------ envoi au serveur
class _Server:
    def __init__(self, served: frozenset[str]) -> None:
        self.served = served
        self.failures: list[WorkerFailure] = []

    def fail(self, job: WorkerJob, failure: WorkerFailure) -> None:
        self.failures.append(failure)


def _worker(tmp_path: Path, client: object, said: list[str], noted: list[str]) -> Worker:
    return Worker(
        client=client,  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: None,
        say=said.append,
        note=noted.append,
        sleep=lambda _s: None,
    )


def test_the_cause_goes_with_its_values_to_a_server_that_serves_it(tmp_path: Path) -> None:
    server = _Server(frozenset({FAILURE_DETAIL}))
    said: list[str] = []
    _worker(tmp_path, server, said, [])._fail(JOB, "vram_depassee", 5230, 5000)
    assert server.failures[-1].model_dump(exclude_none=True) == {
        "reason": "vram_depassee",
        "measured": 5230,
        "threshold": 5000,
    }
    assert said[-1].endswith("échoué : vram_depassee (5230, seuil 5000).")
    # Un code hors du garde-fou part seul, sans valeur.
    _worker(tmp_path, server, [], [])._fail(JOB, "engine_error", 3, 4)
    assert server.failures[-1].model_dump(exclude_none=True) == {"reason": "engine_error"}


def test_an_older_server_still_gets_vram(tmp_path: Path) -> None:
    old = _Server(frozenset({"heartbeat", "style"}))
    _worker(tmp_path, old, [], [])._fail(JOB, "carte_occupee", 812, 500)
    assert old.failures[-1].model_dump(exclude_none=True) == {"reason": "vram"}


def test_the_failure_body_has_no_empty_fields_and_the_claim_still_fits() -> None:
    sent: list[bytes] = []

    class _Http:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, _method: str, _url: str, **kw: object) -> object:
            sent.append(kw["content"])  # type: ignore[arg-type]
            return types.SimpleNamespace(status_code=204, headers={})

    client = WorkerClient("https://app.plandesign.fr", "x" * 40, http=_Http())  # type: ignore[arg-type]
    client.fail(JOB, WorkerFailure(reason="nvidia_smi_illisible_depart"))
    assert json.loads(sent[-1]) == {"reason": "nvidia_smi_illisible_depart"}
    # Un serveur d'avant plafonne `version` à 32 caractères : l'annonce y tient toujours.
    from openplan.cli.render_worker.client import CAPABILITIES

    announced = "+".join(CAPABILITIES)
    assert announced == "heartbeat+style+style-ignore+why"
    assert WorkerClaim(state="idle", version=announced).version == announced


# ------------------------------------------------------------------ journal du poste
def test_the_journal_turns_every_night_and_keeps_14_days(tmp_path: Path) -> None:
    log = journal.open_journal(tmp_path / "etat")
    try:
        path = tmp_path / "etat" / journal.FILE_NAME
        assert (path.stat().st_mode & 0o777) == 0o600
        assert ((tmp_path / "etat").stat().st_mode & 0o777) == 0o700
        (handler,) = log.handlers
        assert isinstance(handler, TimedRotatingFileHandler)
        assert handler.when == "MIDNIGHT" and handler.backupCount == 14
        assert log.propagate is False
        said: list[str] = []
        say, note = journal.teller(log, said.append)
        say("Rendu 1234abcd pris.")
        note("Tour passé : tests en cours (pytest, processus 42).")
        handler.flush()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert said == ["Rendu 1234abcd pris."]  # le terminal ne reçoit que `say`
        assert [line.split(" ", 1)[1] for line in lines] == [
            "Rendu 1234abcd pris.",
            "Tour passé : tests en cours (pytest, processus 42).",
        ]
        assert journal.open_journal(tmp_path / "etat").handlers == [handler]  # un seul fichier
    finally:
        journal.close_journal(log)


def test_every_skipped_turn_is_written_to_the_journal(tmp_path: Path) -> None:
    said: list[str] = []
    noted: list[str] = []
    now = [0.0]
    worker = Worker(
        client=_Server(frozenset()),  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: "tests en cours (verrou pd-e2e.lock pris)",
        say=said.append,
        note=noted.append,
        clock=lambda: now[0],
    )
    for t in (0.0, 12.0, 24.0, 61.0):
        now[0] = t
        assert worker.state() == "waiting"
    line = "Tour passé : tests en cours (verrou pd-e2e.lock pris)."
    # Le terminal (et le journal) une fois par minute ; le journal seul pour les autres tours.
    assert said == [line, line] and noted == [line, line]


# ------------------------------------------------------------------ garde « tests en cours »
@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["grep", "-n", "pytest", "tests/unit/x.py"], None),
        (["rg", "playwright|pytest"], None),
        (["tail", "-f", "/home/augus/pd-journaux/kan159-pytest.log"], None),
        (["bash", "-c", "grep pytest x"], None),
        (["vim", "pytest.ini"], None),
        (["pgrep", "-f", "playwright|pytest"], None),
        (["/home/augus/.venv/bin/python3", "/home/augus/.venv/bin/pytest", "tests"], "pytest"),
        (["python", "-m", "pytest", "-q", "tests/unit"], "pytest"),
        (["nice", "-n", "10", "timeout", "900", "pytest", "-x"], "pytest"),
        (["uv", "run", "pytest"], "pytest"),
        (["python", "-m", "http.server"], None),
        (["npx", "playwright", "test", "e2e/x.spec.ts"], "playwright"),
        (["node", "/w/node_modules/@playwright/test/cli.js", "test"], "playwright"),
        (["npx", "playwright", "install"], None),
        (["node", "/w/node_modules/.bin/vite"], None),
    ],
)
def test_only_real_test_launchers_count(argv: list[str], expected: str | None) -> None:
    assert checks.launches_tests(argv) == expected


def _fake_proc(root: Path, processes: dict[int, list[str]]) -> Path:
    for pid, argv in processes.items():
        (root / str(pid)).mkdir(parents=True)
        (root / str(pid) / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (root / "self").mkdir(exist_ok=True)  # pas un pid
    return root


def test_a_plain_grep_pytest_no_longer_skips_a_turn(tmp_path: Path, _own_lock_dir: Path) -> None:
    """Le défaut constaté : `pgrep -f 'pytest|playwright'` voyait un `grep pytest` (ou un
    `tail` d'un journal de pytest) et le poste passait son tour pour rien."""
    # Le verrou GPU cherché par défaut est celui du dossier temporaire, jamais /tmp.
    assert checks.heavy_lock() == _own_lock_dir / "pd-gpu.lock"
    proc = _fake_proc(
        tmp_path / "proc",
        {
            101: ["grep", "-rn", "pytest", "tests"],
            102: ["tail", "-f", "/home/augus/pd-journaux/pytest.log"],
            103: ["bash", "-c", "ls | grep playwright"],
        },
    )
    free = (tmp_path / "pd-e2e.lock", tmp_path / "pd-pytest-1.lock")
    assert checks.tests_running(free, proc) is None
    assert checks.skip_reason(needs_gpu=False, test_locks=free, proc=proc) is None
    # Un vrai pytest, lui, fait passer le tour.
    _fake_proc(proc, {204: ["python3", "-m", "pytest", "tests/unit"]})
    assert checks.tests_running(free, proc) == "pytest, processus 204"
    assert checks.skip_reason(needs_gpu=False, test_locks=free, proc=proc) == (
        "tests en cours (pytest, processus 204)"
    )


def test_the_worker_does_not_count_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _fake_proc(tmp_path / "proc", {4242: ["python", "-m", "pytest"]})
    monkeypatch.setattr("openplan.cli.render_worker.checks.os.getpid", lambda: 4242)
    assert checks.tests_running((), proc) is None
