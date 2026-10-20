"""KAN-51, lot 1 : commentaires de l'espace de travail, côté serveur. Droits par rôle (refus par
défaut), l'auteur seul modifie ou supprime son message, le propriétaire supprime tout fil,
« Ancien membre » sans erreur ni adresse, texte gardé tel quel (jamais interprété), événements
pour la cloche, et le mode local identique."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.comments import EVENTS_FILE
from tests.accounts.helpers import ADMIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client

NOTE = {"at": {"x": 10, "y": 20}}


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quatre personnes se connectent depuis le même client de test : la limite des tentatives
    (10 par minute et par adresse) est relevée pour ces tests seulement."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _team(tmp_path: Path, url: str) -> tuple[TestClient, dict[str, TestClient], str, Any]:
    """Le propriétaire et un membre par rôle (lecteur, commentateur, éditeur)."""
    owner, service, mailer = accounts_client(tmp_path, url, signup="open")
    sign_in(owner, mailer, ADMIN)
    pid = owner.post("/api/projects", json={"name": "Salon"}).json()["id"]
    members: dict[str, TestClient] = {}
    for role in ("viewer", "commenter", "editor"):
        token = owner.post(f"/api/projects/{pid}/invitations", json={"role": role}).json()["token"]
        client = _client(owner.app)
        sign_in(client, mailer, f"{role}@example.org")
        assert client.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
        members[role] = client
    return owner, members, pid, service


def _thread(client: TestClient, pid: str, text: str = "Plus de lumière ici") -> Any:
    return client.post(f"/api/projects/{pid}/comments", json={"target": NOTE, "text": text})


def test_rights_by_role(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    # Lecteur : lit, n'écrit rien.
    assert m["viewer"].get(f"/api/projects/{pid}/comments").status_code == 200
    assert _thread(m["viewer"], pid).status_code == 403
    # Commentateur : commente, répond, résout.
    made = _thread(m["commenter"], pid)
    assert made.status_code == 201
    tid = made.json()["id"]
    assert (
        m["viewer"]
        .post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "x"})
        .status_code
        == 403
    )
    replied = m["editor"].post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Oui"})
    assert replied.status_code == 201
    assert m["commenter"].post(f"/api/projects/{pid}/comments/{tid}/resolve").status_code == 200
    assert m["viewer"].post(f"/api/projects/{pid}/comments/{tid}/reopen").status_code == 403
    # Le commentateur ne modifie ni l'espace de travail ni le plan (Must have 6).
    board = m["commenter"].patch(
        f"/api/projects/{pid}/board/items", json={"view": {"x": 0, "y": 0, "zoom": 1}}
    )
    assert board.status_code == 403
    level = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    assert (
        m["commenter"].post(f"/api/projects/{pid}/levels/{level}/ops", json={}).status_code == 403
    )
    # Non-membre : 404, sans révéler le projet.
    stranger = _client(owner.app)
    sign_in(stranger, owner.app.state.accounts.mailer, "x@example.org")  # type: ignore[attr-defined]
    assert stranger.get(f"/api/projects/{pid}/comments").status_code == 404


def test_only_the_author_edits_or_deletes_and_the_owner_deletes_threads(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    thread = _thread(m["commenter"], pid).json()
    tid, mid = thread["id"], thread["messages"][0]["id"]
    url = f"/api/projects/{pid}/comments/{tid}/messages/{mid}"
    assert m["editor"].patch(url, json={"text": "pirate"}).status_code == 403
    assert owner.patch(url, json={"text": "pirate"}).status_code == 403  # même le propriétaire
    edited = m["commenter"].patch(url, json={"text": "Plus de lumière, ici"})
    assert edited.status_code == 200 and edited.json()["messages"][0]["edited_at"]
    # L'auteur supprime son fil tant qu'il n'a pas de réponse.
    assert m["commenter"].get(f"/api/projects/{pid}/comments").json()[0]["can_delete"] is True
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Oui"})
    assert m["commenter"].delete(f"/api/projects/{pid}/comments/{tid}").status_code == 403
    assert m["editor"].delete(url).status_code == 403  # pas son message
    # Le propriétaire supprime tout fil.
    assert owner.delete(f"/api/projects/{pid}/comments/{tid}").status_code == 204
    assert owner.get(f"/api/projects/{pid}/comments").json() == []


def test_names_colors_former_member_and_text_kept_as_is(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, service = _team(tmp_path, migrated_url)
    script = "<script>alert(1)</script> & <b>gras</b>"
    tid = _thread(m["editor"], pid, script).json()["id"]
    m["commenter"].post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Bien"})
    seen = owner.get(f"/api/projects/{pid}/comments").json()[0]["messages"]
    assert seen[0]["text"] == script  # gardé tel quel ; le front l'affiche en texte
    assert seen[0]["author_name"] == "editor" and seen[0]["author_color"].startswith("#")
    assert seen[0]["mine"] is False
    assert "@" not in json.dumps(seen)
    # Le commentateur quitte le projet : « Ancien membre », sans erreur ni adresse.
    gone = service.user_by_email("commenter@example.org")
    assert owner.delete(f"/api/projects/{pid}/members/{gone.id}").status_code == 200
    seen = owner.get(f"/api/projects/{pid}/comments").json()[0]["messages"]
    assert seen[1]["author_name"] == "Ancien membre" and "@" not in json.dumps(seen)
    # Trop long, vide : refusés.
    assert _thread(owner, pid, "x" * 2001).status_code == 422
    assert _thread(owner, pid, "   ").status_code == 422


def test_events_for_the_bell(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    item = str(uuid4())
    made = m["commenter"].post(
        f"/api/projects/{pid}/comments",
        json={"target": {"at": {"x": 4, "y": 4}, "item_id": item}, "text": "Ce rendu"},
    )
    tid = made.json()["id"]
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Oui"})
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/resolve")
    m["editor"].post(f"/api/projects/{pid}/comments/{tid}/reopen")
    store = owner.app.state.store  # type: ignore[attr-defined]
    lines = (store.project_dir(UUID(pid)) / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert [e["type"] for e in events] == [
        "comment.created",
        "comment.replied",
        "comment.resolved",
        "comment.reopened",
    ]
    assert all(e["thread_id"] == tid and e["item_id"] == item for e in events)
    assert set(events[0]) == {"type", "project_id", "actor", "thread_id", "item_id", "at"}
    assert "Ce rendu" not in "\n".join(lines) and "@" not in "\n".join(lines)


def test_local_mode_is_the_same(tmp_path: Path) -> None:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    thread = _thread(local, pid).json()
    message = thread["messages"][0]
    assert message["author_name"] == "Vous" and message["mine"] is True
    tid = thread["id"]
    assert (
        local.post(
            f"/api/projects/{pid}/comments/{tid}/replies", json={"text": "Et ici"}
        ).status_code
        == 201
    )
    assert local.post(f"/api/projects/{pid}/comments/{tid}/resolve").json()["resolved"] is True
    assert local.post(f"/api/projects/{pid}/comments/{tid}/reopen").json()["resolved"] is False
    # L'archive du projet emporte les fils (auteur « local », aucune donnée personnelle).
    _thread(local, pid, "Gardé dans l'archive")
    import io
    import zipfile

    archive = zipfile.ZipFile(io.BytesIO(local.get(f"/api/projects/{pid}/exports/archive").content))
    kept = json.loads(archive.read("comments.json"))
    assert kept["threads"][-1]["messages"][0]["author"] == "local"
    assert local.delete(f"/api/projects/{pid}/comments/{tid}").status_code == 204


def test_listing_writes_nothing_and_a_removed_item_leaves_its_last_position(
    tmp_path: Path,
) -> None:
    """La liste est une lecture pure (elle suit l'élément sans écrire) ; la dernière position
    s'enregistre quand l'élément est retiré par le PATCH du tableau."""
    data = tmp_path / "local"
    local = TestClient(create_app(settings=Settings(data_dir=data)))
    pid = local.post("/api/projects", json={"name": "Épingles"}).json()["id"]
    item = next(i for i in local.get(f"/api/projects/{pid}/board").json()["items"])
    target = {"at": {"x": 10, "y": 10}, "item_id": item["id"]}
    assert (
        local.post(
            f"/api/projects/{pid}/comments", json={"target": target, "text": "Ici"}
        ).status_code
        == 201
    )
    moved = {**item, "x": item["x"] + 300}
    patch = local.patch(f"/api/projects/{pid}/board/items", json={"set": [moved]})
    assert patch.status_code == 200, patch.text
    path = data / "projects" / pid / "comments.json"
    if not path.is_file():
        path = next(data.rglob("comments.json"))
    before = (path.stat().st_mtime_ns, path.read_bytes())
    listed = local.get(f"/api/projects/{pid}/comments").json()
    assert listed[0]["target"]["last"]["x"] == moved["x"]
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before
    # L'élément retiré : le fil garde sa dernière position, enregistrée, et reste attaché à
    # son id (le front l'affiche « détaché »).
    gone = local.patch(f"/api/projects/{pid}/board/items", json={"delete": [item["id"]]})
    assert gone.status_code == 200, gone.text
    kept = json.loads(path.read_text(encoding="utf-8"))["threads"][0]["target"]
    assert kept["item_id"] == item["id"] and kept["last"]["x"] == moved["x"]
