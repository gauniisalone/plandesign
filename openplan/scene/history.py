"""Historique par patches JSON (RFC 6902) : diff, application, annuler, rétablir.

Politique de priorité des sources (docs/architecture/01 §1) : un acteur autre que
l'utilisateur ne peut pas modifier un élément verrouillé, abaisser une source, supprimer
un élément d'autorité utilisateur, créer un élément prétendument mesuré, ni toucher aux
mesures. Seul l'acteur "user" a ces droits.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

import jsonpatch

from openplan.schemas.common import USER_AUTHORITATIVE, Source, now_utc, source_rank
from openplan.schemas.scene import HistoryEntry, JsonPatch, Project

USER_ACTOR = "user"
USER_PREFIX = "user:"
"""Acteur d'un utilisateur identifié par un jeton nommé : `user:<nom>` (mêmes droits)."""
_META_FIELDS = frozenset({"history", "history_cursor", "updated_at"})


def user_actor(name: str) -> str:
    return f"{USER_PREFIX}{name}"


def is_user(actor: str) -> bool:
    """Un geste humain (anonyme ou nommé), par opposition à un `pipeline:*`."""
    return actor == USER_ACTOR or actor.startswith(USER_PREFIX)


class HistoryError(ValueError):
    """Rien à annuler ou à rétablir."""


class PatchRejectedError(ValueError):
    """Le changement viole la politique de priorité des sources."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("changement refusé :\n- " + "\n- ".join(problems))


class _Governed(Protocol):
    @property
    def source(self) -> Source: ...
    @property
    def locked(self) -> bool: ...


def content(project: Project) -> dict[str, Any]:
    """Représentation JSON du projet sans ses métadonnées d'historique."""
    return project.model_dump(mode="json", exclude=set(_META_FIELDS))


def diff(before: Project, after: Project) -> JsonPatch:
    """Patch JSON transformant le contenu de `before` en celui de `after`."""
    patch: list[dict[str, Any]] = jsonpatch.make_patch(content(before), content(after)).patch
    return patch


def apply(project: Project, patch: JsonPatch) -> Project:
    """Applique un patch au contenu ; l'historique du projet est conservé tel quel."""
    doc: dict[str, Any] = jsonpatch.apply_patch(content(project), patch, in_place=False)
    meta = project.model_dump(mode="json", include=set(_META_FIELDS))
    doc.update(meta)
    return Project.model_validate(doc)


def check_policy(before: Project, after: Project, actor: str) -> None:
    """Lève PatchRejectedError si `actor` n'a pas le droit de faire ce changement."""
    if is_user(actor):
        return
    problems: list[str] = []
    if before.measurements != after.measurements:
        problems.append(f"{actor} ne peut pas modifier les mesures")
    for level_id, lvl_before in before.levels.items():
        lvl_after = after.levels.get(level_id)
        if lvl_after is None:
            problems.append(f"{actor} ne peut pas supprimer le niveau {level_id}")
            continue
        height_changed = (
            lvl_after.height != lvl_before.height
            or lvl_after.height_source != lvl_before.height_source
        )
        if lvl_before.height_source in USER_AUTHORITATIVE and height_changed:
            problems.append(f"la hauteur du niveau {level_id} fait autorité utilisateur")
        _check_items(lvl_before.vertices, lvl_after.vertices, "sommet", actor, problems)
        _check_items(lvl_before.elements, lvl_after.elements, "élément", actor, problems)
    if problems:
        raise PatchRejectedError(problems)


def _check_items(
    before: Mapping[UUID, _Governed],
    after: Mapping[UUID, _Governed],
    label: str,
    actor: str,
    problems: list[str],
) -> None:
    for item_id, b in before.items():
        a = after.get(item_id)
        if a is None:
            if b.locked or b.source in USER_AUTHORITATIVE:
                problems.append(f"{actor} ne peut pas supprimer le {label} {item_id} ({b.source})")
            continue
        if a == b:
            continue
        if b.locked:
            problems.append(f"le {label} {item_id} est verrouillé")
        elif source_rank(a.source) < source_rank(b.source):
            problems.append(
                f"le {label} {item_id} passerait de {b.source} à {a.source} (priorité abaissée)"
            )
    for item_id, a in after.items():
        if item_id not in before and a.source in USER_AUTHORITATIVE:
            problems.append(f"{actor} ne peut pas créer le {label} {item_id} en {a.source}")


def commit(
    before: Project,
    after: Project,
    *,
    actor: str,
    label: str,
    run_id: UUID | None = None,
) -> Project:
    """Enregistre le passage de `before` à `after` comme entrée d'historique.

    Renvoie un nouveau Project. Les entrées « à rétablir » au-delà du curseur sont
    abandonnées. Un changement vide ne crée pas d'entrée.
    """
    check_policy(before, after, actor)
    patch = diff(before, after)
    if not patch:
        return before
    entry = HistoryEntry(
        actor=actor,
        label=label,
        patch=patch,
        inverse_patch=diff(after, before),
        run_id=run_id,
    )
    history = [*before.history[: before.history_cursor], entry]
    return after.model_copy(
        update={"history": history, "history_cursor": len(history), "updated_at": now_utc()}
    )


def can_undo(project: Project) -> bool:
    return project.history_cursor > 0


def can_redo(project: Project) -> bool:
    return project.history_cursor < len(project.history)


def undo(project: Project) -> Project:
    if not can_undo(project):
        msg = "rien à annuler"
        raise HistoryError(msg)
    entry = project.history[project.history_cursor - 1]
    restored = apply(project, entry.inverse_patch)
    return restored.model_copy(
        update={"history_cursor": project.history_cursor - 1, "updated_at": now_utc()}
    )


def redo(project: Project) -> Project:
    if not can_redo(project):
        msg = "rien à rétablir"
        raise HistoryError(msg)
    entry = project.history[project.history_cursor]
    restored = apply(project, entry.patch)
    return restored.model_copy(
        update={"history_cursor": project.history_cursor + 1, "updated_at": now_utc()}
    )
