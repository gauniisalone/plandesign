"""Journal du poste de rendu (KAN-159) : un fichier qui tourne chaque nuit, gardé 14 jours.

Il reçoit les lignes que le poste dit au terminal, et chaque tour passé (le terminal n'en dit
qu'un par minute). Rien du projet n'y entre : le poste n'y écrit que ses propres phrases (début
d'identifiant de demande, codes d'échec, valeurs relevées, raisons d'attente), jamais une
description, un nom de projet ou d'image, ni le jeton.

    ~/.local/state/plandesign/poste.log        (XDG_STATE_HOME, sinon ~/.local/state)
    ~/.local/state/plandesign/poste.log.2026-11-06 …
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

KEEP_DAYS = 14
FILE_NAME = "poste.log"
LOGGER = "openplan.render_worker.journal"


def journal_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "plandesign"


def open_journal(folder: Path | None = None) -> logging.Logger:
    """Le journal (dossier 700, fichier 600), rotation à minuit, `KEEP_DAYS` fichiers gardés.
    Ouvert deux fois, il garde un seul fichier."""
    folder = folder or journal_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        folder.chmod(0o700)
    path = folder / FILE_NAME
    logger = logging.getLogger(f"{LOGGER}.{path}")
    logger.setLevel(logging.INFO)
    logger.propagate = False  # jamais vers les journaux de l'application
    if not logger.handlers:
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(fd)
        handler = TimedRotatingFileHandler(
            path, when="midnight", backupCount=KEEP_DAYS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
    return logger


def close_journal(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def teller(
    logger: logging.Logger, out: Callable[[str], None] = print
) -> tuple[Callable[[str], None], Callable[[str], None]]:
    """(`say`, `note`) pour `Worker` : `say` écrit au terminal et au journal, `note` au journal
    seul."""

    def note(text: str) -> None:
        logger.info(text)

    def say(text: str) -> None:
        out(text)
        note(text)

    return say, note
