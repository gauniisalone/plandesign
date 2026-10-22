"""Garde-fous du poste avant chaque rendu : il passe son tour quand la machine travaille déjà.

- le verrou du travail lourd (/tmp/pd-lourd.lock) est pris ;
- le GPU sert déjà (nvidia-smi au-dessus de 0 Mio), ou nvidia-smi est illisible (moteur réel) ;
- des tests tournent (pgrep trouve playwright ou pytest).
Les commandes lancées ici sont fixes : rien ne vient du serveur.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

HEAVY_LOCK = Path("/tmp/pd-lourd.lock")
Runner = Callable[[Sequence[str]], tuple[int, str]]


def run_command(cmd: Sequence[str]) -> tuple[int, str]:
    try:
        done = subprocess.run(list(cmd), capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return done.returncode, done.stdout


def lock_taken(path: Path = HEAVY_LOCK) -> bool:
    """Le verrou est-il tenu par quelqu'un ? (essai non bloquant, relâché aussitôt)"""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def gpu_used_mib(run: Runner = run_command) -> int | None:
    """Mémoire GPU occupée (Mio, la plus haute des cartes) ; None si illisible."""
    code, out = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    values = [line.strip() for line in out.splitlines() if line.strip()]
    if code != 0 or not values or not all(v.isdigit() for v in values):
        return None
    return max(int(v) for v in values)


def heavy_processes(run: Runner = run_command) -> bool:
    """Un playwright ou un pytest tourne (pgrep ne se compte pas lui-même)."""
    code, out = run(["pgrep", "-f", "playwright|pytest"])
    mine = str(os.getpid())
    return code == 0 and any(pid.strip() and pid.strip() != mine for pid in out.splitlines())


def skip_reason(
    *, needs_gpu: bool, run: Runner = run_command, lock: Path = HEAVY_LOCK
) -> str | None:
    """Pourquoi passer son tour, ou None si le poste peut rendre."""
    if lock_taken(lock):
        return "verrou du travail lourd pris"
    if needs_gpu:
        used = gpu_used_mib(run)
        if used is None:
            return "nvidia-smi illisible"
        if used > 0:
            return f"GPU déjà occupé ({used} Mio)"
    if heavy_processes(run):
        return "tests en cours (playwright ou pytest)"
    return None
