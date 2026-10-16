"""T5 : la vignette de la galerie, par ordre de préférence : rendu retenu ou plus récent (WebP
480 px en cache), sinon plan en SVG, sinon première photo, sinon carte Exigences."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.render import RENDER_SIDECAR, RenderEntry
from openplan.scene.factory import new_project
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import Project
from tests.factories import rect_room_project


def _client(tmp_path: Path, project: Project) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))), store


def _render(store: ProjectStore, project: Project, *, favorite: bool = False) -> str:
    run = uuid4()
    d = store.runs_dir(project.id) / str(run) / "blender"
    d.mkdir(parents=True)
    Image.new("RGB", (1280, 960), (200, 150, 90)).save(d / "preview.png")
    entry = RenderEntry(
        run_id=run,
        camera_id=uuid4(),
        camera_name="Vue",
        samples=16,
        device="cpu",
        render_s=1.0,
        width_px=1280,
        height_px=960,
        created_at=datetime.now(UTC),
    )
    (d / RENDER_SIDECAR).write_text(entry.model_dump_json(), encoding="utf-8")
    if favorite:
        (d / "gallery.json").write_text(json.dumps({"version": 1, "favorites": ["blender"]}))
    return str(run)


def test_cover_prefers_a_render_then_the_plan_then_the_note(tmp_path: Path) -> None:
    # Projet vide : la carte Exigences (SVG), jamais un 404.
    empty = new_project("Vide").model_copy(update={"pending_instruction": "grenier chaleureux"})
    client, store = _client(tmp_path, empty)
    r = client.get(f"/api/projects/{empty.id}/cover")
    assert r.status_code == 200 and r.headers["x-cover-kind"] == "requirements"
    assert (
        r.headers["content-type"].startswith("image/svg+xml") and b"grenier chaleureux" in r.content
    )

    # Un plan : le SVG du plan.
    plan = rect_room_project(4.0, 3.0)
    store.save(plan)
    r = client.get(f"/api/projects/{plan.id}/cover?theme=dark")
    assert r.status_code == 200 and r.headers["x-cover-kind"] == "plan"

    # Un rendu : la réduction WebP de 480 px, mise en cache.
    _render(store, plan)
    r = client.get(f"/api/projects/{plan.id}/cover")
    assert r.status_code == 200 and r.headers["x-cover-kind"] == "render"
    assert r.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(r.content)) as img:
        assert img.width == 480 and img.height == 360
    cached = list((store.project_dir(plan.id) / "cover").glob("*.webp"))
    assert len(cached) == 1
    # Un rendu favori l'emporte sur un plus récent.
    favorite = _render(store, plan, favorite=True)
    _render(store, plan)
    client.get(f"/api/projects/{plan.id}/cover")
    cached = list((store.project_dir(plan.id) / "cover").glob("*.webp"))
    assert [p.name for p in cached] == [f"{favorite}-blender.webp"]
