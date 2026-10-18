"""Couche de stockage (note 17) : clés sûres, disque, rien hors de la racine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from openplan.scene.storage import (
    DiskStorage,
    Storage,
    StorageKeyError,
    StoredFileNotFoundError,
    check_key,
)


def test_keys_are_relative_posix_paths_without_escape() -> None:
    assert check_key("projects/abc/images/photo.jpg") == "projects/abc/images/photo.jpg"
    for bad in ("", "/etc/passwd", "a/../b", "..", "a//b", "a/./b", "a\\b", "a/b\x00", "a/"):
        with pytest.raises(StorageKeyError):
            check_key(bad)


def test_disk_round_trip_list_and_delete(tmp_path: Path) -> None:
    storage = DiskStorage(tmp_path / "data")
    assert isinstance(storage, Storage)
    storage.write_bytes("projects/p1/images/a.jpg", b"aaa")
    storage.write_bytes("projects/p1/runs/r/preview.png", b"pp")
    storage.write_bytes("projects/p2/project.json", b"{}")
    assert storage.exists("projects/p1/images/a.jpg")
    assert storage.read_bytes("projects/p1/images/a.jpg") == b"aaa"
    with storage.open_read("projects/p1/runs/r/preview.png") as handle:
        assert handle.read() == b"pp"
    assert storage.size("projects/p1/images/a.jpg") == 3
    assert storage.list("projects/p1") == [
        "projects/p1/images/a.jpg",
        "projects/p1/runs/r/preview.png",
    ]
    # Écriture atomique : pas de fichier temporaire qui traîne, remplacement complet.
    storage.write_bytes("projects/p1/images/a.jpg", b"b")
    assert storage.read_bytes("projects/p1/images/a.jpg") == b"b"
    assert not [p for p in (tmp_path / "data").rglob("*.tmp")]
    storage.delete("projects/p1/images/a.jpg")
    assert not storage.exists("projects/p1/images/a.jpg")
    with pytest.raises(StoredFileNotFoundError):
        storage.read_bytes("projects/p1/images/a.jpg")
    assert storage.delete_prefix("projects/p1") == 1
    assert storage.list("projects/p1") == []
    assert storage.list("projects") == ["projects/p2/project.json"]


def test_paths_outside_the_root_are_refused(tmp_path: Path) -> None:
    storage = DiskStorage(tmp_path / "data")
    (tmp_path / "secret.txt").write_text("x")
    with pytest.raises(StorageKeyError):
        storage.key_of(tmp_path / "secret.txt")
    with pytest.raises(StorageKeyError):
        storage.key_of(tmp_path / "data")
    # Un lien symbolique ne sort pas de la racine.
    (tmp_path / "data" / "lien").symlink_to(tmp_path)
    with pytest.raises(StorageKeyError):
        storage.read_bytes("lien/secret.txt")
    with pytest.raises(StorageKeyError):
        storage.key_of(tmp_path / "data" / "lien" / "secret.txt")
    storage.write_bytes("projects/p/x.bin", b"1")
    path = storage.local_path("projects/p/x.bin")
    assert path is not None and storage.key_of(path) == "projects/p/x.bin"


def test_local_mode_never_imports_the_accounts_stack(tmp_path: Path) -> None:
    """Sans OPENPLAN_DATABASE_URL : ni SQLAlchemy, ni Alembic, ni pilote chargés, et les
    projets restent directement dans le dossier des données (mode local inchangé)."""
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from openplan.api.app import create_app\n"
        "from openplan.api.settings import Settings\n"
        f"app = create_app(settings=Settings(data_dir=Path({str(tmp_path / 'd')!r})))\n"
        f"assert app.state.store.root == Path({str(tmp_path / 'd')!r})\n"
        "assert app.state.accounts is None\n"
        "bad = [m for m in ('sqlalchemy', 'alembic', 'psycopg') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr[-2000:]
