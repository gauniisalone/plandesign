"""KAN-89 : garde-fous du poste de rendu (configuration, tour passé, temporisation, moteur,
calcul sans réseau). Aucun GPU : le moteur réel (KAN-88) n'est jamais lancé ici."""

from __future__ import annotations

import fcntl
import hashlib
import os
import subprocess
import sys
import types
from collections.abc import Sequence
from pathlib import Path

import pytest

from openplan.cli.main import main
from openplan.cli.render_worker import checks, config, engine
from openplan.cli.render_worker.client import ApiBusyError, ApiRefusedError
from openplan.cli.render_worker.engine import EngineFailedError, png_bytes
from openplan.cli.render_worker.loop import Backoff, Worker
from openplan.cli.render_worker.sandbox import BAKE, CAMERA, OUT, PROJECT, Sandbox
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from openplan.schemas.render_queue import WorkerJob


def test_token_file_is_private_and_never_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "plandesign" / "worker.env"
    assert main(["worker", "--config", str(path), "token"]) == 0
    out = capsys.readouterr().out
    assert (path.stat().st_mode & 0o777) == 0o600
    cfg = config.load_config(path)
    assert cfg.url == "https://app.plandesign.fr" and cfg.engine == "zimage"
    assert cfg.token not in out and cfg.token not in repr(cfg)
    assert hashlib.sha256(cfg.token.encode()).hexdigest() in out
    # Pas d'écrasement silencieux.
    assert main(["worker", "--config", str(path), "token"]) == 2
    # Trop ouvert : refusé, sans jamais montrer le jeton.
    path.chmod(0o644)
    with pytest.raises(config.WorkerConfigError) as caught:
        config.load_config(path)
    assert "chmod 600" in str(caught.value) and cfg.token not in str(caught.value)
    assert main(["worker", "--config", str(path), "status"]) == 2
    assert cfg.token not in capsys.readouterr().out


def test_url_is_https_or_loopback() -> None:
    assert config.checked_url("https://app.plandesign.fr/") == "https://app.plandesign.fr"
    assert config.checked_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    for bad in (
        "http://app.plandesign.fr",
        "ftp://x",
        "https://app.plandesign.fr/api",
        "https://u:p@app.plandesign.fr",
    ):
        with pytest.raises(config.WorkerConfigError):
            config.checked_url(bad)


def test_pause_resume_stop_flags(tmp_path: Path) -> None:
    path = tmp_path / "worker.env"
    args = ["worker", "--config", str(path)]
    assert main([*args, "pause"]) == 0 and (tmp_path / "worker.pause").exists()
    assert main([*args, "stop"]) == 0 and (tmp_path / "worker.stop").exists()
    assert main([*args, "resume"]) == 0
    assert not (tmp_path / "worker.pause").exists() and not (tmp_path / "worker.stop").exists()


def _runner(answers: dict[str, tuple[int, str]]) -> checks.Runner:
    def run(cmd: Sequence[str]) -> tuple[int, str]:
        return answers.get(cmd[0], (127, ""))

    return run


def test_turn_is_skipped_when_the_machine_works(tmp_path: Path) -> None:
    lock = tmp_path / "pd-lourd.lock"
    calm = _runner({"nvidia-smi": (0, "0\n"), "pgrep": (1, "")})
    assert checks.skip_reason(needs_gpu=True, run=calm, lock=lock) is None
    # Verrou du travail lourd tenu par un autre.
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        reason = checks.skip_reason(needs_gpu=True, run=calm, lock=lock)
        assert reason == "verrou du travail lourd pris"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert checks.skip_reason(needs_gpu=True, run=calm, lock=lock) is None
    busy_gpu = _runner({"nvidia-smi": (0, "0\n812\n"), "pgrep": (1, "")})
    assert checks.skip_reason(needs_gpu=True, run=busy_gpu, lock=lock) == (
        "GPU déjà occupé (812 Mio)"
    )
    no_smi = _runner({"pgrep": (1, "")})
    assert checks.skip_reason(needs_gpu=True, run=no_smi, lock=lock) == "nvidia-smi illisible"
    assert checks.skip_reason(needs_gpu=False, run=no_smi, lock=lock) is None
    tests = _runner({"nvidia-smi": (0, "0\n"), "pgrep": (0, "4242\n")})
    assert "tests en cours" in (checks.skip_reason(needs_gpu=True, run=tests, lock=lock) or "")


def test_backoff_never_loops_tightly() -> None:
    backoff = Backoff()
    assert [backoff.refused() for _ in range(4)] == [60.0, 300.0, 300.0, 300.0]
    assert [backoff.busy() for _ in range(7)] == [15.0, 30.0, 60.0, 120.0, 240.0, 300.0, 300.0]
    backoff.reset()
    assert backoff.refused() == 60.0 and backoff.busy() == 15.0


class StubClient:
    def __init__(self, answers: list[object]) -> None:
        self.answers = answers
        self.states: list[str] = []

    def claim(self, state: str) -> WorkerJob | None:
        self.states.append(state)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer  # type: ignore[return-value]


def _worker(tmp_path: Path, client: object, skip: str | None = None) -> Worker:
    return Worker(
        client=client,  # type: ignore[arg-type]
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        skip=lambda: skip,
        say=lambda _text: None,
        sleep=lambda _s: None,
        jitter=lambda low, high: (low + high) / 2,
    )


def test_worker_waits_after_refusals_and_errors(tmp_path: Path) -> None:
    """401/403 : 1 min, puis 5 min ; 429, 5xx, réseau : recul exponentiel plafonné ; un appel
    réussi remet à zéro ; sinon un appel toutes les 10 à 15 s."""
    client = StubClient(
        [
            ApiRefusedError("401"),
            ApiRefusedError("403"),
            ApiRefusedError("401"),
            None,
            ApiBusyError("429"),
            ApiBusyError("ConnectError"),
            ApiBusyError("502"),
            None,
            ApiRefusedError("401"),
        ]
    )
    worker = _worker(tmp_path, client)
    delays = [worker.cycle() for _ in range(9)]
    assert delays == [60.0, 300.0, 300.0, 12.5, 15.0, 30.0, 60.0, 12.5, 60.0]
    assert all(d >= 10.0 for d in delays)


def test_worker_reports_pause_and_skipped_turns(tmp_path: Path) -> None:
    client = StubClient([None, None, None])
    _worker(tmp_path, client, skip="tests en cours").cycle()
    (tmp_path / "worker.pause").touch()
    _worker(tmp_path, client).cycle()
    (tmp_path / "worker.pause").unlink()
    _worker(tmp_path, client).cycle()
    assert client.states == ["waiting", "paused", "idle"]


def test_stop_flag_ends_the_loop(tmp_path: Path) -> None:
    client = StubClient([None] * 5)
    worker = _worker(tmp_path, client)
    (tmp_path / "worker.stop").touch()
    assert worker.run() == 0
    assert client.states == []


def _camera(path: Path, width: int = 1280, height: int = 960) -> None:
    camera = RenderCamera(
        position=Point3(x=1.0, y=1.0, z=1.6),
        yaw=0.0,
        pitch=0.0,
        focal_px=1000.0,
        width_px=width,
        height_px=height,
    )
    path.write_text(camera.model_dump_json(), encoding="utf-8")


def test_fake_engine_and_missing_zimage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _camera(tmp_path / CAMERA)
    outcome = engine.render(
        "fake", tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT
    )
    assert (outcome.width, outcome.height, outcome.engine) == (1024, 768, "fake")
    assert (tmp_path / OUT).read_bytes().startswith(b"\x89PNG")
    # Moteur de KAN-88 absent : échec « unavailable », jamais une exception qui remonte.
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "openplan.pipeline.absent_kan89")
    with pytest.raises(EngineFailedError) as caught:
        engine.render(
            "zimage", tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT
        )
    assert caught.value.reason == "unavailable"


def test_zimage_contract_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le contrat de KAN-88, par un module factice : project_path passé, erreurs traduites."""
    fake = types.ModuleType("kan89_fake_zimage")

    class ZImageUnavailable(Exception):  # noqa: N818 - nom du contrat KAN-88
        pass

    class ZImageVramGuard(Exception):  # noqa: N818 - nom du contrat KAN-88
        pass

    calls: list[dict[str, object]] = []
    mode = {"raise": None}

    def render_scene(
        bake_path: Path,
        camera_path: Path,
        out_png: Path,
        *,
        project_path: Path | None = None,
        prompt: str | None = None,
    ) -> object:
        calls.append({"bake": bake_path, "project": project_path, "prompt": prompt})
        if mode["raise"] is not None:
            raise mode["raise"]  # type: ignore[misc]
        out_png.write_bytes(png_bytes(32, 16, (1, 2, 3)))
        return types.SimpleNamespace(
            seconds=3.5, vram_peak_mib=4700, width=32, height=16, engine="zimage"
        )

    fake.render_scene = render_scene  # type: ignore[attr-defined]
    fake.ZImageUnavailable = ZImageUnavailable  # type: ignore[attr-defined]
    fake.ZImageVramGuard = ZImageVramGuard  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kan89_fake_zimage", fake)
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "kan89_fake_zimage")
    args = (tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT)
    outcome = engine.render("zimage", *args)
    assert outcome.vram_peak_mib == 4700 and outcome.engine == "zimage"
    assert calls[0]["project"] == tmp_path / PROJECT and calls[0]["prompt"] is None
    for exc, reason in (
        (ZImageVramGuard(), "vram"),
        (ZImageUnavailable(), "unavailable"),
        (TimeoutError(), "timeout"),
        (RuntimeError("cuda"), "engine_error"),
    ):
        mode["raise"] = exc  # type: ignore[assignment]
        with pytest.raises(EngineFailedError) as caught:
            engine.render("zimage", *args)
        assert caught.value.reason == reason


def test_real_zimage_module_without_weights_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Après intégration de KAN-88 : le vrai module, sans poids, échoue en « unavailable »
    (jamais de GPU ici). Sauté tant que le module n'est pas dans la branche."""
    pytest.importorskip(engine.ZIMAGE_MODULE)
    monkeypatch.setenv("OPENPLAN_MODELS_DIR", str(tmp_path / "aucun-poids"))
    _camera(tmp_path / CAMERA)
    with pytest.raises(EngineFailedError) as caught:
        engine.render(
            "zimage", tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT
        )
    assert caught.value.reason == "unavailable"


def test_socket_guard_blocks_the_network() -> None:
    code = (
        "import socket\n"
        "from openplan.cli.render_worker.sandbox import block_network, NetworkBlockedError\n"
        "block_network()\n"
        "for attempt in (lambda: socket.create_connection(('127.0.0.1', 9), 1),\n"
        "                lambda: socket.socket().connect(('127.0.0.1', 9)),\n"
        "                lambda: socket.getaddrinfo('example.org', 443)):\n"
        "    try:\n"
        "        attempt()\n"
        "    except NetworkBlockedError:\n"
        "        print('bloqué')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.stdout.split() == ["bloqué"] * 3, done.stderr


@pytest.mark.parametrize("mode", ["socket", "unshare"])
def test_sandbox_runs_the_engine_in_a_child_process(tmp_path: Path, mode: str) -> None:
    from openplan.cli.render_worker import sandbox

    if mode == "unshare" and not sandbox.unshare_available():
        pytest.skip("unshare -rn indisponible sur ce noyau")
    _camera(tmp_path / CAMERA, 640, 480)
    outcome = Sandbox(mode=mode).run(tmp_path, "fake")  # type: ignore[arg-type]
    assert (outcome.width, outcome.height) == (640, 480)
    assert (tmp_path / OUT).is_file()
    # Une caméra illisible : échec propre, code seulement.
    (tmp_path / CAMERA).write_text("{}", encoding="utf-8")
    with pytest.raises(EngineFailedError) as caught:
        Sandbox(mode=mode).run(tmp_path, "fake")  # type: ignore[arg-type]
    assert caught.value.reason == "engine_error"


def test_sandbox_never_inherits_the_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANDESIGN_WORKER_TOKEN", "x" * 40)
    seen: dict[str, object] = {}

    class Popen:
        def __init__(self, cmd: list[str], **kwargs: object) -> None:
            seen.update(kwargs)
            seen["cmd"] = cmd
            self.returncode = 1
            self.pid = 0

        def wait(self, timeout: float) -> int:
            return 1

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(subprocess, "Popen", Popen)
    with pytest.raises(EngineFailedError):
        Sandbox(mode="unshare").run(tmp_path, "fake")
    assert "PLANDESIGN_WORKER_TOKEN" not in seen["env"]  # type: ignore[operator]
    cmd = seen["cmd"]
    # Commande fixe : le dossier de travail du poste et le moteur de sa configuration.
    assert cmd[:2] == ["unshare", "-rn"] and cmd[-2:] == [str(tmp_path), "fake"]  # type: ignore[index]
