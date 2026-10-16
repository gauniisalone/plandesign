"""Vues périmées (P2) : la vue d'une caméra est périmée quand un pas d'historique appliqué,
plus récent que son dernier rendu, touche ce qu'on voit : la géométrie, les meubles, les
matières, l'ambiance (tout ce qui n'est ni une caméra ni un réglage du projet). Un pas qui ne
touche que des caméras ne périme que les vues de ces caméras ; un réglage du projet (nom,
réglages de rendu, confidentialité, unités, consigne gardée, photos) n'en périme aucune.

La même règle est écrite côté atelier (`apps/web/src/staleViews.ts`) : les deux se lisent
ensemble, et le test `tests/unit/test_stale.py` en fixe les cas.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from pydantic import JsonValue

from openplan.pipeline.render import RenderEntry
from openplan.schemas.scene import HistoryEntry, Project

PROJECT_SETTINGS = frozenset(
    {
        "name",
        "name_auto",
        "render",
        "privacy",
        "units_display",
        "pending_instruction",
        "pending_answers",
        "images",
    }
)
"""Champs du projet dont le changement ne change pas ce qu'on voit dans un rendu."""
_ELEMENT = re.compile(r"^/levels/([^/]+)/elements/([^/]+)(/|$)")


@dataclass(frozen=True)
class StepScope:
    """Ce qu'un pas d'historique touche : tout ce qu'on voit, ou seulement des caméras."""

    visible: bool
    cameras: frozenset[str] = field(default_factory=frozenset)


def _types_at(ops: Iterable[Mapping[str, JsonValue]]) -> dict[str, str]:
    """Type de l'élément ajouté ou remplacé par chemin d'élément (« /levels/…/elements/… »)."""
    out: dict[str, str] = {}
    for op in ops:
        path, value = op.get("path"), op.get("value")
        if isinstance(path, str) and isinstance(value, dict) and _ELEMENT.match(path):
            kind = value.get("type")
            if isinstance(kind, str):
                out[path.rstrip("/")] = kind
    return out


def step_scope(entry: HistoryEntry, project: Project) -> StepScope:
    added = _types_at(entry.patch)
    removed = _types_at(entry.inverse_patch)
    visible = False
    cameras: set[str] = set()
    for op in entry.patch:
        for key in ("path", "from"):
            path = op.get(key)
            if not isinstance(path, str) or not path.startswith("/"):
                continue
            top = path.split("/")[1]
            if top in PROJECT_SETTINGS:
                continue
            m = _ELEMENT.match(path)
            if m is None:
                visible = True
                continue
            level_id, element_id = m.group(1), m.group(2)
            element_path = f"/levels/{level_id}/elements/{element_id}"
            kind = _current_type(project, level_id, element_id)
            if kind is None:
                kind = added.get(element_path) or removed.get(element_path)
            if kind == "camera":
                cameras.add(element_id)
            else:
                visible = True
    return StepScope(visible=visible, cameras=frozenset(cameras))


def _current_type(project: Project, level_id: str, element_id: str) -> str | None:
    try:
        level = project.levels.get(UUID(level_id))
        element = level.elements.get(UUID(element_id)) if level is not None else None
    except ValueError:
        return None
    return element.type if element is not None else None


def latest_view_renders(renders: Iterable[RenderEntry]) -> dict[UUID, RenderEntry]:
    """Dernier rendu de chaque vue (pas les vignettes de variantes)."""
    latest: dict[UUID, RenderEntry] = {}
    for entry in renders:
        if entry.variant is not None or entry.camera_id is None:
            continue
        known = latest.get(entry.camera_id)
        if known is None or entry.created_at > known.created_at:
            latest[entry.camera_id] = entry
    return latest


def stale_cameras(project: Project, renders: Iterable[RenderEntry]) -> set[UUID]:
    """Caméras dont la vue est périmée (une annulation ne remet pas une vue « à jour »)."""
    existing = {
        e.id
        for level in project.levels.values()
        for e in level.elements.values()
        if e.type == "camera"
    }
    latest = {c: r for c, r in latest_view_renders(renders).items() if c in existing}
    if not latest:
        return set()
    scoped: list[tuple[datetime, StepScope]] = [
        (e.timestamp, step_scope(e, project)) for e in project.history[: project.history_cursor]
    ]
    stale: set[UUID] = set()
    for camera_id, render in latest.items():
        for at, scope in scoped:
            if at > render.created_at and (scope.visible or str(camera_id) in scope.cameras):
                stale.add(camera_id)
                break
    return stale
