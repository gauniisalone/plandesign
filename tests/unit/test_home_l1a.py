"""Accueil L1a : consigne passée au chemin automatique, nom automatique du projet, aperçu
« Compris : … », renommage des anciens noms de fichier à l'ouverture."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline import autopilot as ap
from openplan.pipeline.autopilot import HomeRequest, Workspace, run_autopilot
from openplan.pipeline.naming import auto_name, looks_like_file_name, rename
from openplan.scene.store import ProjectStore
from openplan.schemas.elements import Room
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


class _Store:
    def __init__(self, project: Project) -> None:
        self.project = project

    def load(self) -> Project:
        return self.project

    def save(self, project: Project) -> None:
        self.project = project


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore(tmp_path / "data")
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> list[UUID]:
    rendered: list[UUID] = []

    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")
        rendered.append(camera_id)

    monkeypatch.setattr(ap, "render_camera_view", render)
    return rendered


def _created(project: Project, day: datetime) -> Project:
    return project.model_copy(update={"created_at": day})


@pytest.mark.parametrize(
    ("name", "file_like"),
    [
        ("WhatsApp Image 2026 08 25 at 13.36.24 (1)", True),
        ("IMG 2041", True),
        ("PXL_20260825_133624", True),
        ("20260825_133624", True),
        ("Grenier", False),
        ("Maison de campagne 2026", False),
        ("Test", False),
    ],
)
def test_file_names_are_recognised(name: str, file_like: bool) -> None:
    assert looks_like_file_name(name) is file_like


def test_auto_name_uses_the_room_and_the_paris_date() -> None:
    project = _created(attic_project(camera=False), datetime(2026, 10, 5, 23, 30, tzinfo=UTC))
    # 23 h 30 UTC = 6 octobre à Paris.
    assert auto_name(project) == "Grenier · 6 oct."
    assert auto_name(project, ["Grenier · 6 oct."]) == "Grenier · 6 oct. (2)"
    assert auto_name(project, ["Grenier · 6 oct.", "Grenier · 6 oct. (2)"]).endswith("(3)")


def test_rename_never_touches_a_hand_given_name() -> None:
    manual = attic_project(camera=False).model_copy(update={"name": "Maison de Léa"})
    assert rename(manual, [], actor="pipeline:naming") is None
    typed = attic_project(camera=False).model_copy(update={"name": "IMG 2041", "name_auto": False})
    assert rename(typed, [], actor="pipeline:naming") is None, "tapé à la main : gardé"
    old = attic_project(camera=False).model_copy(
        update={"name": "WhatsApp Image 2026 08 25 at 13.36.24"}
    )
    after = rename(old, [], actor="pipeline:naming")
    assert after is not None and after.name.startswith("Grenier · ") and after.name_auto
    assert after.history[-1].actor == "pipeline:naming"
    assert after.history[-1].label == "Nommer le projet"
    # Déjà juste : rien à faire.
    assert rename(after, [], actor="pipeline:naming") is None


def test_autopilot_translates_the_home_instruction_and_names_the_project(
    tmp_path: Path, fake_render: list[UUID]
) -> None:
    project = attic_project(camera=False).model_copy(
        update={"name": "Projet · 6 oct.", "name_auto": True}
    )
    store = _Store(project)
    run = run_autopilot(
        Workspace(store.load, store.save),
        only_level(project).id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        home=HomeRequest("grenier chaleureux, bois et lin, deux lits"),
        taken_names=lambda: [],
    )
    level = only_level(store.project)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    assert room.intent is not None
    assert room.intent.instruction == "grenier chaleureux, bois et lin, deux lits"
    assert room.intent.mood == ["chaleureux"]
    assert room.intent.chosen_by_user("mood"), "la consigne compte comme un choix"
    step = next(s for s in run.steps if s.name == "intent")
    assert step.status == "done" and step.note.startswith("consigne traduite")
    assert store.project.name.startswith("Grenier · ")
    labels = [(h.actor, h.label) for h in store.project.history]
    assert (ap.ACTOR, "Nommer le projet") in labels
    assert any(actor == "user" for actor, _ in labels), "la consigne est un geste de l'utilisateur"


def test_autopilot_keeps_a_hand_given_name(tmp_path: Path, fake_render: list[UUID]) -> None:
    project = attic_project(camera=False).model_copy(update={"name": "Chez nous"})
    store = _Store(project)
    run_autopilot(
        Workspace(store.load, store.save),
        only_level(project).id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        taken_names=lambda: [],
    )
    assert store.project.name == "Chez nous"


def test_api_create_auto_name_preview_and_manual_rename(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name_auto": True}).json()
    assert created["name_auto"] is True and created["name"].startswith("Projet · ")
    preview = client.post(
        "/api/instruction/preview", json={"text": "grenier chaleureux, bois et lin, deux lits"}
    ).json()
    assert "humeur : chaleureux" in preview["translation"]["understood"]
    renamed = client.patch(f"/api/projects/{created['id']}", json={"name": "Mon grenier"}).json()
    assert renamed["name"] == "Mon grenier" and renamed["name_auto"] is False
    # Un nom donné à la main reste à l'ouverture.
    opened = client.post(f"/api/projects/{created['id']}/name/auto").json()
    assert opened["name"] == "Mon grenier"
    # Un nom tapé à la main reste, même s'il ressemble à un nom de fichier.
    typed = client.post(
        "/api/projects", json={"name": "WhatsApp Image 2026 08 25 at 13.36.24"}
    ).json()
    assert typed["name_auto"] is False
    opened = client.post(f"/api/projects/{typed['id']}/name/auto").json()
    assert opened["name"] == "WhatsApp Image 2026 08 25 at 13.36.24"
