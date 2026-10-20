"""KAN-43 : une vue de l'espace de travail par personne (position et zoom), gardée par projet
dans `views.json`, à côté de `board.json`. Une vue n'est pas une modification du projet : ni au
journal, ni dans la cloche, ni dans l'archive. Même code en local (« local ») et avec les
comptes (l'id du compte). Écrit sous le verrou du projet par l'appelant. À ne pas confondre
avec `pipeline.views`, les vues de rendu."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from uuid import uuid4

from openplan.schemas.board import BoardView
from openplan.schemas.common import now_utc

VIEWS_FILE = "views.json"
ZOOM_RANGE = (0.1, 4.0)
"""La plage de zoom de l'interface (BoardCanvas) : une valeur reçue hors plage y est ramenée."""
MAX_COORD = 1e7


def bounded(view: BoardView) -> BoardView:
    """Une vue sûre à l'arrivée : zoom dans la plage de l'interface, x et y finis et bornés."""

    def coord(v: float) -> float:
        return max(-MAX_COORD, min(MAX_COORD, v)) if math.isfinite(v) else 0.0

    zoom = view.zoom if math.isfinite(view.zoom) else 1.0
    return BoardView(
        x=coord(view.x), y=coord(view.y), zoom=max(ZOOM_RANGE[0], min(ZOOM_RANGE[1], zoom))
    )


class ViewStore:
    def __init__(self, project_dir: Path) -> None:
        self.path = project_dir / VIEWS_FILE

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        views = data.get("views") if isinstance(data, dict) else None
        return views if isinstance(views, dict) else {}

    def _save(self, views: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f"{VIEWS_FILE}.{uuid4().hex}.tmp")
        tmp.write_text(json.dumps({"version": 1, "views": views}, indent=2) + "\n", "utf-8")
        tmp.replace(self.path)

    def get(self, person: str) -> BoardView | None:
        raw = self._load().get(person)
        if not isinstance(raw, dict):
            return None
        try:
            return bounded(BoardView(x=raw["x"], y=raw["y"], zoom=raw["zoom"]))
        except (KeyError, TypeError, ValueError):
            return None

    def set(self, person: str, view: BoardView) -> BoardView:
        safe = bounded(view)
        views = self._load()
        views[person] = {**safe.model_dump(), "at": now_utc().isoformat()}
        self._save(views)
        return safe

    def forget(self, person: str) -> None:
        views = self._load()
        if views.pop(person, None) is not None:
            self._save(views)
