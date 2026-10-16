"""Store local : sauvegarde atomique, chargement, liste, suppression complète."""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from openplan.scene.store import PROJECT_FILE, ProjectNotFoundError, ProjectStore
from openplan.schemas import Project
from tests.factories import rect_room_project


def test_save_then_load_is_lossless(store: ProjectStore, rect_project: Project) -> None:
    path = store.save(rect_project)
    assert path.name == PROJECT_FILE
    assert store.exists(rect_project.id)
    assert store.load(rect_project.id) == rect_project
    assert not list(path.parent.glob("*.tmp")), "pas de fichier temporaire résiduel"


def test_save_creates_images_and_runs_dirs(store: ProjectStore, rect_project: Project) -> None:
    store.save(rect_project)
    assert store.images_dir(rect_project.id).is_dir()
    assert store.runs_dir(rect_project.id).is_dir()


def test_load_unknown_project_raises(store: ProjectStore) -> None:
    with pytest.raises(ProjectNotFoundError):
        store.load(uuid4())


def test_list_is_sorted_by_recent_update(store: ProjectStore) -> None:
    older = rect_room_project(name="ancien").model_copy(
        update={"updated_at": datetime(2026, 1, 1, tzinfo=UTC)}
    )
    newer = rect_room_project(name="récent").model_copy(
        update={"updated_at": datetime(2026, 1, 2, tzinfo=UTC)}
    )
    store.save(older)
    store.save(newer)
    names = [s.name for s in store.list()]
    assert names == ["récent", "ancien"]


def test_list_ignores_foreign_dirs(store: ProjectStore, rect_project: Project) -> None:
    (store.root / "pas-un-projet").mkdir()
    store.save(rect_project)
    assert [s.id for s in store.list()] == [rect_project.id]


def test_delete_removes_everything(store: ProjectStore, rect_project: Project) -> None:
    store.save(rect_project)
    (store.images_dir(rect_project.id) / "photo.jpg").write_bytes(b"\xff\xd8")
    store.delete(rect_project.id)
    assert not store.project_dir(rect_project.id).exists()
    assert _rows(store) == []
    with pytest.raises(ProjectNotFoundError):
        store.delete(rect_project.id)


def _rows(store: ProjectStore) -> list[str]:
    with sqlite3.connect(store.index_path) as con:
        return [row[0] for row in con.execute("SELECT id FROM projects")]


def test_index_holds_what_the_list_shows(store: ProjectStore, rect_project: Project) -> None:
    assert store.index_path.is_file() and _rows(store) == []
    store.thumbnail_path(rect_project.id).parent.mkdir(parents=True)
    store.thumbnail_path(rect_project.id).write_bytes(b"\xff\xd8\xff")
    store.save(rect_project)
    assert _rows(store) == [str(rect_project.id)]
    (summary,) = store.list()
    assert summary.name == rect_project.name and summary.created_at == rect_project.created_at
    assert summary.wall_count == 4 and summary.image_count == 0 and summary.has_thumbnail
    assert summary.path == store.project_dir(rect_project.id) / PROJECT_FILE


def test_index_reconciles_with_the_disk(store: ProjectStore, rect_project: Project) -> None:
    """Projet copié à la main, projet supprimé hors du store, fichier modifié : l'index suit."""
    other = rect_room_project(name="copié à la main")
    manual = store.project_dir(other.id)
    manual.mkdir()
    (manual / PROJECT_FILE).write_text(other.model_dump_json(), encoding="utf-8")
    store.save(rect_project)
    assert {s.name for s in store.list()} == {rect_project.name, "copié à la main"}
    assert store.reconcile() == 0, "rien de nouveau : aucune relecture"
    # Modification hors store (date future pour battre la résolution des horloges).
    renamed = other.model_copy(update={"name": "renommé"})
    (manual / PROJECT_FILE).write_text(renamed.model_dump_json(), encoding="utf-8")
    os.utime(manual / PROJECT_FILE, (time.time() + 5, time.time() + 5))
    assert store.reconcile() == 1
    assert "renommé" in {s.name for s in store.list()}
    shutil.rmtree(manual)
    assert [s.name for s in store.list()] == [rect_project.name]
    # Index supprimé : reconstruit à l'ouverture suivante.
    store.index_path.unlink()
    again = ProjectStore(store.root)
    assert [s.id for s in again.list()] == [rect_project.id]
    # Fichier illisible : ignoré, pas d'exception.
    broken = store.root / str(uuid4())
    broken.mkdir()
    (broken / PROJECT_FILE).write_text("{pas du json", encoding="utf-8")
    assert [s.id for s in again.list()] == [rect_project.id]


def test_lock_is_per_project(store: ProjectStore) -> None:
    a, b = uuid4(), uuid4()
    assert store.lock(a) is store.lock(a)
    assert store.lock(a) is not store.lock(b)
    with store.lock(a):
        assert store.lock(a).locked() and not store.lock(b).locked()
