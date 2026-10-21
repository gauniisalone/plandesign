"""Réactions (KAN-78) : lecture et écriture de `reactions.json`, sous le verrou du projet par
l'appelant ; même code en local et avec les comptes. Le fichier est dans le dossier du projet :
il compte dans la place du compte (KAN-12) comme le reste."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import UUID, uuid4

from openplan.schemas.reactions import ReactionFile

REACTIONS_FILE = "reactions.json"


class ReactionStore:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def load(self) -> ReactionFile:
        path = self.project_dir / REACTIONS_FILE
        if not path.is_file():
            return ReactionFile()
        return ReactionFile.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, reactions: ReactionFile) -> None:
        """Écriture atomique (fichier temporaire, puis remplacement)."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.project_dir / f"{REACTIONS_FILE}.{uuid4().hex}.tmp"
        tmp.write_text(reactions.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.project_dir / REACTIONS_FILE)

    def forget(self, gone: Iterable[UUID]) -> None:
        """Un élément retiré de l'espace de travail emporte ses réactions."""
        gone = set(gone)
        if not gone or not (self.project_dir / REACTIONS_FILE).is_file():
            return
        reactions = self.load()
        kept = [r for r in reactions.reactions if r.item_id not in gone]
        if len(kept) != len(reactions.reactions):
            self.save(ReactionFile(reactions=kept))
