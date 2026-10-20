"""KAN-52 : les modifications de l'espace de travail et du plan entrent au journal du projet.

Une seule porte pour toutes les routes d'écriture (il y en a des dizaines côté plan) : avant la
requête, l'intergiciel des comptes note l'empreinte (date, taille) de `board.json` et de
`project.json` ; après une réponse réussie, ce qui a changé devient `board.changed` ou
`plan.changed`, au nom de l'auteur. Les commentaires publient eux-mêmes ; renommer le projet
n'est pas une modification du plan. La cloche regroupe à la lecture (10 min)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from openplan.pipeline.board import BOARD_FILE
from openplan.pipeline.events import ProjectEvents
from openplan.scene.store import PROJECT_FILE
from openplan.schemas.events import EventType, ProjectEvent

_LEVEL = re.compile(r"/levels/([0-9a-f-]{36})(/|$)")
_SKIPPED = re.compile(r"^/api/projects/[0-9a-f-]{36}(/comments(/.*)?|/journal/ui)?$")
"""Pas des modifications : les commentaires (publiés par leurs routes), le renommage ou la
suppression du projet, la mesure d'usage de l'écran."""
_BOARD_PATHS = ("/board", "/media")


@dataclass(frozen=True)
class Stamps:
    board: tuple[int, int] | None
    plan: tuple[int, int] | None


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_mtime_ns, stat.st_size


def watched(path: str) -> bool:
    return _SKIPPED.match(path) is None


def stamps(project_dir: Path) -> Stamps:
    return Stamps(_stamp(project_dir / BOARD_FILE), _stamp(project_dir / PROJECT_FILE))


def record(project_dir: Path, project_id: UUID, actor: str, path: str, before: Stamps) -> bool:
    """Après une écriture réussie : au plus une ligne, la sorte dite par le chemin d'abord
    (une photo ajoutée touche aussi le tableau, c'est une modification du plan). Vrai si une
    ligne est entrée au journal."""
    after = stamps(project_dir)
    board, plan = after.board != before.board, after.plan != before.plan
    if not (board or plan):
        return False
    kind: EventType
    if board and (not plan or any(p in path for p in _BOARD_PATHS)):
        kind = "board.changed"
    else:
        kind = "plan.changed"
    level = _LEVEL.search(path)
    ProjectEvents(project_dir).append(
        ProjectEvent(
            type=kind,
            project_id=project_id,
            actor=actor,
            level_id=UUID(level.group(1)) if level and kind == "plan.changed" else None,
        )
    )
    return True
