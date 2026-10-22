"""Le calcul tourne sans réseau, dans un sous-processus ; l'envoi se fait ensuite par le parent.

- `unshare -rn` (espace de noms réseau vide, sans droits d'administrateur) quand le noyau le
  permet ; sinon le sous-processus pose lui-même une garde de socket (toute connexion
  IPv4/IPv6 refusée), qui est aussi posée sous `unshare`, par prudence.
- Le sous-processus reçoit un dossier de travail créé par le poste (noms de fichiers fixes) et
  le nom du moteur, lu dans la configuration locale : rien qui vienne du serveur. Il n'hérite
  pas du jeton (jamais dans l'environnement).
- Il écrit `result.json` : le résultat, ou un code d'échec ; au-delà du délai, il est tué.

Entrée du sous-processus : `python -m openplan.cli.render_worker.sandbox <dossier> <moteur>`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import openplan
from openplan.cli.render_worker.engine import EngineFailedError, RenderOutcome, render

PROJECT, BAKE, CAMERA, OUT, RESULT = (
    "project.json",
    "bake.json",
    "camera.json",
    "render.png",
    "result.json",
)
TIMEOUT_S = 20 * 60
"""Le moteur a sa propre garde (15 min) ; au-delà de 20 min, le poste tue le calcul."""
Mode = Literal["unshare", "socket"]
_FORBIDDEN_ENV = ("PLANDESIGN_WORKER_TOKEN",)


class NetworkBlockedError(OSError):
    """Le calcul a tenté une connexion réseau."""


def block_network() -> None:
    """Garde de socket : toute connexion ou résolution réseau est refusée dans ce processus."""

    def refused(*_args: Any, **_kwargs: Any) -> Any:
        msg = "réseau interdit pendant le calcul du rendu"
        raise NetworkBlockedError(msg)

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refused()
        original_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            refused()
        return original_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign,assignment]
    socket.getaddrinfo = refused
    socket.create_connection = refused


_unshare_ok: bool | None = None


def unshare_available() -> bool:
    global _unshare_ok  # noqa: PLW0603 - sondé une fois par processus
    if _unshare_ok is None:
        binary = shutil.which("unshare")
        if binary is None:
            _unshare_ok = False
        else:
            try:
                probe = subprocess.run(
                    [binary, "-rn", "true"], capture_output=True, timeout=10, check=False
                )
                _unshare_ok = probe.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                _unshare_ok = False
    return _unshare_ok


class Sandbox:
    """Lance le calcul ; `kill()` l'arrête (Ctrl+C du poste)."""

    def __init__(self, mode: Mode | None = None, timeout_s: float = TIMEOUT_S) -> None:
        self.mode: Mode = mode or ("unshare" if unshare_available() else "socket")
        self.timeout_s = timeout_s
        self.process: subprocess.Popen[bytes] | None = None

    def command(self, job_dir: Path, engine: str) -> list[str]:
        cmd = [sys.executable, "-m", "openplan.cli.render_worker.sandbox", str(job_dir), engine]
        return ["unshare", "-rn", *cmd] if self.mode == "unshare" else cmd

    def run(self, job_dir: Path, engine: str) -> RenderOutcome:
        env = {k: v for k, v in os.environ.items() if k not in _FORBIDDEN_ENV}
        root = Path(openplan.__file__).resolve().parents[1]
        env["PYTHONPATH"] = str(root)  # le même code que le poste, pas une autre copie
        self.process = subprocess.Popen(
            self.command(job_dir, engine),
            cwd=job_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            self.process.wait(timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            self.kill()
            raise EngineFailedError("timeout") from exc
        finally:
            code = self.process.returncode
            self.process = None
        return read_result(job_dir, code)

    def kill(self) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()


def read_result(job_dir: Path, code: int | None) -> RenderOutcome:
    path = job_dir / RESULT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise EngineFailedError("engine_error") from None
    if not data.get("ok"):
        reason = data.get("reason")
        valid = ("unavailable", "vram", "timeout", "engine_error", "invalid_input")
        raise EngineFailedError(reason if reason in valid else "engine_error")
    if code != 0:
        raise EngineFailedError("engine_error")
    return RenderOutcome(**data["outcome"])


def child(job_dir: Path, engine: str) -> int:
    block_network()
    out = job_dir / RESULT
    try:
        outcome = render(engine, job_dir / PROJECT, job_dir / BAKE, job_dir / CAMERA, job_dir / OUT)
    except EngineFailedError as exc:
        out.write_text(json.dumps({"ok": False, "reason": exc.reason}), encoding="utf-8")
        return 1
    except Exception:
        out.write_text(json.dumps({"ok": False, "reason": "engine_error"}), encoding="utf-8")
        return 1
    out.write_text(json.dumps({"ok": True, "outcome": asdict(outcome)}), encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover - lancé par Sandbox.run
    sys.exit(child(Path(sys.argv[1]), sys.argv[2]))
