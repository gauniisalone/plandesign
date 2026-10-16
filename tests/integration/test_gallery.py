"""US2 : galerie des rendus. Titre et favoris dans `gallery.json` à côté des images ; vue et
ambiance notées ; corbeille douce (annuler, puis effacement réel et journal V2) ; favoris en
zip ; rien dans le format de scène."""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.routes.gallery import purge_project
from openplan.api.settings import Settings
from openplan.pipeline import gallery as g
from openplan.pipeline import journal
from openplan.pipeline.journal import JournalEvent
from openplan.scene.store import ProjectStore
from openplan.schemas.common import Point3, Source
from openplan.schemas.elements import Camera, Room
from openplan.schemas.intent import DesignIntent
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _png(path: Path, color: tuple[int, int, int]) -> None:
    Image.new("RGB", (8, 6), color).save(path)


def _project() -> tuple[Project, dict[str, Camera]]:
    project = rect_room_project()
    level = only_level(project)
    elements = dict(level.elements)
    room = next(e for e in elements.values() if isinstance(e, Room))
    elements[room.id] = room.model_copy(
        update={"intent": DesignIntent(style="scandinave", mood=["chaleureux"])}
    )
    cams: dict[str, Camera] = {}
    for key, role in (("overall", "overall"), ("detail", "detail"), ("hand", None)):
        cam = Camera(
            name={"overall": "Vue d'ensemble", "detail": "Détail", "hand": "Point de vue"}[key],
            position=Point3(x=1.0, y=1.0, z=1.5),
            yaw=0.0,
            pitch=0.0,
            source=Source.GENERATED,
            confidence=0.8,
            metadata={"auto_view": role} if role else {},
        )
        elements[cam.id] = cam
        cams[key] = cam
    level = level.model_copy(update={"elements": elements})
    return project.model_copy(update={"levels": {level.id: level}}), cams


def _render(
    store: ProjectStore,
    project: Project,
    camera: Camera,
    *,
    minutes_ago: int = 0,
    finished: bool = False,
    inspiration: bool = False,
) -> UUID:
    run_id = uuid4()
    out = store.runs_dir(project.id) / str(run_id) / "blender"
    out.mkdir(parents=True)
    created = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    _png(out / "preview.png", (200, 180, 150))
    (out / "render.json").write_text(
        json.dumps(
            {
                "run_id": str(run_id),
                "camera_id": str(camera.id),
                "camera_name": camera.name,
                "samples": 16,
                "device": "cpu",
                "render_s": 3.0,
                "width_px": 8,
                "height_px": 6,
                "created_at": created,
            }
        ),
        encoding="utf-8",
    )
    common = {"run_id": str(run_id), "prompt": "essai", "seed": 0, "model_ids": []}
    if finished:
        _png(out / "finished.png", (190, 170, 140))
        (out / "finish.json").write_text(
            json.dumps(
                {
                    **common,
                    "strength": 0.35,
                    "style": "naturel",
                    "duration_s": 1.0,
                    "guided_by": [],
                    "created_at": created,
                }
            ),
            encoding="utf-8",
        )
    if inspiration:
        _png(out / "inspiration.png", (180, 160, 130))
        (out / "inspiration.json").write_text(
            json.dumps(
                {
                    **common,
                    "mode": "img2img",
                    "strength": 0.8,
                    "control_scale": 1.2,
                    "duration_s": 1.0,
                    "created_at": created,
                }
            ),
            encoding="utf-8",
        )
    return run_id


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[TestClient, ProjectStore, Project, dict[str, Camera]]]:
    store = ProjectStore(tmp_path / "data")
    project, cams = _project()
    store.save(project)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        yield client, store, project, cams


def test_list_groups_by_view_with_ambiance_newest_first(setup: Any) -> None:
    client, store, project, cams = setup
    old = _render(store, project, cams["overall"], minutes_ago=10, finished=True)
    new = _render(store, project, cams["detail"], inspiration=True)
    hand = _render(store, project, cams["hand"], minutes_ago=5)
    r = client.get(f"/api/projects/{project.id}/renders")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [e["run_id"] for e in body] == [str(new), str(hand), str(old)]
    groups = {e["run_id"]: e["view_group"] for e in body}
    assert groups == {str(old): "overall", str(new): "detail", str(hand): "hand"}
    assert {e["ambiance"] for e in body} == {"Scandinave · chaleureux"}
    assert all(e["title"] is None and e["favorites"] == [] for e in body)


def test_rename_and_favorites_are_kept_next_to_the_images(setup: Any) -> None:
    client, store, project, cams = setup
    run = _render(store, project, cams["overall"], finished=True, inspiration=True)
    url = f"/api/projects/{project.id}/renders/{run}"
    r = client.patch(url, json={"title": "  Séjour   du matin  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Séjour du matin"
    r = client.patch(url, json={"favorites": ["inspiration", "blender"]})
    assert r.json()["favorites"] == ["blender", "inspiration"]  # ordre stable
    assert r.json()["title"] == "Séjour du matin"  # un champ absent ne change pas
    meta = json.loads(
        (store.runs_dir(project.id) / str(run) / "blender" / "gallery.json").read_text()
    )
    assert meta["title"] == "Séjour du matin" and meta["favorites"] == ["blender", "inspiration"]
    # Pas de version catalogue pour ce rendu : on ne peut pas la mettre en favori.
    r = client.patch(url, json={"favorites": ["catalogue"]})
    assert r.status_code == 409
    assert "catalogue" in r.json()["detail"]
    # Titre vidé : le nom du point de vue revient.
    r = client.patch(url, json={"title": ""})
    assert r.json()["title"] is None
    # Le format de scène n'a pas bougé.
    assert store.load(project.id).model_dump() == project.model_dump()
    r = client.patch(f"/api/projects/{project.id}/renders/{uuid4()}", json={"title": "x"})
    assert r.status_code == 409


def test_delete_then_undo_puts_the_render_back(setup: Any) -> None:
    client, store, project, cams = setup
    run = _render(store, project, cams["overall"], finished=True)
    client.patch(f"/api/projects/{project.id}/renders/{run}", json={"favorites": ["finished"]})
    r = client.delete(f"/api/projects/{project.id}/renders/{run}")
    assert r.status_code == 200, r.text
    assert r.json()["purge_after_s"] == g.TRASH_TTL_S
    assert client.get(f"/api/projects/{project.id}/renders").json() == []
    r = client.get(f"/api/projects/{project.id}/runs/{run}/blender/preview.png")
    assert r.status_code != 200
    r = client.post(f"/api/projects/{project.id}/renders/{run}/restore")
    assert r.status_code == 200, r.text
    assert r.json()["favorites"] == ["finished"]  # rien de perdu
    listed = client.get(f"/api/projects/{project.id}/renders").json()
    assert [e["run_id"] for e in listed] == [str(run)]
    # Pas de ligne « rendu supprimé » : la suppression a été annulée.
    assert not any(e.note == "rendu supprimé" for e in journal.read(store.project_dir(project.id)))
    assert client.delete(f"/api/projects/{project.id}/renders/{uuid4()}").status_code == 409


def test_purge_after_delay_deletes_files_and_journals_linked_to_origin(setup: Any) -> None:
    client, store, project, cams = setup
    run = _render(store, project, cams["overall"])
    project_dir = store.project_dir(project.id)
    origin = JournalEvent(project_id=project.id, kind="autopilot", text="grenier", run_ids=[run])
    journal.record(project_dir, project, origin)
    assert client.delete(f"/api/projects/{project.id}/renders/{run}").status_code == 200
    runs_dir = store.runs_dir(project.id)
    trashed = runs_dir / g.TRASH_DIR / str(run)
    assert trashed.is_dir()
    # Avant le délai, rien n'est effacé ; après, le dossier part pour de bon.
    assert g.purge(runs_dir) == []
    assert purge_project(store, project.id, older_than_s=0.0) == [run]
    assert not trashed.exists()
    r = client.post(f"/api/projects/{project.id}/renders/{run}/restore")
    assert r.status_code == 409
    events = journal.read(project_dir)
    deleted = [e for e in events if e.note == "rendu supprimé"]
    assert len(deleted) == 1
    assert deleted[0].kind == "undo"
    assert deleted[0].run_ids == [run]
    assert deleted[0].request_id == origin.id
    line = journal.journal_path(project_dir).read_text(encoding="utf-8").splitlines()[-1]
    assert ".png" not in line and "runs" not in line and str(project_dir) not in line


def test_purge_without_origin_and_with_journal_off(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project, cams = _project()
    project = project.model_copy(
        update={"privacy": project.privacy.model_copy(update={"design_journal": False})}
    )
    store.save(project)
    run = _render(store, project, cams["hand"])
    runs_dir = store.runs_dir(project.id)
    g.trash(runs_dir, run)
    assert purge_project(store, project.id, older_than_s=0.0) == [run]
    assert journal.read(store.project_dir(project.id)) == []


def test_trash_is_emptied_when_the_server_starts(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project, cams = _project()
    store.save(project)
    run = _render(store, project, cams["overall"])
    g.trash(store.runs_dir(project.id), run)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app):
        assert not (store.runs_dir(project.id) / g.TRASH_DIR / str(run)).exists()


def test_favorites_zip(setup: Any) -> None:
    client, store, project, cams = setup
    url = f"/api/projects/{project.id}/renders/favorites.zip"
    assert client.get(url).status_code == 409  # aucun favori
    a = _render(store, project, cams["overall"], finished=True, inspiration=True)
    b = _render(store, project, cams["detail"], minutes_ago=3)
    client.patch(
        f"/api/projects/{project.id}/renders/{a}",
        json={"title": "Séjour", "favorites": ["finished", "inspiration"]},
    )
    client.patch(f"/api/projects/{project.id}/renders/{b}", json={"favorites": ["blender"]})
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "rendus-favoris-" in r.headers["content-disposition"]
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == ["detail-blender.png", "sejour-inspiration.png", "sejour-reference.png"]


def test_note_render_records_view_and_ambiance(tmp_path: Path) -> None:
    project, cams = _project()
    level_id = next(iter(project.levels))
    out = tmp_path / "blender"
    out.mkdir()
    g.note_render(out, project, level_id, cams["detail"], None)
    meta = g.read_meta(out)
    assert meta.view_group == "detail"
    assert meta.ambiance == "Scandinave · chaleureux"
    g.note_render(out, project, level_id, None, 2)
    assert g.read_meta(out).view_group == "variant"
    assert g.read_meta(out).ambiance is None
    photo = cams["hand"].model_copy(update={"image_id": uuid4()})
    assert g.view_group_of(photo) == "photo"
    assert g.view_group_of(cams["hand"]) == "hand"


def test_broken_meta_is_ignored(tmp_path: Path) -> None:
    (tmp_path / g.GALLERY_SIDECAR).write_text("{pas du json", encoding="utf-8")
    assert g.read_meta(tmp_path) == g.GalleryMeta()
