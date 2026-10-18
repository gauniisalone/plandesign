"""Journal local des demandes (V2, note 10, § 2) : de quoi ajuster un jour un petit modèle
ouvert à la traduction des consignes, **sans entraînement maintenant**.

- **Où** : dans le dossier du projet, `journal/design.jsonl`, une ligne JSON par événement.
- **Quoi** : la demande (consigne, phrase à l'assistant), sa traduction (champs de l'ambiance,
  intentions), qui l'a traduite (« phrases » ou le modèle), ce qui en est sorti (identifiants
  internes des éléments posés, des rendus, des pas d'historique), puis ce que l'utilisateur en
  a fait : gardé (variante appliquée), corrigé (champ de l'ambiance changé, meuble déplacé ou
  retiré), annulé (Annuler, rattaché à la demande qui avait produit le pas annulé).
- **Jamais** : aucune photo, aucune image, aucune coordonnée (ni de la photo ni du plan),
  aucune clé, aucun nom de fichier. Seulement du texte et des identifiants internes ; une ligne
  qui en contiendrait est refusée avant écriture (`_assert_clean`).
- **Rien ne quitte la machine** : pas d'envoi, pas de télémétrie (`privacy.telemetry` reste
  sans effet ici).
- **Désactivable** : `privacy.design_journal` (actif par défaut) ; coupé, rien n'est écrit ;
  « Vider » efface le journal du projet.
- **Export** : `openplan journal export <projet|--all>` (JSONL, schéma `JournalEvent` v1).
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, JsonValue

from openplan.schemas.common import SCHEMA_VERSION, StrictModel, now_utc
from openplan.schemas.scene import HistoryEntry, Project

JOURNAL_DIR = "journal"
JOURNAL_FILE = "design.jsonl"
JOURNAL_VERSION = 1
EventKind = Literal[
    "instruction",
    "assistant",
    "autopilot",
    "layout_applied",
    "intent_corrected",
    "furniture_moved",
    "furniture_removed",
    "furniture_replaced",
    "undo",
    "tab_changed",
    "refine_clicked",
    "render_requested",
    "render_picked",
    "generate_clicked",
    "viewer_opened",
]
UiEvent = Literal[
    "tab_changed", "refine_clicked", "render_requested", "generate_clicked", "viewer_opened"
]
"""Mesure du parcours (P0) : gestes d'interface sans effet sur le plan, envoyés par l'atelier
(`POST /projects/{id}/journal/ui`) ; le détail est un mot d'un vocabulaire fermé."""
EVENT_LABELS: dict[str, str] = {
    "instruction": "consigne traduite en ambiance",
    "assistant": "phrase à l'assistant",
    "autopilot": "chemin automatique",
    "layout_applied": "variante gardée",
    "intent_corrected": "ambiance corrigée",
    "furniture_moved": "meuble déplacé",
    "furniture_removed": "meuble retiré",
    "furniture_replaced": "meuble remplacé",
    "undo": "annulé",
    "tab_changed": "onglet changé",
    "refine_clicked": "« Affiner » cliqué",
    "render_requested": "rendu demandé",
    "render_picked": "élément désigné dans un rendu",
    "generate_clicked": "« Générer » cliqué (plan ou chemin automatique lancé à la main)",
    "viewer_opened": "visionneuse des rendus ouverte (T7d2 ; ce n'est pas un geste)",
}
_lock = threading.Lock()


class JournalEvent(StrictModel):
    """Une ligne du journal (schéma v1, documenté et versionné)."""

    version: Literal[1] = 1
    id: UUID = Field(default_factory=uuid4)
    at: datetime = Field(default_factory=now_utc)
    schema_version: str = SCHEMA_VERSION
    project_id: UUID
    kind: EventKind
    text: str | None = Field(default=None, max_length=500)
    """La demande telle qu'écrite (consigne, phrase) ; None pour un geste."""
    translation: dict[str, JsonValue] | None = None
    """Champs de l'ambiance ou intentions de l'assistant, tels que traduits."""
    by: str | None = Field(default=None, max_length=80)
    """« phrases », « llm:<modèle> », « pipeline:autopilot »… ; None pour un geste."""
    fields: list[str] = Field(default_factory=list)
    """Champs touchés (correction de l'ambiance)."""
    element_ids: list[UUID] = Field(default_factory=list)
    run_ids: list[UUID] = Field(default_factory=list)
    history_ids: list[UUID] = Field(default_factory=list)
    """Pas d'historique produits par cette demande : une annulation s'y rattache."""
    request_id: UUID | None = None
    """Pour un geste qui suit une demande (annulation, correction) : la demande d'origine."""
    note: str | None = Field(default=None, max_length=200)


def journal_path(project_dir: Path) -> Path:
    return project_dir / JOURNAL_DIR / JOURNAL_FILE


_PATHISH = re.compile(
    r"(?i)(/home/|/mnt/|[a-z]:\\|\.jpe?g\b|\.png\b|\.heic\b|\.webp\b|\.pdf\b|\.dxf\b)"
)
_FORBIDDEN_KEYS = frozenset(
    {"x", "y", "z", "position", "points", "polygon", "image", "image_id", "pixels", "path", "file"}
)


class JournalLeakError(ValueError):
    """Une ligne contiendrait une image, une coordonnée ou un chemin : elle n'est pas écrite."""


def _assert_clean(value: object, where: str = "") -> None:
    if isinstance(value, dict):
        for key, inner in value.items():
            if str(key) in _FORBIDDEN_KEYS:
                msg = f"clé interdite dans le journal : {where}{key}"
                raise JournalLeakError(msg)
            _assert_clean(inner, f"{where}{key}.")
    elif isinstance(value, list):
        for inner in value:
            _assert_clean(inner, where)
    elif isinstance(value, str) and _PATHISH.search(value):
        msg = f"chemin ou nom d'image dans le journal : {where}"
        raise JournalLeakError(msg)


def history_ids(before: Project, after: Project) -> list[UUID]:
    """Les pas d'historique ajoutés par une demande."""
    start = before.history_cursor
    return [e.id for e in after.history[start : after.history_cursor]]


def record(project_dir: Path, project: Project, event: JournalEvent) -> bool:
    """Ajoute l'événement si le journal du projet est actif ; renvoie s'il a été écrit."""
    if not project.privacy.design_journal:
        return False
    data = event.model_dump(mode="json")
    try:
        _assert_clean({k: v for k, v in data.items() if k in ("text", "translation", "note")})
    except JournalLeakError:
        return False  # la ligne n'est pas écrite ; la demande, elle, aboutit

    path = journal_path(project_dir)
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
    return True


def read(project_dir: Path) -> list[JournalEvent]:
    path = journal_path(project_dir)
    if not path.is_file():
        return []
    out: list[JournalEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(JournalEvent.model_validate_json(line))
    return out


def clear(project_dir: Path) -> int:
    """Vide le journal du projet ; renvoie le nombre de lignes effacées."""
    events = read(project_dir)
    path = journal_path(project_dir)
    with _lock:
        path.unlink(missing_ok=True)
    return len(events)


def record_undo(project_dir: Path, project_before: Project, project_after: Project) -> bool:
    """Annuler : rattache le pas annulé à la demande qui l'avait produit (si elle est au
    journal)."""
    if project_before.history_cursor <= project_after.history_cursor:
        return False
    undone: HistoryEntry = project_before.history[project_before.history_cursor - 1]
    origin = next(
        (e for e in reversed(read(project_dir)) if undone.id in e.history_ids and e.kind != "undo"),
        None,
    )
    return record(
        project_dir,
        project_after,
        JournalEvent(
            project_id=project_after.id,
            kind="undo",
            history_ids=[undone.id],
            request_id=origin.id if origin else None,
            note=undone.label[:200],
        ),
    )


def export_lines(project_dirs: list[Path]) -> Iterator[str]:
    """Les lignes des journaux, projet par projet, telles qu'écrites (JSONL)."""
    for directory in project_dirs:
        for event in read(directory):
            yield event.model_dump_json()
