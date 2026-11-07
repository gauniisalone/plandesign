"""KAN-162 : le poste sait qu'un rendu attend en file, le dit au journal (avec qui tient le
verrou), et écrit un état local que les agents lisent pour céder la place (`/tmp/pd-poste.etat`).
Aucun GPU, aucun réseau : un faux serveur et un faux /proc."""

from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest

from openplan.cli.render_worker import checks, etat
from openplan.cli.render_worker.client import ApiBusyError, ApiRefusedError, WorkerClient
from openplan.cli.render_worker.loop import StateKeeper, Worker
from openplan.schemas.render_queue import PENDING_HEADER, WorkerJob, parse_pending

NOW = datetime(2026, 11, 7, 14, 42, 0)
KEYS = {"en_file", "rendu_en_cours", "horodatage", "version"}
JOB = WorkerJob.model_validate(
    {
        "request_id": "12345678-1234-4234-8234-123456789abc",
        "lease": "87654321-4321-4321-8321-cba987654321",
        "created_at": "2026-11-07T12:00:00+00:00",
    }
)


@pytest.fixture(autouse=True)
def _own_lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Aucun test ne lit les verrous réels de la machine (`PD_LOCK_DIR` temporaire)."""
    folder = tmp_path / "verrous"
    folder.mkdir()
    monkeypatch.setenv(checks.LOCK_DIR_ENV, str(folder))
    return folder


class StubClient:
    """Le client vu par la boucle : des réponses à `claim`, et `pending` comme le vrai client
    (None : serveur d'avant KAN-162)."""

    def __init__(self, answers: list[object], pending: list[int | None]) -> None:
        self.answers = answers
        self.pendings = pending
        self.pending: int | None = None
        self.states: list[str] = []

    def claim(self, state: str) -> WorkerJob | None:
        self.states.append(state)
        self.pending = self.pendings.pop(0) if self.pendings else None
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


def _worker(
    tmp_path: Path,
    client: object,
    skip: object = None,
    said: list[str] | None = None,
    noted: list[str] | None = None,
    clock: list[float] | None = None,
) -> Worker:
    now = clock if clock is not None else [0.0]
    return Worker(
        client=client,  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: skip,  # type: ignore[arg-type,return-value]
        say=(said if said is not None else []).append,
        note=(noted if noted is not None else []).append,
        sleep=lambda _s: None,
        jitter=lambda low, high: (low + high) / 2,
        clock=lambda: now[0],
        state_file=tmp_path / "pd-poste.etat",
        wall=lambda: NOW,
    )


def _state(tmp_path: Path) -> dict[str, object]:
    data: dict[str, object] = json.loads((tmp_path / "pd-poste.etat").read_text("utf-8"))
    return data


# ------------------------------------------------------------------ lecture de l'en-tête
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0", 0),
        ("3", 3),
        (" 12 ", 12),
        (None, None),
        ("", None),
        ("-1", None),
        ("1e3", None),
        ("deux", None),
        ("٣", None),  # chiffre non ASCII
        ("9999999", None),  # au-delà du plafond
    ],
)
def test_the_pending_header_is_a_plain_bounded_integer(text: str | None, expected: object) -> None:
    assert parse_pending(text) == expected


def test_the_client_reads_the_count_and_an_older_server_leaves_it_unknown() -> None:
    answers: list[dict[str, str]] = [{PENDING_HEADER: "2"}, {}]

    class _Http:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}

        def request(self, _method: str, _url: str, **_kw: object) -> object:
            return types.SimpleNamespace(status_code=204, headers=answers.pop(0))

    client = WorkerClient("https://app.plandesign.fr", "x" * 40, http=_Http())  # type: ignore[arg-type]
    assert client.claim("waiting") is None and client.pending == 2
    assert client.claim("waiting") is None and client.pending is None


# ------------------------------------------------------------------ journal
def test_the_wait_is_written_with_the_queue_and_the_lock_holder(tmp_path: Path) -> None:
    holder = checks.Holder(4321, "pytest test_x.py", 600.0, "pytest-1")
    wait = checks.Wait("tests en cours (verrou pd-pytest-1.lock pris)", holder)
    said: list[str] = []
    noted: list[str] = []
    clock = [0.0]
    client = StubClient([None, None, None], [2, 2, 0])
    worker = _worker(tmp_path, client, wait, said, noted, clock)
    worker.cycle()
    clock[0] = 12.0
    worker.cycle()
    line = (
        "En attente : 2 rendu(s) en file (verrou tenu par pytest-1 depuis 14:32, pytest test_x.py)."
    )
    # Le terminal une fois par minute, le journal à chaque tour (KAN-159).
    assert said == [line] and noted == [line]
    clock[0] = 24.0
    worker.cycle()
    assert noted[-1] == line.replace("2 rendu(s)", "0 rendu(s)")
    # Sans détenteur (GPU occupé, lanceur vu dans /proc) : la raison elle-même.
    assert worker.wait_line(checks.Wait("GPU déjà occupé (812 Mio)"), 1) == (
        "En attente : 1 rendu(s) en file (GPU déjà occupé (812 Mio))."
    )
    gone = checks.Wait(checks.waiting_text(None))
    assert worker.wait_line(gone, 1) == (
        "En attente : 1 rendu(s) en file (un travail lourd tient le verrou, le rendu partira "
        "dès qu'il sera fini)."
    )
    # Détenteur sans durée ni commande lisibles : la ressource seule.
    bare = checks.Wait("x", checks.Holder(1, None, None, "gpu"))
    assert worker.wait_line(bare, 3) == "En attente : 3 rendu(s) en file (verrou tenu par gpu)."


def test_an_older_server_keeps_the_lines_and_the_state_says_unknown(tmp_path: Path) -> None:
    said: list[str] = []
    client = StubClient([None, None], [None, None])
    worker = _worker(tmp_path, client, "tests en cours (verrou pd-e2e.lock pris)", said)
    worker.cycle()
    assert said == ["Tour passé : tests en cours (verrou pd-e2e.lock pris)."]  # comme avant
    assert _state(tmp_path)["en_file"] is None
    assert _state(tmp_path)["rendu_en_cours"] is False
    # Un poste libre vers un serveur d'avant : rien de dit, l'état écrit quand même.
    _worker(tmp_path, client, None, said).cycle()
    assert len(said) == 1 and _state(tmp_path)["en_file"] is None


def test_paused_turns_note_the_queue(tmp_path: Path) -> None:
    (tmp_path / "worker.pause").touch()
    noted: list[str] = []
    _worker(tmp_path, StubClient([None], [4]), None, None, noted).cycle()
    assert noted == ["En pause : 4 rendu(s) en file."]
    assert _state(tmp_path)["en_file"] == 4


# ------------------------------------------------------------------ état local : contenu
def test_the_state_holds_four_keys_and_no_identifier(tmp_path: Path) -> None:
    seen: list[dict[str, object]] = []
    client = StubClient([JOB], [5])
    worker = _worker(tmp_path, client)

    def process(job: WorkerJob) -> float:
        seen.append(_state(tmp_path))
        return 0.0

    worker.process = process  # type: ignore[method-assign]
    worker.cycle()
    during, after = seen[0], _state(tmp_path)
    assert set(during) == set(after) == KEYS
    assert during["rendu_en_cours"] is True and during["en_file"] == 5
    assert after["rendu_en_cours"] is False and after["version"] == etat.FORMAT_VERSION
    text = (tmp_path / "pd-poste.etat").read_text("utf-8")
    assert str(JOB.request_id) not in text and str(JOB.request_id)[:8] not in text
    assert str(JOB.lease) not in text
    stamp = datetime.fromisoformat(str(after["horodatage"]))
    assert stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert abs((datetime.now(UTC) - stamp).total_seconds()) < 60
    # Le poste arrêté : plus rien en cours, nombre inconnu.
    _worker(tmp_path, StubClient([None], [1])).run(once=True)
    assert _state(tmp_path)["en_file"] is None and _state(tmp_path)["rendu_en_cours"] is False


def test_a_test_worker_without_state_file_writes_nothing(tmp_path: Path) -> None:
    worker = Worker(
        client=StubClient([None], [3]),  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: None,
        say=lambda _t: None,
    )
    assert worker.state_file is None
    worker.cycle()
    assert worker.published == (3, False)
    assert Path("/tmp/pd-poste.etat") == etat.STATE_FILE


# ------------------------------------------------------------------ état local : chaque tour
def test_the_state_is_rewritten_at_every_turn(tmp_path: Path) -> None:
    """Contrat de l'Infra : au-delà de 300 s, la garde des agents croit l'état périmé. Chaque
    tour le réécrit : pris, sauté (en attente), en pause, en erreur."""
    path = tmp_path / "pd-poste.etat"
    client = StubClient(
        [None, None, ApiBusyError("502"), ApiRefusedError("401"), None, None], [0, 1, 1, 1, 1, 2]
    )
    turns = [
        ("idle", None),
        ("waiting", "GPU déjà occupé (812 Mio)"),
        ("idle", None),  # serveur injoignable
        ("idle", None),  # jeton refusé
        ("paused", None),
        ("idle", None),
    ]
    for expected, reason in turns:
        if expected == "paused":
            (tmp_path / "worker.pause").touch()
        path.unlink(missing_ok=True)
        _worker(tmp_path, client, reason).cycle()
        (tmp_path / "worker.pause").unlink(missing_ok=True)
        assert path.exists(), expected
        assert set(_state(tmp_path)) == KEYS
    assert client.states == ["idle", "waiting", "idle", "idle", "paused", "idle"]
    assert _state(tmp_path)["en_file"] == 2


def test_long_waits_and_renders_refresh_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un recul de 5 min (jeton refusé) ou un calcul de 15 min ne laissent pas l'état périmer."""
    path = tmp_path / "pd-poste.etat"
    clock = [0.0]
    worker = _worker(tmp_path, StubClient([], []), clock=clock)
    worker.publish(2, False)
    writes: list[float] = []
    real = worker.publish

    def counted(en_file: int | None, running: bool) -> None:
        writes.append(clock[0])
        real(en_file, running)

    def tick(seconds: float) -> None:
        clock[0] += seconds

    worker.publish = counted  # type: ignore[method-assign]
    worker.sleep = tick
    # `wait` compte en temps monotone : il avance ici avec l'horloge de test (5 min en un instant).
    with monkeypatch.context() as patch:
        patch.setattr("openplan.cli.render_worker.loop.time.monotonic", lambda: clock[0])
        worker.wait(300.0)
    assert clock[0] == 300.0
    assert len(writes) >= 4
    assert all(
        b - a <= etat.REFRESH_S for a, b in zip([0.0, *writes], [*writes, 300.0], strict=True)
    )
    assert etat.REFRESH_S < 300 and _state(tmp_path)["en_file"] == 2
    # Pendant un calcul : un fil réécrit l'état (rendu en cours) sans attendre la boucle.
    path.unlink()
    worker.publish = real  # type: ignore[method-assign]
    worker.publish(1, True)
    path.unlink()
    with StateKeeper(worker, every=0.02):
        deadline = time.monotonic() + 5
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
    assert _state(tmp_path)["rendu_en_cours"] is True and _state(tmp_path)["en_file"] == 1


# ------------------------------------------------------------------ état local : atomique
def test_the_state_is_written_atomically_in_the_same_folder_644(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "pd-poste.etat"
    old_umask = os.umask(0o077)  # même un umask fermé donne 644
    try:
        assert etat.write_state(path, 1, False)
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    replaced: list[tuple[Path, Path, int, str]] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        source = Path(src)
        replaced.append(
            (source.parent, Path(dst), stat.S_IMODE(source.stat().st_mode), source.read_text())
        )
        real_replace(src, dst)

    monkeypatch.setattr("openplan.cli.render_worker.etat.os.replace", spy)
    assert etat.write_state(path, 3, True)
    ((folder, dst, mode, content),) = replaced
    assert folder == tmp_path and dst == path and mode == 0o644
    assert json.loads(content)["en_file"] == 3  # complet avant d'être mis en place
    # Un remplacement raté : l'ancien état reste entier, aucun temporaire ne traîne.
    before = path.read_text()

    def broken(_src: str, _dst: str) -> None:
        raise OSError("disque plein")

    monkeypatch.setattr("openplan.cli.render_worker.etat.os.replace", broken)
    assert etat.write_state(path, 9, False) is False
    assert path.read_text() == before
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["pd-poste.etat"]


def test_readers_never_see_a_partial_state(tmp_path: Path) -> None:
    path = tmp_path / "pd-poste.etat"
    etat.write_state(path, 0, False)
    bad: list[str] = []
    stop = threading.Event()

    def read() -> None:
        while not stop.is_set():
            text = path.read_text("utf-8")
            try:
                assert set(json.loads(text)) == KEYS
            except (ValueError, AssertionError):
                bad.append(text)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        for n in range(300):
            etat.write_state(path, n, n % 2 == 0)
    finally:
        stop.set()
        reader.join()
    assert bad == []


# ------------------------------------------------------------------ qui tient le verrou
@pytest.mark.parametrize(
    ("name", "resource"),
    [
        ("pd-gpu.lock", "gpu"),
        ("pd-e2e.lock", "e2e"),
        ("pd-pytest-1.lock", "pytest-1"),
        ("pd-pytest-2.lock", "pytest-2"),
        ("pd-lourd.lock", "lourd"),
        ("autre.lock", "autre.lock"),
    ],
)
def test_the_resource_comes_from_the_lock_name(name: str, resource: str) -> None:
    assert checks.resource_name(Path("/tmp") / name) == resource


def test_lock_holder_gives_the_resource_and_a_short_command(tmp_path: Path) -> None:
    lock = tmp_path / "pd-pytest-1.lock"
    lock.touch()
    st = lock.stat()
    dev = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    proc = tmp_path / "proc"
    (proc / "777").mkdir(parents=True)
    (proc / "locks").write_text(f"1: FLOCK  ADVISORY  WRITE 777 {dev} 0 EOF\n")
    argv = [
        "/bin/bash", "/home/augus/bin/pd-lock", "pytest", "--", "uv", "run", "--extra", "api",
        "--extra", "vision", "pytest",
        "/home/augus/ai-agent-workspace/projects/pd-wt/KAN-162/tests/unit/test_kan162_poste.py",
        "-k", "atomic or pending", "-q",
    ]  # fmt: skip
    (proc / "777" / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    tick = os.sysconf("SC_CLK_TCK")
    fields = ["S"] + ["0"] * 18 + [str(1000 * tick)] + ["0"] * 10
    (proc / "777" / "stat").write_text(f"777 (bash) {' '.join(fields)}\n")
    (proc / "uptime").write_text("1600.00 9000.00\n")  # depuis 10 min
    holder = checks.lock_holder(lock, proc)
    assert holder is not None and holder.resource == "pytest-1"
    assert holder.command == "pytest test_kan162_poste.py"
    assert "ai-agent-workspace" not in holder.text(NOW) and "atomic" not in holder.text(NOW)
    assert holder.text(NOW) == "verrou tenu par pytest-1 depuis 14:32, pytest test_kan162_poste.py"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["bash", "/b/pd-lock", "e2e", "--", "npx", "playwright", "test", "--workers", "2"],
         "playwright test"),
        (["bash", "/b/pd-lock", "gpu", "--", "uv", "run", "python",
          "/home/a/pd-wt/KAN-136/kan136p/banc.py", "--prompt", "a very long prompt"], "banc.py"),
        (["bash", "-c", "cd /home/a/x && uv run pytest tests/unit -q"], "travail lourd"),
        (["python", "-m", "pytest", "tests/unit/test_a.py"], "pytest test_a.py"),
        (["pytest", "x" * 60], "pytest"),
    ],
)  # fmt: skip
def test_short_command_drops_long_arguments_and_project_paths(
    argv: list[str], expected: str
) -> None:
    assert checks.short_command(argv) == expected


def test_the_lock_folder_is_pd_lock_dir_or_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert checks.lock_dir() == tmp_path / "verrous"  # la fixture : jamais /tmp ici
    assert checks.heavy_lock() == tmp_path / "verrous" / "pd-gpu.lock"
    assert [p.name for p in checks.pd_test_locks()] == list(checks.TEST_LOCK_NAMES)
    assert checks.pd_test_locks(tmp_path)[0] == tmp_path / "pd-e2e.lock"
    monkeypatch.delenv(checks.LOCK_DIR_ENV)
    assert checks.lock_dir() == Path("/tmp")  # le poste réel, comme pd-lock
    assert checks.heavy_lock() == checks.HEAVY_LOCK
    assert checks.pd_test_locks() == checks.TEST_LOCKS


def test_a_held_test_lock_names_its_resource(tmp_path: Path, _own_lock_dir: Path) -> None:
    """Le vrai /proc/locks : ce pytest tient la place pytest-2 (du dossier temporaire)."""
    proc = tmp_path / "proc"
    proc.mkdir()
    lock = _own_lock_dir / "pd-pytest-2.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        # Les verrous par défaut du dossier temporaire (PD_LOCK_DIR posé par la fixture).
        wait = checks.wait_reason(needs_gpu=False, proc=proc)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert wait is not None and wait.reason == "tests en cours (verrou pd-pytest-2.lock pris)"
    assert wait.holder is not None and wait.holder.resource == "pytest-2"
    assert wait.holder.pid == os.getpid()
