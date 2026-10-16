"""B1 : le tableau du projet. Mise en page de départ (photos, demande, rendus, plan), gardée à
part de la géométrie ; redimensionner le cadre du plan ne change pas ses cotes ; un rendu
refait remplace l'ancien à sa place ; ce qui est retiré n'est pas remis ; fichiers joints
servis sans être interprétés."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.render import RENDER_SIDECAR, RenderEntry
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (180, 160, 140)).save(buf, format="PNG")
    return buf.getvalue()


def _client(tmp_path: Path, project: Project) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    return TestClient(app), store


def _render(store: ProjectStore, project: Project, camera: UUID, minutes: int) -> str:
    run = uuid4()
    out = store.runs_dir(project.id) / str(run) / "blender"
    out.mkdir(parents=True)
    (out / "preview.png").write_bytes(_png())
    entry = RenderEntry(
        run_id=run,
        camera_id=camera,
        camera_name="Ensemble",
        samples=8,
        device="cpu",
        render_s=1.0,
        width_px=64,
        height_px=48,
        created_at=datetime(2026, 10, 13, tzinfo=UTC) + timedelta(minutes=minutes),
    )
    (out / RENDER_SIDECAR).write_text(entry.model_dump_json(), encoding="utf-8")
    return str(run)


def test_first_layout_shows_photos_renders_and_plan(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0).model_copy(
        update={"pending_instruction": "grenier chaleureux, bois et lin"}
    )
    client, store = _client(tmp_path, project)
    r = client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    )
    assert r.status_code == 201, r.text
    image_id = r.json()["image"]["id"]
    run = _render(store, project, uuid4(), 0)
    board = client.get(f"/api/projects/{project.id}/board").json()
    kinds = {(i["kind"], i["ref"]) for i in board["items"]}
    assert ("photo", image_id) in kinds and ("render", run) in kinds
    assert ("plan", str(only_level(project).id)) in kinds
    # T7b : la consigne vit dans le projet (compositeur flottant), plus aucune note sur le
    # tableau.
    assert not any(i["role"] == "prompt" or i["kind"] == "requirements" for i in board["items"])
    assert client.get(f"/api/projects/{project.id}").json()["pending_instruction"] == (
        "grenier chaleureux, bois et lin"
    )
    frames = {i["role"]: i for i in board["items"] if i["kind"] == "frame"}
    photo = next(i for i in board["items"] if i["kind"] == "photo")
    assert photo["frame"] == frames["photos"]["id"]
    # Maquette d'augus : Photos puis Inspirations à gauche, le plan au centre, Rendus à droite.
    plan = next(i for i in board["items"] if i["kind"] == "plan")
    assert frames["photos"]["x"] == frames["inspirations"]["x"] == 0.0
    assert frames["photos"]["y"] == 0.0
    assert frames["inspirations"]["y"] > frames["photos"]["y"]
    assert frames["photos"]["x"] + frames["photos"]["w"] < plan["x"]
    assert frames["renders"]["x"] > plan["x"] + plan["w"]
    assert (store.project_dir(project.id) / "board.json").is_file()


def test_an_old_requirements_note_leaves_and_the_left_column_moves_up(tmp_path: Path) -> None:
    """Tableau d'avant T7b : la note Exigences en haut à gauche, les cadres sous elle. Elle
    quitte le tableau et les cadres remontent à sa place, leurs photos avec eux."""
    from openplan.pipeline.board import save_board
    from openplan.schemas.board import Board, BoardItem

    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    )
    board = Board.model_validate(client.get(f"/api/projects/{project.id}/board").json())
    shift = 640.0 + 48.0
    note = BoardItem(
        kind="requirements", x=0.0, y=0.0, w=360.0, h=640.0, text="Exigences", role="prompt"
    )
    moved = [i.model_copy(update={"y": i.y + shift}) if i.x < 600 else i for i in board.items]
    save_board(store.project_dir(project.id), board.model_copy(update={"items": [note, *moved]}))
    after = client.get(f"/api/projects/{project.id}/board").json()
    assert not any(i["kind"] == "requirements" for i in after["items"])
    frames = {i["role"]: i for i in after["items"] if i["kind"] == "frame"}
    assert frames["photos"]["y"] == 0.0
    photo = next(i for i in after["items"] if i["kind"] == "photo")
    assert frames["photos"]["y"] <= photo["y"] < frames["photos"]["y"] + frames["photos"]["h"]


def test_old_frame_content_moves_under_the_header(tmp_path: Path) -> None:
    """Tableau d'avant T7b : une photo à 24 px du haut de son cadre descend sous l'en-tête ;
    le cadre grandit d'autant. Un élément déplacé à la main ailleurs ne bouge pas."""
    from openplan.pipeline.board import PAD, PAD_TOP, save_board
    from openplan.schemas.board import Board

    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    )
    board = Board.model_validate(client.get(f"/api/projects/{project.id}/board").json())
    frame = next(i for i in board.items if i.kind == "frame" and i.role == "photos")
    old = [
        i.model_copy(update={"y": frame.y + PAD}) if i.kind == "photo" else i for i in board.items
    ]
    save_board(store.project_dir(project.id), board.model_copy(update={"items": old}))
    after = client.get(f"/api/projects/{project.id}/board").json()
    photo = next(i for i in after["items"] if i["kind"] == "photo")
    moved = next(i for i in after["items"] if i["id"] == str(frame.id))
    assert photo["y"] == frame.y + PAD_TOP
    assert moved["h"] == frame.h + PAD_TOP - PAD
    again = client.get(f"/api/projects/{project.id}/board").json()
    assert next(i for i in again["items"] if i["kind"] == "photo")["y"] == photo["y"]


def test_resizing_the_plan_frame_never_changes_the_plan(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    board = client.get(f"/api/projects/{project.id}/board").json()
    before = store.load(project.id).levels
    for item in board["items"]:
        if item["kind"] == "plan":
            item["w"], item["h"], item["x"] = item["w"] * 2, item["h"] * 0.5, 4000.0
    saved = client.put(f"/api/projects/{project.id}/board", json=board)
    assert saved.status_code == 200, saved.text
    assert store.load(project.id).levels == before, "la géométrie n'a pas bougé"
    again = client.get(f"/api/projects/{project.id}/board").json()
    plan = next(i for i in again["items"] if i["kind"] == "plan")
    assert plan["x"] == 4000.0 and plan["w"] == 1800.0


def test_a_new_render_of_a_view_takes_the_old_ones_place(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    camera = uuid4()
    old = _render(store, project, camera, 0)
    board = client.get(f"/api/projects/{project.id}/board").json()
    item = next(i for i in board["items"] if i["ref"] == old)
    item["x"], item["y"] = 1234.0, 567.0
    client.put(f"/api/projects/{project.id}/board", json=board)
    new = _render(store, project, camera, 5)
    after = client.get(f"/api/projects/{project.id}/board").json()
    moved = next(i for i in after["items"] if i["id"] == item["id"])
    assert moved["ref"] == new and (moved["x"], moved["y"]) == (1234.0, 567.0)
    assert not any(i["ref"] == old for i in after["items"])


def test_what_is_removed_by_hand_stays_removed(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client, _ = _client(tmp_path, project)
    client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    )
    board = client.get(f"/api/projects/{project.id}/board").json()
    photo = next(i for i in board["items"] if i["kind"] == "photo")
    board["items"] = [i for i in board["items"] if i["id"] != photo["id"]]
    out = client.put(f"/api/projects/{project.id}/board", json=board).json()
    assert f"photo:{photo['ref']}" in out["dismissed"]
    again = client.get(f"/api/projects/{project.id}/board").json()
    assert not any(i["kind"] == "photo" for i in again["items"])


def test_a_save_from_a_stale_revision_is_refused(tmp_path: Path) -> None:
    """Deux onglets : le second envoi, parti de la même révision, est refusé (409) au lieu
    d'écraser le premier en silence."""
    project = rect_room_project(5.0, 4.0)
    client, _ = _client(tmp_path, project)
    tab_a = client.get(f"/api/projects/{project.id}/board").json()
    tab_b = dict(tab_a)
    tab_a["view"] = {"x": 10.0, "y": 0.0, "zoom": 1.0}
    first = client.put(f"/api/projects/{project.id}/board", json=tab_a)
    assert first.status_code == 200 and first.json()["revision"] == tab_a["revision"] + 1
    tab_b["view"] = {"x": 99.0, "y": 0.0, "zoom": 2.0}
    second = client.put(f"/api/projects/{project.id}/board", json=tab_b)
    assert second.status_code == 409 and "rechargez" in second.json()["detail"]
    kept = client.get(f"/api/projects/{project.id}/board").json()
    assert kept["view"]["x"] == 10.0


def test_the_archive_holds_project_board_photos_and_files(tmp_path: Path) -> None:
    import zipfile

    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    image = client.post(
        f"/api/projects/{project.id}/images", files={"file": ("p.png", _png(), "image/png")}
    ).json()["image"]
    media = client.post(
        f"/api/projects/{project.id}/media",
        files={"file": ("devis.txt", b"42 euros", "text/plain")},
    ).json()
    client.get(f"/api/projects/{project.id}/board")
    _render(store, project, uuid4(), 0)
    r = client.get(f"/api/projects/{project.id}/exports/archive")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"project.json", "board.json", image["path"], media["path"]} <= names
    assert any(n.endswith("/blender/preview.png") for n in names)
    # Supprimer le projet emporte aussi le tableau et les fichiers joints.
    assert client.delete(f"/api/projects/{project.id}").status_code in (200, 204)
    assert not store.project_dir(project.id).exists()


def test_photo_inspiration_without_plan_is_on_the_board(tmp_path: Path) -> None:
    """A2 : le rendu de secours (image d'inspiration sans plan) entre dans le cadre Rendus."""
    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    run = uuid4()
    out = store.runs_dir(project.id) / str(run) / "inspiration"
    out.mkdir(parents=True)
    (out / "inspiration.png").write_bytes(_png())
    board = client.get(f"/api/projects/{project.id}/board").json()
    item = next(i for i in board["items"] if i["ref"] == str(run))
    assert item["kind"] == "render" and item["role"] == "inspiration"
    assert item["text"] == "inspiration, sans plan"


def test_attached_files_are_kept_and_never_interpreted(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client, store = _client(tmp_path, project)
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    r = client.post(
        f"/api/projects/{project.id}/media",
        files={"file": ("../../etc/devis.svg", svg, "image/svg+xml")},
    )
    assert r.status_code == 201, r.text
    ref = r.json()
    assert ref["name"] == "devis.svg" and ref["media_type"] == "application/octet-stream"
    got = client.get(f"/api/projects/{project.id}/media/{ref['id']}/file")
    assert got.content == svg
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment")
    assert got.headers["x-content-type-options"] == "nosniff"
    png = client.post(
        f"/api/projects/{project.id}/media", files={"file": ("vue.png", _png(), "image/png")}
    ).json()
    shown = client.get(f"/api/projects/{project.id}/media/{png['id']}/file")
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["content-disposition"].startswith("inline")
    assert (store.project_dir(project.id) / ref["path"]).is_file()
    assert client.delete(f"/api/projects/{project.id}/media/{ref['id']}").status_code == 204
    assert client.get(f"/api/projects/{project.id}/media/{ref['id']}/file").status_code == 404
    empty = client.post(
        f"/api/projects/{project.id}/media", files={"file": ("vide.txt", b"", "text/plain")}
    )
    assert empty.status_code == 400
