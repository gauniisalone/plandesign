"""T2 : les cadres fixes du tableau. « Photos de la pièce » et « Inspirations » existent dès le
départ, même vides ; retirés à la main, ils ne reviennent pas ; une photo du projet entre
dans le cadre des photos ; une image jointe cochée Référence reste à sa place."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.board import FIXED_FRAMES
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import Project
from tests.factories import rect_room_project


def _client(tmp_path: Path, project: Project) -> TestClient:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


def test_fixed_frames_exist_from_the_start_and_stay_removed(tmp_path: Path) -> None:
    project = rect_room_project(4.0, 3.0)
    client = _client(tmp_path, project)
    pid = project.id
    board = client.get(f"/api/projects/{pid}/board").json()
    frames = {i["role"]: i for i in board["items"] if i["kind"] == "frame"}
    assert set(FIXED_FRAMES) <= set(frames)
    assert frames["photos"]["text"] == "Photos de la pièce"
    assert frames["inspirations"]["text"] == "Inspirations"
    # T4 : le cadre Rendus est là aussi, à droite du plan, à sa hauteur.
    plan = next(i for i in board["items"] if i["kind"] == "plan")
    assert frames["renders"]["text"] == "Rendus"
    assert frames["renders"]["x"] >= plan["x"] + plan["w"] and frames["renders"]["y"] == plan["y"]
    assert not any(i["frame"] == frames["inspirations"]["id"] for i in board["items"])
    # Ils ne se dédoublent pas d'une synchronisation à l'autre.
    again = client.get(f"/api/projects/{pid}/board").json()
    assert sum(1 for i in again["items"] if i["kind"] == "frame" and i["role"] == "photos") == 1
    # Retiré à la main : noté, pas remis.
    again["items"] = [i for i in again["items"] if i.get("role") != "inspirations"]
    out = client.put(f"/api/projects/{pid}/board", json=again).json()
    assert "frame:inspirations" in out["dismissed"]
    final = client.get(f"/api/projects/{pid}/board").json()
    assert not any(i.get("role") == "inspirations" for i in final["items"])
    assert any(i.get("role") == "photos" for i in final["items"])


def test_a_project_photo_enters_the_photos_frame_and_a_reference_image_keeps_its_place(
    tmp_path: Path,
) -> None:
    project = rect_room_project(4.0, 3.0)
    client = _client(tmp_path, project)
    pid = project.id
    board = client.get(f"/api/projects/{pid}/board").json()
    frames = {i["role"]: i for i in board["items"] if i["kind"] == "frame"}
    # Une image jointe (inspiration) posée dans le cadre Inspirations, cochée Référence.
    joined = client.post(
        f"/api/projects/{pid}/media", files={"file": ("chalet.png", _png(), "image/png")}
    ).json()
    assert joined["kind"] == "image"
    inspiration = {
        "id": "00000000-0000-4000-8000-00000000a001",
        "kind": "file",
        "x": frames["inspirations"]["x"] + 24,
        "y": frames["inspirations"]["y"] + 24,
        "w": 280,
        "h": 190,
        "z": 1,
        "locked": False,
        "frame": frames["inspirations"]["id"],
        "ref": joined["id"],
        "text": "chalet.png",
        "role": None,
        "url": None,
        "reference": True,
    }
    board["items"].append(inspiration)
    assert client.put(f"/api/projects/{pid}/board", json=board).status_code == 200
    # Une photo réelle importée ensuite entre dans le cadre des photos ; l'inspiration reste.
    r = client.post(f"/api/projects/{pid}/images", files={"file": ("p.png", _png(), "image/png")})
    assert r.status_code == 201, r.text
    after = client.get(f"/api/projects/{pid}/board").json()
    photo = next(i for i in after["items"] if i["kind"] == "photo")
    assert photo["frame"] == frames["photos"]["id"]
    kept = next(i for i in after["items"] if i["ref"] == joined["id"])
    assert kept["frame"] == frames["inspirations"]["id"] and kept["reference"] is True
    assert sum(1 for i in after["items"] if i["ref"] == joined["id"]) == 1, (
        "pas de doublon Fichiers"
    )
