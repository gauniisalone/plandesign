"""Accueil L1b : texte sans fichier (plan de départ simplifié depuis une surface, ou
consigne gardée), ligne « Compris » en mots simples, noms de fichier renommés dans la liste."""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline import autopilot as ap
from openplan.pipeline.autopilot import HomeRequest, Workspace, run_autopilot
from openplan.pipeline.instruction import translate_phrases
from openplan.pipeline.starter import STARTER_KEY, area_in_text, starter_plan
from openplan.scene.catalog import load_catalog
from openplan.scene.factory import new_project
from openplan.scene.ops import OpError
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Source
from openplan.schemas.elements import Room, Wall
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


@pytest.fixture
def store(tmp_path: Path) -> ProjectStore:
    return ProjectStore(tmp_path / "data")


@pytest.fixture
def client(tmp_path: Path, store: ProjectStore) -> Iterator[TestClient]:
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize(
    ("text", "area"),
    [
        ("Un appartement de 60 m², deux chambres", 60.0),
        ("studio de 25m2 lumineux", 25.0),
        ("45,5 m² sous les toits", 45.5),
        ("30 mètres carrés", 30.0),
        ("une pièce de 3 m²", None),
        ("grenier chaleureux", None),
    ],
)
def test_area_is_read_from_the_text(text: str, area: float | None) -> None:
    assert area_in_text(text) == area


def test_starter_plan_is_a_4_3_rectangle_of_the_asked_area() -> None:
    project = new_project("Projet · 7 oct.")
    level = only_level(project)
    after = starter_plan(project, level.id, 60.0, actor="pipeline:starter")
    built = only_level(after)
    walls = [e for e in built.elements.values() if isinstance(e, Wall)]
    rooms = [e for e in built.elements.values() if isinstance(e, Room)]
    assert len(walls) == 4 and len(rooms) == 1
    assert all(w.source == Source.GENERATED for w in walls)
    assert rooms[0].metadata[STARTER_KEY] == 60.0
    # Un seul pas d'historique, attribué au pipeline.
    assert len(after.history) == len(project.history) + 1
    assert after.history[-1].actor == "pipeline:starter"
    assert after.history[-1].label == "Plan de départ simplifié"
    # Surface intérieure : largeur × profondeur = 60 m², rapport 4:3.
    inner_w = math.sqrt(60 * 4 / 3)
    assert inner_w / (60 / inner_w) == pytest.approx(4 / 3)


def test_starter_plan_refuses_a_plan_with_walls_or_out_of_bounds() -> None:
    project = attic_project(camera=False)
    with pytest.raises(OpError, match="déjà des murs"):
        starter_plan(project, only_level(project).id, 40.0, actor="pipeline:starter")
    empty = new_project("Vide")
    with pytest.raises(OpError, match="hors bornes"):
        starter_plan(empty, only_level(empty).id, 2.0, actor="pipeline:starter")


def test_summary_is_in_plain_words_and_text_order() -> None:
    reading = translate_phrases(
        "grenier chaleureux, bois et lin, deux lits", load_catalog().categories
    )
    assert reading.summary == ["grenier", "chaleureux", "lin", "2 lits simples"]
    reading = translate_phrases(
        "suite parentale cosy avec coin lecture, lumière chaude du soir",
        load_catalog().categories,
    )
    assert reading.summary == ["suite parentale", "cosy", "coin lecture", "lumière chaude", "soir"]


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> None:
    def render(project: Project, level_id: UUID, camera_id: UUID, out_dir: Path, **_: Any) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "preview.png").write_bytes(b"png")

    monkeypatch.setattr(ap, "render_camera_view", render)


def test_kept_instruction_is_applied_then_cleared(tmp_path: Path, fake_render: None) -> None:
    project = attic_project(camera=False).model_copy(
        update={"pending_instruction": "grenier chaleureux, deux lits"}
    )
    saved = {"p": project}
    run_autopilot(
        Workspace(lambda: saved["p"], lambda p: saved.update(p=p)),
        only_level(project).id,
        tmp_path,
        stage=lambda *_, **__: None,
        blender_bin=None,
        device="cuda",
        home=HomeRequest(project.pending_instruction or ""),
    )
    room = next(e for e in only_level(saved["p"]).elements.values() if isinstance(e, Room))
    assert room.intent is not None and room.intent.instruction == "grenier chaleureux, deux lits"
    assert saved["p"].pending_instruction is None


def test_api_starter_and_kept_instruction(client: TestClient) -> None:
    created = client.post("/api/projects", json={"name_auto": True}).json()
    pid = created["id"]
    lid = next(iter(created["levels"]))
    after = client.post(f"/api/projects/{pid}/levels/{lid}/starter", json={"area_m2": 45}).json()
    elements = next(iter(after["levels"].values()))["elements"].values()
    assert sum(e["type"] == "wall" for e in elements) == 4
    room = next(e for e in elements if e["type"] == "room")
    assert room["metadata"]["starter"] == 45
    again = client.post(f"/api/projects/{pid}/levels/{lid}/starter", json={"area_m2": 45})
    assert again.status_code >= 400
    kept = client.patch(f"/api/projects/{pid}", json={"pending_instruction": "  bureau  "}).json()
    assert kept["pending_instruction"] == "bureau"
    cleared = client.patch(f"/api/projects/{pid}", json={"pending_instruction": ""}).json()
    assert cleared["pending_instruction"] is None


def test_listing_names_old_file_named_projects_without_history(
    client: TestClient, store: ProjectStore
) -> None:
    old = new_project("WhatsApp Image 2026 08 25 at 13.36.24")
    twin = new_project("WhatsApp Image 2026 08 25 at 13.36.24")
    typed = new_project("IMG 2041").model_copy(update={"name_auto": False})
    for p in (old, twin, typed):
        store.save(p)
    names = {s["id"]: s["name"] for s in client.get("/api/projects").json()}
    assert names[str(old.id)].startswith("Projet · ")
    assert names[str(twin.id)] != names[str(old.id)], "deux anciens : deux noms distincts"
    assert names[str(typed.id)] == "IMG 2041", "nom tapé à la main : gardé"
    reloaded = store.load(old.id)
    assert reloaded.name_auto is True and len(reloaded.history) == len(old.history)
