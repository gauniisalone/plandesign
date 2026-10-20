"""Commentaires (KAN-51) : lecture et écriture de `comments.json`, et journal des événements
`events.jsonl` (une ligne par geste, pour la future cloche). Fichiers du projet, écrits sous le
verrou du projet par l'appelant ; même code en local et avec les comptes."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from openplan.schemas.comments import CommentEvent, CommentFile

COMMENTS_FILE = "comments.json"
EVENTS_FILE = "events.jsonl"


class CommentStore:
    """Les fils d'un projet, dans son dossier. Une seule porte, pour pouvoir passer un jour à une
    autre mémoire sans toucher aux routes."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir

    def load(self) -> CommentFile:
        path = self.project_dir / COMMENTS_FILE
        if not path.is_file():
            return CommentFile()
        return CommentFile.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, comments: CommentFile) -> None:
        """Écriture atomique (fichier temporaire, puis remplacement)."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.project_dir / f"{COMMENTS_FILE}.{uuid4().hex}.tmp"
        tmp.write_text(comments.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.project_dir / COMMENTS_FILE)

    def publish(self, event: CommentEvent) -> None:
        """Ajoute une ligne au journal des événements du projet."""
        self.project_dir.mkdir(parents=True, exist_ok=True)
        with (self.project_dir / EVENTS_FILE).open("a", encoding="utf-8") as out:
            out.write(event.model_dump_json() + "\n")
