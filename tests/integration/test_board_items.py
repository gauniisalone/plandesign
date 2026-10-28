"""B2c : l'espace de travail en dernière écriture par élément (`PATCH …/board/items`). Le
serveur horodate chaque élément écrit et note son acteur ; deux gestes sur deux éléments
différents sont gardés tous les deux ; sur le même élément, le dernier reçu gagne ; aucun 409.
Le PUT du tableau entier reste, avec son 409, pour la compatibilité."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    client = TestClient(create_app(settings=Settings(data_dir=tmp_path / "data")))
    pid = client.post("/api/projects", json={"name": "Atelier"}).json()["id"]
    return client, pid


def _note(text: str, x: float = 0.0, **extra: Any) -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "kind": "note",
        "x": x,
        "y": 0,
        "w": 200,
        "h": 120,
        "text": text,
        **extra,
    }


def _items(client: TestClient, pid: str) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in client.get(f"/api/projects/{pid}/board").json()["items"]}


def test_writes_are_stamped_by_the_server(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    note = _note("Idée", updated_at="2000-01-01T00:00:00Z", updated_by="moi")
    out = client.patch(f"/api/projects/{pid}/board/items", json={"set": [note]})
    assert out.status_code == 200
    written = next(i for i in out.json()["items"] if i["id"] == note["id"])
    assert written["updated_by"] == "user"  # l'acteur de la requête, pas celui envoyé
    assert not written["updated_at"].startswith("2000")
    assert _items(client, pid)[note["id"]]["text"] == "Idée"


def test_two_gestures_on_two_items_are_both_kept(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    a, b = _note("A"), _note("B", x=300)
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [a, b]})
    # Deux onglets partis du même état : chacun déplace sa carte, sans révision.
    moved_a = client.patch(f"/api/projects/{pid}/board/items", json={"set": [{**a, "x": 50}]})
    moved_b = client.patch(f"/api/projects/{pid}/board/items", json={"set": [{**b, "x": 450}]})
    assert moved_a.status_code == 200 and moved_b.status_code == 200
    items = _items(client, pid)
    assert items[a["id"]]["x"] == 50 and items[b["id"]]["x"] == 450


def test_on_the_same_item_the_last_write_wins(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    note = _note("Première")
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [note]})
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [{**note, "text": "Alice"}]})
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [{**note, "text": "Bruno"}]})
    assert _items(client, pid)[note["id"]]["text"] == "Bruno"


def test_delete_frees_children_and_remembers_what_the_project_provides(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    board = client.get(f"/api/projects/{pid}/board").json()
    photos = next(i for i in board["items"] if i["kind"] == "frame" and i["role"] == "photos")
    child = _note("Dans le cadre", frame=photos["id"])
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [child]})
    out = client.patch(
        f"/api/projects/{pid}/board/items", json={"delete": [photos["id"], str(uuid4())]}
    ).json()
    # KAN-93 : le node Photos du premier plan porte la ref de son plan, sa clé de retrait aussi.
    first = next(iter(client.get(f"/api/projects/{pid}").json()["levels"]))
    assert photos["ref"] == first
    assert f"frame:photos:{first}" in out["dismissed"]
    items = _items(client, pid)
    assert photos["id"] not in items  # le cadre fixe retiré ne revient pas
    assert items[child["id"]]["frame"] is None
    # Une écriture de plus relance la synchronisation : le node retiré ne revient toujours pas.
    _patch(client, pid, {"set": [_note("Après", x=600)]})
    again = _items(client, pid)
    assert photos["id"] not in again
    assert not any(i["kind"] == "frame" and i.get("role") == "photos" for i in again.values())


def _photo(ref: str, x: float = 0.0) -> dict[str, Any]:
    return {"id": str(uuid4()), "kind": "photo", "x": x, "y": 0, "w": 280, "h": 190, "ref": ref}


def _patch(client: TestClient, pid: str, body: dict[str, Any]) -> dict[str, Any]:
    out = client.patch(f"/api/projects/{pid}/board/items", json=body)
    assert out.status_code == 200
    board: dict[str, Any] = out.json()
    return board


def test_a_card_duplicated_on_purpose_survives_a_patch(tmp_path: Path) -> None:
    """« Dupliquer » (moodboard A3) : deux cartes de la même image, voulues. Écrire l'une ne
    retire jamais l'autre ; le serveur ne dédoublonne rien de lui-même."""
    client, pid = _client(tmp_path)
    first = _photo("img-1")
    copy = {**first, "id": str(uuid4()), "x": 400}
    _patch(client, pid, {"set": [first]})
    _patch(client, pid, {"set": [copy]})
    out = _patch(client, pid, {"set": [{**first, "x": 50}]})
    photos = [i for i in out["items"] if i["kind"] == "photo"]
    assert sorted(p["id"] for p in photos) == sorted([first["id"], copy["id"]])


def test_removing_the_sync_duplicate_does_not_dismiss_the_image(tmp_path: Path) -> None:
    """Le client retire le doublon posé par la synchronisation pendant qu'il posait sa propre
    carte de la même photo : l'image n'est pas « retirée à la main » (sa carte reste)."""
    client, pid = _client(tmp_path)
    synced, mine = _photo("img-1"), _photo("img-1", x=500)
    _patch(client, pid, {"set": [synced, mine]})
    out = _patch(client, pid, {"delete": [synced["id"]]})
    assert [i["id"] for i in out["items"] if i["kind"] == "photo"] == [mine["id"]]
    assert "photo:img-1" not in out["dismissed"]
    # La dernière carte retirée, elle : l'image est notée retirée, comme avant.
    out = _patch(client, pid, {"delete": [mine["id"]]})
    assert "photo:img-1" in out["dismissed"]


def test_view_and_validation(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    out = client.patch(
        f"/api/projects/{pid}/board/items", json={"view": {"x": 10, "y": -5, "zoom": 2}}
    )
    assert out.json()["view"] == {"x": 10.0, "y": -5.0, "zoom": 2.0}
    bad = client.patch(f"/api/projects/{pid}/board/items", json={"set": [_note("x", w=-1)]})
    assert bad.status_code == 422
    assert client.patch(f"/api/projects/{pid}/board/items", json={"inconnu": 1}).status_code == 422


def test_the_whole_board_put_keeps_its_409(tmp_path: Path) -> None:
    client, pid = _client(tmp_path)
    board = client.get(f"/api/projects/{pid}/board").json()
    client.patch(f"/api/projects/{pid}/board/items", json={"set": [_note("après")]})
    stale = client.put(f"/api/projects/{pid}/board", json=board)
    assert stale.status_code == 409
