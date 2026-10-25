"""Carte « Fichiers » vide (signalée par Features, 25 oct.) : une image déposée dans
Inspirations est d'abord envoyée (POST /media), puis sa carte est écrite (PUT /board). Une
lecture du tableau entre les deux (un autre onglet, la synchronisation en direct) rangeait le
fichier, que rien ne pointait encore, dans un cadre « Fichiers » créé pour l'occasion. Un envoi
de l'espace de travail est maintenant marqué `placed` : la synchronisation n'y touche plus ; et
les copies et cadres « Fichiers » vides déjà créés sont nettoyés. Un fichier joint par l'API
(C1) entre toujours dans « Fichiers »."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore
from tests.factories import rect_room_project


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    project = rect_room_project(4.0, 3.0)
    store = ProjectStore(tmp_path / "data")
    store.save(project)
    client = TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data")))
    return client, str(project.id)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _card(frame: dict[str, Any], ref: str) -> dict[str, Any]:
    """La carte que l'espace de travail écrit après l'envoi (image dans Inspirations)."""
    return {
        "id": "00000000-0000-4000-8000-00000000b001",
        "kind": "file",
        "x": frame["x"] + 24,
        "y": frame["y"] + 24,
        "w": 280,
        "h": 190,
        "z": 1,
        "locked": False,
        "frame": frame["id"],
        "ref": ref,
        "text": "ambiance.png",
        "role": None,
        "url": None,
        "reference": True,
    }


def _files(board: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in board["items"] if i["kind"] == "frame" and i["role"] == "files"]


def test_a_workspace_upload_never_lands_in_files(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    board = client.get(f"/api/projects/{pid}/board").json()
    inspirations = next(i for i in board["items"] if i.get("role") == "inspirations")
    sent = client.post(
        f"/api/projects/{pid}/media",
        params={"placed": "true"},
        files={"file": ("ambiance.png", _png(), "image/png")},
    )
    assert sent.status_code == 201, sent.text
    media = sent.json()
    assert media["placed"] is True
    # La course : une lecture du tableau AVANT que la carte soit écrite.
    between = client.get(f"/api/projects/{pid}/board").json()
    assert _files(between) == []
    assert not any(i["ref"] == media["id"] for i in between["items"])
    # La carte arrive : une seule, dans Inspirations, et rien d'autre ensuite.
    between["items"].append(_card(inspirations, media["id"]))
    assert client.put(f"/api/projects/{pid}/board", json=between).status_code == 200
    after = client.get(f"/api/projects/{pid}/board").json()
    mine = [i for i in after["items"] if i["ref"] == media["id"]]
    assert len(mine) == 1 and mine[0]["frame"] == inspirations["id"]
    assert _files(after) == []


def test_copies_and_empty_files_frames_already_made_are_cleaned(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    board = client.get(f"/api/projects/{pid}/board").json()
    inspirations = next(i for i in board["items"] if i.get("role") == "inspirations")
    # Comme avant le correctif : envoi sans marque, lecture du tableau, copie dans « Fichiers ».
    old = client.post(
        f"/api/projects/{pid}/media", files={"file": ("ambiance.png", _png(), "image/png")}
    ).json()
    damaged = client.get(f"/api/projects/{pid}/board").json()
    assert len(_files(damaged)) == 1
    # La carte du front arrive ensuite (la copie reste à côté, comme chez augus).
    damaged["items"].append(_card(inspirations, old["id"]))
    assert client.put(f"/api/projects/{pid}/board", json=damaged).status_code == 200
    repaired = client.get(f"/api/projects/{pid}/board").json()
    mine = [i for i in repaired["items"] if i["ref"] == old["id"]]
    assert len(mine) == 1 and mine[0]["frame"] == inspirations["id"]
    assert _files(repaired) == [], "le cadre « Fichiers » vidé de sa copie s'en va"


def test_a_file_joined_by_the_api_still_enters_files(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    note = client.post(
        f"/api/projects/{pid}/media", files={"file": ("notes.txt", b"mesures", "text/plain")}
    ).json()
    assert note["placed"] is False
    board = client.get(f"/api/projects/{pid}/board").json()
    frames = _files(board)
    assert len(frames) == 1
    inside = [i for i in board["items"] if i["frame"] == frames[0]["id"]]
    assert [(i["kind"], i["ref"]) for i in inside] == [("file", note["id"])]
    # Synchronisé de nouveau : ni dédoublé, ni retiré (le cadre n'est pas vide).
    again = client.get(f"/api/projects/{pid}/board").json()
    assert len(_files(again)) == 1
    assert sum(1 for i in again["items"] if i["ref"] == note["id"]) == 1
