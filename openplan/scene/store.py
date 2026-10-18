"""Stockage local des projets : un dossier par projet, supprimable d'un bloc.

Disposition : <root>/<project_id>/project.json, images/, runs/, thumbnail.jpg (vignette,
écrite par l'import d'image) ; <root>/index.sqlite, index des projets (roadmap 2.7).
Les identifiants sont des UUID : aucun chemin fourni par l'utilisateur n'entre dans
la construction des dossiers.

L'index (sqlite3 de la bibliothèque standard, aucune dépendance) ne contient que ce que la
liste des projets affiche : nom, dates, nombre de photos et de murs, présence d'une vignette,
et la date de modification du fichier. Il se répare tout seul : `list()` compare les dates
des fichiers `project.json` à celles de l'index et relit ce qui a changé (projet copié à la
main, index supprimé, écriture concurrente) ; le fichier `project.json` reste la vérité.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from openplan.scene.migrate import load_project
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Wall
from openplan.schemas.scene import Project

PROJECT_FILE = "project.json"
IMAGES_DIR = "images"
RUNS_DIR = "runs"
THUMBNAIL_FILE = "thumbnail.jpg"
INDEX_FILE = "index.sqlite"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  image_count INTEGER NOT NULL,
  wall_count INTEGER NOT NULL,
  has_thumbnail INTEGER NOT NULL,
  mtime REAL NOT NULL
)
"""


class ProjectNotFoundError(FileNotFoundError):
    """Aucun projet avec cet identifiant dans le store."""


class ProjectSummary(StrictModel):
    id: UUID
    name: str
    updated_at: datetime
    path: Path
    created_at: datetime | None = None
    image_count: int = 0
    wall_count: int = 0
    has_thumbnail: bool = False


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._locks: dict[UUID, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / INDEX_FILE
        with self._connect() as con:
            con.execute(_SCHEMA)

    def project_dir(self, project_id: UUID) -> Path:
        return self.root / str(project_id)

    def images_dir(self, project_id: UUID) -> Path:
        return self.project_dir(project_id) / IMAGES_DIR

    def runs_dir(self, project_id: UUID) -> Path:
        return self.project_dir(project_id) / RUNS_DIR

    def thumbnail_path(self, project_id: UUID) -> Path:
        return self.project_dir(project_id) / THUMBNAIL_FILE

    def exists(self, project_id: UUID) -> bool:
        return (self.project_dir(project_id) / PROJECT_FILE).is_file()

    def lock(self, project_id: UUID) -> threading.Lock:
        """Verrou d'un projet : à tenir autour de charger → modifier → sauver quand deux
        requêtes peuvent se suivre de près (deux clics de mur), sinon la seconde écrase la
        première."""
        with self._locks_guard:
            return self._locks.setdefault(project_id, threading.Lock())

    def save(self, project: Project) -> Path:
        """Écriture atomique : fichier temporaire puis remplacement ; puis l'index."""
        directory = self.project_dir(project.id)
        (directory / IMAGES_DIR).mkdir(parents=True, exist_ok=True)
        (directory / RUNS_DIR).mkdir(exist_ok=True)
        target = directory / PROJECT_FILE
        # Un nom temporaire par écriture : deux sauvegardes proches ne se volent pas le fichier.
        tmp = directory / f"{PROJECT_FILE}.{uuid4().hex}.tmp"
        tmp.write_text(project.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
        self._index(project, target)
        return target

    def load(self, project_id: UUID) -> Project:
        target = self.project_dir(project_id) / PROJECT_FILE
        if not target.is_file():
            raise ProjectNotFoundError(str(project_id))
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            msg = f"{target} ne contient pas un objet JSON"
            raise ValueError(msg)
        return load_project(data)

    def list(self) -> list[ProjectSummary]:
        """Projets présents, du plus récemment modifié au plus ancien, depuis l'index
        (réconcilié avec le disque au passage)."""
        self.reconcile()
        with self._connect() as con:
            rows = con.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
        return [self._summary(row) for row in rows]

    def reconcile(self) -> int:
        """Aligne l'index sur le disque : relit les `project.json` nouveaux ou modifiés
        depuis l'indexation, oublie les dossiers disparus. Renvoie le nombre de changements."""
        with self._connect() as con:
            known = {
                row["id"]: row["mtime"] for row in con.execute("SELECT id, mtime FROM projects")
            }
        present: set[str] = set()
        changes = 0
        for directory in self.root.iterdir():
            target = directory / PROJECT_FILE
            if not target.is_file():
                continue
            present.add(directory.name)
            mtime = target.stat().st_mtime
            indexed = known.get(directory.name)
            if indexed is not None and abs(indexed - mtime) < 1e-6:
                continue
            try:
                project = load_project(json.loads(target.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                continue  # fichier en cours d'écriture ou corrompu : il reste hors index
            self._index(project, target)
            changes += 1
        stale = [pid for pid in known if pid not in present]
        if stale:
            with self._connect() as con:
                con.executemany("DELETE FROM projects WHERE id = ?", [(pid,) for pid in stale])
            changes += len(stale)
        return changes

    def delete(self, project_id: UUID) -> None:
        """Supprime le projet entier : scène, images, runs, vignette, et sa ligne d'index."""
        directory = self.project_dir(project_id)
        if not directory.is_dir():
            raise ProjectNotFoundError(str(project_id))
        shutil.rmtree(directory)
        with self._connect() as con:
            con.execute("DELETE FROM projects WHERE id = ?", (str(project_id),))

    # --- index ---------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.index_path)
        con.row_factory = sqlite3.Row
        return con

    def _index(self, project: Project, target: Path) -> None:
        wall_count = sum(
            1
            for level in project.levels.values()
            if level.variant_of is None  # T7e : une variante ne double pas les murs
            for element in level.elements.values()
            if isinstance(element, Wall)
        )
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO projects
                  (id, name, created_at, updated_at, image_count, wall_count, has_thumbnail, mtime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  name = excluded.name, created_at = excluded.created_at,
                  updated_at = excluded.updated_at, image_count = excluded.image_count,
                  wall_count = excluded.wall_count, has_thumbnail = excluded.has_thumbnail,
                  mtime = excluded.mtime
                """,
                (
                    str(project.id),
                    project.name,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    len(project.images),
                    wall_count,
                    int(self.thumbnail_path(project.id).is_file()),
                    target.stat().st_mtime,
                ),
            )

    def _summary(self, row: sqlite3.Row) -> ProjectSummary:
        project_id = UUID(row["id"])
        return ProjectSummary(
            id=project_id,
            name=row["name"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            path=self.project_dir(project_id) / PROJECT_FILE,
            image_count=int(row["image_count"]),
            wall_count=int(row["wall_count"]),
            has_thumbnail=bool(row["has_thumbnail"]),
        )
