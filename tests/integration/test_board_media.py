"""C1 : vidéo, PDF et liens sur le tableau, sans aucune requête vers l'extérieur. Un PDF reçoit
la vignette de sa première page (rendue sur la machine) ; une vidéo est lue telle quelle ; un
lien n'est qu'une adresse http(s), jamais récupérée ; un fichier joint par l'API entre dans le
cadre Fichiers ; la vignette du plan existe en thème sombre."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64


def _pdf() -> bytes:
    buf = io.BytesIO()
    doc = canvas.Canvas(buf)
    doc.drawString(72, 720, "Devis du menuisier")
    doc.rect(72, 400, 300, 200)
    doc.save()
    return buf.getvalue()


def _client(tmp_path: Path, project: Project) -> TestClient:
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    return TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))


def _upload(client: TestClient, project: Project, name: str, data: bytes) -> dict:  # type: ignore[type-arg]
    r = client.post(f"/api/projects/{project.id}/media", files={"file": (name, data, "x/y")})
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_pdf_gets_a_first_page_thumbnail_and_opens_inline(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    ref = _upload(client, project, "devis.pdf", _pdf())
    assert ref["kind"] == "pdf" and ref["media_type"] == "application/pdf"
    assert ref["thumb"]
    thumb = client.get(f"/api/projects/{project.id}/media/{ref['id']}/thumb")
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
    shown = client.get(f"/api/projects/{project.id}/media/{ref['id']}/file")
    assert shown.headers["content-type"] == "application/pdf"
    assert shown.headers["content-disposition"].startswith("inline")
    # Un « PDF » illisible reste une carte fichier, sans vignette ni erreur.
    broken = _upload(client, project, "abime.pdf", b"%PDF-1.4 rien")
    assert broken["kind"] == "pdf" and broken["thumb"] is None
    assert client.get(f"/api/projects/{project.id}/media/{broken['id']}/thumb").status_code == 404


def test_videos_are_recognised_and_played_inline(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    mp4 = _upload(client, project, "visite.mp4", MP4)
    webm = _upload(client, project, "visite.webm", WEBM)
    assert (mp4["kind"], mp4["media_type"]) == ("video", "video/mp4")
    assert (webm["kind"], webm["media_type"]) == ("video", "video/webm")
    got = client.get(f"/api/projects/{project.id}/media/{mp4['id']}/file")
    assert got.headers["content-type"] == "video/mp4"
    assert got.headers["content-disposition"].startswith("inline")
    # Un fichier appelé « .mp4 » qui n'en est pas un reste une pièce jointe.
    fake = _upload(client, project, "faux.mp4", b"<html>")
    assert fake["kind"] == "file" and fake["media_type"] == "application/octet-stream"


def test_files_joined_by_the_api_enter_the_files_frame(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    client.get(f"/api/projects/{project.id}/board")
    pdf = _upload(client, project, "devis.pdf", _pdf())
    video = _upload(client, project, "visite.mp4", MP4)
    note = _upload(client, project, "notes.txt", b"mesures")
    board = client.get(f"/api/projects/{project.id}/board").json()
    frame = next(i for i in board["items"] if i["role"] == "files")
    assert frame["kind"] == "frame" and frame["text"] == "Fichiers"
    placed = {(i["kind"], i["ref"]) for i in board["items"] if i["frame"] == frame["id"]}
    assert placed == {("pdf", pdf["id"]), ("video", video["id"]), ("file", note["id"])}
    # Retiré du projet, il quitte le tableau ; retiré du tableau, il n'y revient pas.
    client.delete(f"/api/projects/{project.id}/media/{video['id']}")
    board = client.get(f"/api/projects/{project.id}/board").json()
    assert not any(i["ref"] == video["id"] for i in board["items"])
    board["items"] = [i for i in board["items"] if i["ref"] != note["id"]]
    client.put(f"/api/projects/{project.id}/board", json=board)
    again = client.get(f"/api/projects/{project.id}/board").json()
    assert not any(i["ref"] == note["id"] for i in again["items"])


def test_links_are_web_addresses_only_and_never_fetched(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    board = client.get(f"/api/projects/{project.id}/board").json()
    link = {
        "kind": "link",
        "x": 0,
        "y": 900,
        "w": 260,
        "h": 90,
        "text": "Canapé lin",
        "url": "https://www.exemple.fr/canape?ref=12",
    }
    ok = client.put(
        f"/api/projects/{project.id}/board", json={**board, "items": [*board["items"], link]}
    )
    assert ok.status_code == 200, ok.text
    saved = ok.json()
    for bad in ("javascript:alert(1)", "file:///etc/passwd", "https://a b.fr", "data:text/html,x"):
        r = client.put(
            f"/api/projects/{project.id}/board",
            json={**saved, "items": [*saved["items"], {**link, "url": bad}]},
        )
        assert r.status_code == 422, bad


def test_plan_thumbnail_has_a_dark_theme(tmp_path: Path) -> None:
    project = rect_room_project(5.0, 4.0)
    client = _client(tmp_path, project)
    lid = only_level(project).id
    light = client.get(f"/api/projects/{project.id}/exports/svg?level_id={lid}").text
    dark = client.get(f"/api/projects/{project.id}/exports/svg?level_id={lid}&theme=dark").text
    assert "#262833" not in light and "#262833" in dark
    assert light.count("<polygon") == dark.count("<polygon"), "même plan, autres couleurs"
