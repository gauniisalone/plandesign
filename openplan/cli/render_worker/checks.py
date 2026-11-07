"""Garde-fous du poste avant chaque rendu : il passe son tour quand la machine travaille déjà.

- le verrou du GPU (/tmp/pd-gpu.lock, pris par `pd-lock gpu` ; KAN-152 : avant, le verrou unique
  /tmp/pd-lourd.lock, qui arrêtait le poste pendant le moindre pytest) est pris : le poste dit qui
  le tient et depuis quand (/proc/locks), pour qu'une attente ne passe pas pour une panne ;
- le GPU sert déjà (nvidia-smi au-dessus de 0 Mio), ou nvidia-smi est illisible (moteur réel) ;
- des tests tournent : un verrou de `pd-lock` (e2e, pytest) est pris, ou un vrai lanceur de
  tests tourne (KAN-159 : plus un simple `pgrep -f`, qui comptait un `grep pytest`).
Les commandes lancées ici sont fixes : rien ne vient du serveur.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# Sonar S5443, faux positif : le verrou commun de la machine n'est qu'ouvert en lecture pour un
# essai flock ; rien n'y est écrit. Un tiers local ne peut que faire passer son tour au poste.
# KAN-152 : la ressource GPU de `pd-lock` (pytest et e2e ont leurs propres verrous ; le poste les
# voit encore par pgrep, plus bas, et le GPU occupé par nvidia-smi).
HEAVY_LOCK = Path("/tmp/pd-gpu.lock")  # NOSONAR
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


@dataclass(frozen=True)
class Holder:
    """Qui tient le verrou du travail lourd, lu dans /proc/locks (pas de commande lancée)."""

    pid: int
    command: str | None
    """Commande raccourcie (« playwright e2e/openings.spec.ts ») ; None si illisible."""
    seconds: float | None
    """Depuis combien de temps le processus tourne ; None si illisible."""


_WRAPPERS = frozenset(
    {"nice", "timeout", "npx", "npm", "exec", "uv", "run", "bash", "sh", "env", "node"}
)
_TEST_WORDS = ("playwright", "pytest", "commit29", "e2e")
WAIT_END = "le rendu partira dès qu'il sera fini"


def short_command(argv: Sequence[str], lock: Path = HEAVY_LOCK) -> str:
    """La commande lancée sous le verrou, sans `flock`, les options courtes, les chemins
    absolus ni les enveloppes (nice, timeout, npx, uv run, bash, python), en 3 mots au plus :
    « playwright test e2e/x.spec.ts »."""
    words = list(argv)
    if str(lock) in words:
        words = words[words.index(str(lock)) + 1 :]
    # KAN-152 : `pd-lock gpu -- <commande>` (le détenteur est le script pd-lock lui-même).
    if any(Path(w).name == "pd-lock" for w in words) and "--" in words:
        words = words[words.index("--") + 1 :]
    words = [Path(w).name if w.startswith("/") else w for w in words]
    kept = [
        w
        for w in words
        if w not in _WRAPPERS
        and not w.startswith("python")
        and not w.lstrip("-").isdigit()
        and not (w.startswith("-") and len(w) <= 3)
    ]
    return " ".join(kept[:3])[:70] or "travail lourd"


def lock_holder(path: Path = HEAVY_LOCK, proc: Path = Path("/proc")) -> Holder | None:
    """Le processus qui tient le verrou (ligne FLOCK de /proc/locks sur le même inode), avec sa
    commande et sa durée ; None si on ne le trouve pas."""
    try:
        st = path.stat()
        locks = (proc / "locks").read_text(encoding="utf-8")
    except OSError:
        return None
    target = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    for line in locks.splitlines():
        fields = line.split()
        if len(fields) >= 6 and "FLOCK" in fields and fields[5] == target:
            pid = int(fields[4]) if fields[4].isdigit() else 0
            return Holder(pid, *_process(proc, pid, path))
    return None


def _process(proc: Path, pid: int, lock: Path) -> tuple[str | None, float | None]:
    try:
        argv = (proc / str(pid) / "cmdline").read_bytes().split(b"\0")
        stat = (proc / str(pid) / "stat").read_text(encoding="utf-8")
        uptime = float((proc / "uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None, None
    command = short_command([a.decode(errors="ignore") for a in argv if a], lock)
    try:
        # Champ 22 de /proc/<pid>/stat (après le nom entre parenthèses) : début, en tops.
        start = int(stat.rsplit(")", 1)[1].split()[19]) / os.sysconf("SC_CLK_TCK")
    except (IndexError, ValueError, OSError):
        return command, None
    return command, max(0.0, uptime - start)


def waiting_text(holder: Holder | None) -> str:
    """« en attente : un test lourd tourne depuis 4 min (playwright e2e/x.spec.ts), le rendu
    partira dès qu'il sera fini »."""
    if holder is None or holder.command is None:
        return f"en attente : un travail lourd tient le verrou, {WAIT_END}"
    test = any(w in holder.command for w in _TEST_WORDS)
    what = "un test lourd" if test else "un travail lourd"
    if holder.seconds is None:
        since = ""
    elif holder.seconds < 60:
        since = " depuis moins d'une minute"
    else:
        since = f" depuis {round(holder.seconds / 60)} min"
    return f"en attente : {what} tourne{since} ({holder.command}), {WAIT_END}"


def gpu_used_mib(run: Runner = run_command) -> int | None:
    """Mémoire GPU occupée (Mio, la plus haute des cartes) ; None si illisible."""
    code, out = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"])
    values = [line.strip() for line in out.splitlines() if line.strip()]
    if code != 0 or not values or not all(v.isdigit() for v in values):
        return None
    return max(int(v) for v in values)


_PYTEST = frozenset({"pytest", "py.test"})
_LAUNCH_WRAPPERS = frozenset({"nice", "timeout", "env", "uv", "run", "npx", "npm", "exec"})
"""Ce qui peut précéder un lanceur de tests sur sa ligne de commande. Ni `bash` ni `sh` : un
`bash -c "…pytest…"` lance lui-même le vrai pytest, compté à part."""


def launches_tests(argv: Sequence[str]) -> str | None:
    """KAN-159 : « pytest » ou « playwright » si cette ligne de commande LANCE des tests
    (`pytest …`, `python -m pytest …`, `python …/bin/pytest …`, `playwright test …`, `node
    …/playwright/cli.js test …`, derrière nice, timeout, uv run, npx) ; None sinon : un
    `grep pytest`, un éditeur ou un `tail` d'un journal de pytest ne comptent plus."""
    words = list(argv)
    i = 0
    while i < len(words):
        word = words[i]
        name = Path(word).name
        if name.startswith("python") or name == "node" or name in _LAUNCH_WRAPPERS:
            i += 1
        elif word == "-m" and i + 1 < len(words):  # python -m pytest
            return "pytest" if words[i + 1] in _PYTEST else None
        elif word.startswith("-") or word.isdigit():  # options, durée de timeout, nice -n 10
            i += 1
        elif name in _PYTEST:
            return "pytest"
        elif name == "playwright" or (name == "cli.js" and "playwright" in word):
            return "playwright" if words[i + 1 : i + 2] == ["test"] else None
        else:
            return None
    return None


TEST_LOCKS: tuple[Path, ...] = (
    Path("/tmp/pd-e2e.lock"),  # NOSONAR - même faux positif que HEAVY_LOCK (lecture, flock)
    Path("/tmp/pd-pytest-1.lock"),  # NOSONAR
    Path("/tmp/pd-pytest-2.lock"),  # NOSONAR
)
"""Les verrous de `pd-lock` (Infra, 6 nov.) que prend tout lot de tests : e2e et les deux
places de pytest."""


def tests_running(locks: Sequence[Path] = TEST_LOCKS, proc: Path = Path("/proc")) -> str | None:
    """KAN-159 : pourquoi le poste croit que des tests tournent, ou None. D'abord les verrous de
    `pd-lock` (flock non bloquant), puis les vrais lanceurs de tests (`launches_tests`, lu dans
    /proc/<pid>/cmdline, sans commande lancée) ; le poste ne se compte pas lui-même."""
    for lock in locks:
        if lock_taken(lock):
            return f"verrou {lock.name} pris"
    mine = str(os.getpid())
    try:
        pids = [p for p in proc.iterdir() if p.name.isdigit() and p.name != mine]
    except OSError:
        return None
    for pid in pids:
        try:
            raw = (pid / "cmdline").read_bytes()
        except OSError:
            continue  # processus fini entre-temps, ou d'un autre compte
        argv = [a.decode(errors="ignore") for a in raw.split(b"\0") if a]
        what = launches_tests(argv)
        if what is not None:
            return f"{what}, processus {pid.name}"
    return None


def skip_reason(
    *,
    needs_gpu: bool,
    run: Runner = run_command,
    lock: Path = HEAVY_LOCK,
    test_locks: Sequence[Path] = TEST_LOCKS,
    proc: Path = Path("/proc"),
) -> str | None:
    """Pourquoi passer son tour, ou None si le poste peut rendre."""
    if lock_taken(lock):
        return waiting_text(lock_holder(lock))
    if needs_gpu:
        used = gpu_used_mib(run)
        if used is None:
            return "nvidia-smi illisible"
        if used > 0:
            return f"GPU déjà occupé ({used} Mio)"
    running = tests_running(test_locks, proc)
    if running is not None:
        return f"tests en cours ({running})"
    return None
