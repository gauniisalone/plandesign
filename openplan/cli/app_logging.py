"""KAN-144 : les journaux de l'application (`openplan.*`) au niveau INFO, sur la sortie d'erreur.

Sans configuration, le logger racine reste au niveau WARNING : les `log.info` de `openplan.*` ne
sortaient pas, et la ligne de bilan de la purge KAN-142 manquait à app.log. uvicorn ne configure
que ses propres loggers (`uvicorn`, `uvicorn.error`, `uvicorn.access`) ; `openplan serve` pose
donc, avant de construire l'application, un gestionnaire sur le logger `openplan`, sur la même
sortie que les messages d'uvicorn.

Ce que ce niveau rend visible (inventaire du 6 nov., e057dd6) : une seule ligne INFO, le bilan de
la purge (`openplan.retention`, des nombres seulement, une ligne au démarrage puis une par jour).
Les `log.debug` (`openplan.live`) restent cachés. Tout nouveau `log.info` de `openplan.*` sortira
dans app.log : jamais de secret, de jeton, d'e-mail ni d'IP en clair.

Seul le logger `openplan` est touché : la racine et les bibliothèques gardent leur niveau. Les
lignes WARNING et plus de `openplan.*` sortaient déjà (gestionnaire de dernier recours de
Python, message nu) ; elles portent désormais l'heure, le niveau et le nom du logger."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

APP_LOGGER = "openplan"
LEVEL = logging.INFO
FORMAT = "%(asctime)s %(levelname)s %(name)s : %(message)s"
_MARK = "_openplan_app_logging"
"""Marque du gestionnaire posé ici : le reposer (relance, tests) remplace l'ancien sans doubler
les lignes."""


def configure_app_logging(stream: TextIO | None = None) -> logging.Handler:
    """Le logger `openplan` au niveau INFO, vers `stream` (défaut : la sortie d'erreur)."""
    logger = logging.getLogger(APP_LOGGER)
    for old in [h for h in logger.handlers if getattr(h, _MARK, False)]:
        logger.removeHandler(old)
        old.close()
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    logger.setLevel(LEVEL)
    return handler
