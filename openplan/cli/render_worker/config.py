"""Configuration du poste de rendu : ~/.config/plandesign/worker.env (droits 600).

    PLANDESIGN_URL=https://app.plandesign.fr
    PLANDESIGN_WORKER_TOKEN=<jeton, créé par `openplan worker token`>
    PLANDESIGN_WORKER_ENGINE=zimage            # ou `fake` (essai sans GPU)

Le jeton n'est jamais affiché, ni journalisé, ni passé au sous-processus de calcul. Un fichier
lisible par d'autres que son propriétaire est refusé. Les drapeaux `worker.pause` et
`worker.stop`, dans le même dossier, mettent en pause ou arrêtent le poste.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import secrets
import stat
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from openplan.api.settings import load_env_file

DEFAULT_URL = "https://app.plandesign.fr"
ENGINES = ("zimage", "fake")
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "plandesign"


def env_file() -> Path:
    return config_dir() / "worker.env"


def pause_flag() -> Path:
    return config_dir() / "worker.pause"


def stop_flag() -> Path:
    return config_dir() / "worker.stop"


class WorkerConfigError(RuntimeError):
    """Configuration absente, trop ouverte ou invalide (le message ne contient jamais le
    jeton)."""


LOCK_FILE = "worker.lock"
ALREADY_RUNNING = (
    "un poste de rendu tourne déjà sur cette machine{pid} : un seul à la fois (deux postes "
    "appellent le serveur trop souvent et se font refuser). `openplan worker stop` l'arrête, "
    "ou laissez-le tourner."
)


class WorkerAlreadyRunningError(WorkerConfigError):
    """Retours de la MEP 14 : un deuxième `openplan worker run` avec la même configuration."""


@contextlib.contextmanager
def single_worker(folder: Path) -> Iterator[None]:
    """Verrou de fichier (`worker.lock`, à côté de worker.env) tenu tant que le poste tourne ;
    rendu par le système même si le poste est tué. Un deuxième poste est refusé, avec le pid
    du premier."""
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            other = os.read(fd, 16).decode(errors="ignore").strip()
            pid = f" (processus {other})" if other.isdigit() else ""
            raise WorkerAlreadyRunningError(ALREADY_RUNNING.format(pid=pid)) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)


@dataclass(frozen=True)
class WorkerConfig:
    url: str
    token: str = field(repr=False)
    engine: str = "zimage"


def checked_url(url: str) -> str:
    """HTTPS seulement ; HTTP accepté pour la boucle locale (essais, e2e)."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if (parts.scheme == "https" and host) or (parts.scheme == "http" and host in LOCAL_HOSTS):
        pass
    else:
        msg = "PLANDESIGN_URL : une adresse https:// (http:// seulement pour 127.0.0.1)"
        raise WorkerConfigError(msg)
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
        msg = "PLANDESIGN_URL : l'origine seule, par exemple https://app.plandesign.fr"
        raise WorkerConfigError(msg)
    return f"{parts.scheme}://{parts.netloc}"


def load_config(path: Path | None = None) -> WorkerConfig:
    path = path or env_file()
    if not path.is_file():
        msg = f"{path} absent : `openplan worker token` le crée"
        raise WorkerConfigError(msg)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        msg = f"{path} est lisible par d'autres comptes : `chmod 600 {path}`"
        raise WorkerConfigError(msg)
    values = load_env_file(path)
    token = values.get("PLANDESIGN_WORKER_TOKEN", "").strip()
    if len(token) < 32:
        msg = f"{path} : PLANDESIGN_WORKER_TOKEN absent ou trop court"
        raise WorkerConfigError(msg)
    engine = values.get("PLANDESIGN_WORKER_ENGINE", "zimage").strip() or "zimage"
    if engine not in ENGINES:
        msg = f"PLANDESIGN_WORKER_ENGINE : {' ou '.join(ENGINES)}"
        raise WorkerConfigError(msg)
    url = checked_url(values.get("PLANDESIGN_URL", DEFAULT_URL) or DEFAULT_URL)
    return WorkerConfig(url=url, token=token, engine=engine)


def create_token_file(path: Path | None = None, url: str = DEFAULT_URL, force: bool = False) -> str:
    """Crée le fichier (600, dossier 700) avec un jeton neuf ; renvoie l'EMPREINTE seulement,
    à donner à l'Infra (OPENPLAN_WORKER_TOKEN_SHA256). Refuse d'écraser sans `force`."""
    path = path or env_file()
    url = checked_url(url)
    if path.exists() and not force:
        msg = f"{path} existe déjà : --force pour le remplacer (l'ancien jeton sera perdu)"
        raise WorkerConfigError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.parent.chmod(0o700)
    token = secrets.token_urlsafe(32)
    partial = path.with_suffix(".tmp")
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(
            "# Poste de rendu PlanDesign (KAN-89). Ne jamais partager ce fichier.\n"
            f"PLANDESIGN_URL={url}\n"
            f"PLANDESIGN_WORKER_TOKEN={token}\n"
            "PLANDESIGN_WORKER_ENGINE=zimage\n"
        )
    os.chmod(partial, 0o600)
    os.replace(partial, path)
    # Même empreinte que `openplan.api.worker_auth.token_hash` (sans importer FastAPI ici).
    return hashlib.sha256(token.encode()).hexdigest()
