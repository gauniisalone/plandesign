"""KAN-52, KAN-58 : les modifications de l'espace de travail et du plan entrent au journal du
projet.

- Le plan : une seule porte pour toutes les routes d'écriture (il y en a des dizaines). Avant la
  requête, l'intergiciel des comptes note l'empreinte (date, taille) de `board.json` et de
  `project.json` ; après une réponse réussie, ce qui a changé devient `plan.changed`, au nom de
  l'auteur, avec le libellé du geste tiré de l'historique (« Ajouter un mur » ; « Annulé : … »).
- L'espace de travail : ses routes publient elles-mêmes (`record_board`), avec le détail de ce
  que le geste a posé, modifié ou retiré, et le nom de l'élément quand il n'y en a qu'un. Une vue
  enregistrée (zoom, déplacement de la vue) n'est pas une modification.
- Les commentaires publient eux-mêmes ; renommer le projet n'est pas une modification du plan.
  La cloche et le journal d'activité regroupent à la lecture (10 min)."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool

from openplan.pipeline.board import BOARD_FILE, board_changes
from openplan.pipeline.events import ProjectEvents
from openplan.scene.history import USER_ACTOR, USER_PREFIX
from openplan.scene.store import PROJECT_FILE
from openplan.schemas.board import Board
from openplan.schemas.comments import LOCAL_AUTHOR
from openplan.schemas.events import MAX_LABEL, ProjectEvent

_LEVEL = re.compile(r"/levels/([0-9a-f-]{36})(/|$)")
_SKIPPED = re.compile(
    r"^/api/projects/[0-9a-f-]{36}(/comments(/.*)?|/journal/ui|/board(/.*)?|/media(/.*)?)?$"
)
"""Publiés ailleurs, ou pas des modifications : les commentaires et l'espace de travail (par
leurs routes), le renommage ou la suppression du projet, la mesure d'usage de l'écran."""


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


def _plan_label(project_dir: Path, path: str) -> str | None:
    """Le geste qui vient d'être fait sur le plan, d'après l'historique (lu seulement quand
    `project.json` a changé) ; annuler et rétablir le disent."""
    try:
        data = json.loads((project_dir / PROJECT_FILE).read_text(encoding="utf-8"))
        history, cursor = data.get("history") or [], int(data.get("history_cursor") or 0)
    except (OSError, ValueError, TypeError):
        return None
    if path.endswith("/undo"):
        entry, prefix = (history[cursor] if cursor < len(history) else None), "Annulé : "
    elif path.endswith("/redo"):
        entry, prefix = (history[cursor - 1] if cursor > 0 else None), "Rétabli : "
    else:
        entry, prefix = (history[cursor - 1] if cursor > 0 else None), ""
    label = str(entry.get("label") or "") if isinstance(entry, dict) else ""
    return (prefix + " ".join(label.split()))[:MAX_LABEL] if label else None


def record(project_dir: Path, project_id: UUID, actor: str, path: str, before: Stamps) -> bool:
    """Après une écriture réussie hors des routes de l'espace de travail : au plus une ligne.
    Une photo ajoutée touche aussi le tableau : c'est une modification du plan. Vrai si une
    ligne est entrée au journal."""
    # Seul le plan compte ici : l'espace de travail est publié par ses routes, avec le détail ;
    # `board.json` réécrit ailleurs n'est qu'une synchronisation automatique, pas un geste.
    if stamps(project_dir).plan == before.plan:
        return False
    level = _LEVEL.search(path)
    ProjectEvents(project_dir).append(
        ProjectEvent(
            type="plan.changed",
            project_id=project_id,
            actor=actor,
            level_id=UUID(level.group(1)) if level else None,
            label=_plan_label(project_dir, path),
        )
    )
    return True


_PROJECT = re.compile(r"^/api/projects/([0-9a-f-]{36})(/|$)")
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def install(app: FastAPI) -> None:
    """La porte du plan, en local comme avec les comptes : une écriture réussie sur un projet
    qui a changé `project.json` entre au journal. `request.state.journaled` dit à l'intergiciel
    des comptes de faire sonner les cloches."""

    @app.middleware("http")
    async def _journal(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        match = _PROJECT.match(path) if request.method in _MUTATING else None
        store = request.app.state.store
        if match is None or not watched(path) or not store.exists(UUID(match.group(1))):
            return await call_next(request)
        project_id = UUID(match.group(1))
        project_dir = store.project_dir(project_id)
        before = await run_in_threadpool(stamps, project_dir)
        response = await call_next(request)
        if response.status_code < 400:
            actor = author_of(getattr(request.state, "actor", USER_ACTOR))
            if await run_in_threadpool(record, project_dir, project_id, actor, path, before):
                request.state.journaled = True
        return response


def author_of(actor: str) -> str:
    """L'auteur gardé au journal : `local` sans comptes, sinon l'id du compte."""
    if actor == USER_ACTOR:
        return LOCAL_AUTHOR
    return actor.removeprefix(USER_PREFIX)


def record_board(
    project_dir: Path, project_id: UUID, actor: str, before: Board | None, after: Board
) -> bool:
    """KAN-58 : un geste sur l'espace de travail, avec son détail. Rien si seule la vue a
    changé. Vrai si une ligne est entrée au journal."""
    changes = board_changes(before, after)
    if changes.total == 0:
        return False
    single = changes.single
    ProjectEvents(project_dir).append(
        ProjectEvent(
            type="board.changed",
            project_id=project_id,
            actor=actor,
            item_id=single.id if single is not None else None,
            item_kind=single.kind if single is not None else None,
            label=(" ".join(single.text.split())[:MAX_LABEL] or None) if single else None,
            added=changes.added,
            changed=changes.changed,
            removed=changes.removed,
        )
    )
    return True
