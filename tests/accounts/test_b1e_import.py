"""B1e : `openplan accounts import` (note 17, § 9) — projets de la machine copiés vers le
serveur à comptes, fichiers lourds laissés, confirmation, propriétaire existant."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from openplan.api.accounts.db import make_engine
from openplan.api.accounts.importer import (
    ImportRefusedError,
    find_projects,
    run_import,
)
from openplan.api.accounts.repo import AccountsRepo


def _project(root: Path, name: str, updated: str) -> UUID:
    pid = uuid4()
    d = root / str(pid)
    (d / "images").mkdir(parents=True)
    (d / "cover").mkdir()
    (d / "project.json").write_text(
        json.dumps({"id": str(pid), "name": name, "updated_at": updated, "images": {"a": {}}}),
        encoding="utf-8",
    )
    (d / "images" / "a.jpg").write_bytes(b"j" * 100)
    (d / "thumbnail.jpg").write_bytes(b"t" * 10)
    (d / "cover" / "light.png").write_bytes(b"c" * 10)
    run = d / "runs" / "r1"
    (run / "blender").mkdir(parents=True)
    (run / "render.png").write_bytes(b"r" * 50)
    (run / "meta.json").write_text("{}", encoding="utf-8")
    (run / "scene.glb").write_bytes(b"g" * 20)
    (run / "blender" / "scene.blend").write_bytes(b"b" * 5000)
    (run / "depth.npy").write_bytes(b"d" * 3000)
    trash = d / "runs" / ".trash" / "old"
    trash.mkdir(parents=True)
    (trash / "render.png").write_bytes(b"x" * 70)
    return pid


def test_listing_and_heavy_files_left_behind(tmp_path: Path) -> None:
    src = tmp_path / "machine"
    older = _project(src, "Grenier", "2026-10-01T10:00:00Z")
    newer = _project(src, "Salon", "2026-10-16T10:00:00Z")
    (src / "pas-un-projet").mkdir()
    found = find_projects(src)
    assert [c.id for c in found] == [newer, older]  # le plus récent d'abord
    salon = found[0]
    assert salon.photos == 1 and salon.renders == 1
    assert salon.left_bytes == 5000 + 3000 + 70


def test_import_copies_rows_and_skips_existing(tmp_path: Path, migrated_url: str) -> None:
    repo = AccountsRepo(make_engine(migrated_url))
    owner = repo.create_user("augus@example.org")
    src = tmp_path / "machine"
    keep = _project(src, "Grenier", "2026-10-01")
    other = _project(src, "Salon", "2026-10-16")
    dest = tmp_path / "data" / "projects"
    lines: list[str] = []
    answers = iter(["n", "o"])  # Salon (le plus récent) : non ; Grenier : oui
    done = run_import(
        repo, "Augus@Example.org", src, dest, ask=lambda _: next(answers), say=lines.append
    )
    assert done == [keep]
    copied = sorted(
        p.relative_to(dest / str(keep)).as_posix()
        for p in (dest / str(keep)).rglob("*")
        if p.is_file()
    )
    assert copied == [
        "cover/light.png",
        "images/a.jpg",
        "project.json",
        "runs/r1/meta.json",
        "runs/r1/render.png",
        "runs/r1/scene.glb",
        "thumbnail.jpg",
    ]
    assert repo.role_name(keep, owner.id) == "owner" and repo.project_exists(keep)
    assert not (dest / str(other)).exists()
    assert (
        src / str(keep) / "runs" / "r1" / "blender" / "scene.blend"
    ).exists()  # rien n'est retiré
    # Relance avec --yes : le projet déjà importé est sauté, l'autre part.
    lines.clear()
    again = run_import(repo, "augus@example.org", src, dest, yes=True, say=lines.append)
    assert again == [other]
    assert any("sauté" in line and "Grenier" in line for line in lines)


def test_only_quit_and_refusals(tmp_path: Path, migrated_url: str) -> None:
    repo = AccountsRepo(make_engine(migrated_url))
    repo.create_user("augus@example.org")
    src = tmp_path / "machine"
    a = _project(src, "A", "2026-10-01")
    _project(src, "B", "2026-10-02")
    dest = tmp_path / "projects"
    assert run_import(
        repo, "augus@example.org", src, dest, only=[str(a)[:8]], yes=True, say=lambda _: None
    ) == [a]
    assert (
        run_import(repo, "augus@example.org", src, dest, ask=lambda _: "q", say=lambda _: None)
        == []
    )
    with pytest.raises(ImportRefusedError, match="aucun compte"):
        run_import(repo, "inconnu@example.org", src, dest, yes=True, say=lambda _: None)
    with pytest.raises(ImportRefusedError, match="aucun projet"):
        run_import(
            repo, "augus@example.org", src, dest, only=["zzzz"], yes=True, say=lambda _: None
        )
    with pytest.raises(ImportRefusedError, match="introuvable"):
        run_import(repo, "augus@example.org", tmp_path / "rien", dest, yes=True, say=lambda _: None)
