"""KAN-78 : réactions aux éléments de l'espace de travail, côté serveur. Six emojis fixes ; une
réaction par personne, élément et emoji ; on ne retire que la sienne ; un lecteur ne réagit pas
(403) ; un élément retiré emporte ses réactions ; le journal dit « a réagi 😍 à « … » » ; la
cloche prévient l'auteur de l'élément (regroupé), jamais celui qui réagit ; `reactions.json`
dans le dossier du projet (compté dans la place, suivi par l'archive) ; en local, à l'identique."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.events import ProjectEvents
from openplan.pipeline.reactions import REACTIONS_FILE
from tests.accounts.test_kan51_comments import _team


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plusieurs personnes depuis le même client de test : limite relevée pour ces tests."""
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _note(text: str) -> dict[str, Any]:
    return {"id": str(uuid4()), "kind": "note", "x": 0, "y": 0, "w": 200, "h": 200, "text": text}


def _add_note(client: TestClient, pid: str, text: str) -> str:
    note = _note(text)
    response = client.patch(f"/api/projects/{pid}/board/items", json={"set": [note]})
    assert response.status_code == 200, response.text
    return str(note["id"])


def _react(client: TestClient, pid: str, item: str, key: str) -> Any:
    return client.put(f"/api/projects/{pid}/reactions/{item}/{key}")


def _unreact(client: TestClient, pid: str, item: str, key: str) -> Any:
    return client.delete(f"/api/projects/{pid}/reactions/{item}/{key}")


def _pills(client: TestClient, pid: str) -> list[dict[str, Any]]:
    response = client.get(f"/api/projects/{pid}/reactions")
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _bell(client: TestClient) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get("/api/notifications", params={"tab": "all"}).json()[
        "items"
    ]
    return [line for line in items if line["type"] == "reaction.added"]


def test_add_remove_mine_only_and_names(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    item = _add_note(m["editor"], pid, "Rendu 2")
    assert _react(m["commenter"], pid, item, "love").status_code == 200
    assert _react(m["commenter"], pid, item, "love").status_code == 200  # sans effet
    assert _react(owner, pid, item, "love").status_code == 200
    assert _react(owner, pid, item, "up").status_code == 200
    pills = _pills(m["commenter"], pid)
    assert [(p["key"], p["emoji"], p["count"], p["mine"]) for p in pills] == [
        ("up", "👍", 1, False),
        ("love", "😍", 2, True),
    ]
    love = pills[1]
    assert love["names"][0] == "Vous" and "admin" in love["names"][1]
    # Retirer : seulement la mienne ; celle du propriétaire reste.
    after = _unreact(m["commenter"], pid, item, "love").json()
    assert [(p["key"], p["count"], p["mine"]) for p in after] == [
        ("up", 1, False),
        ("love", 1, False),
    ]
    # Retirer une réaction que je n'ai pas : rien ne bouge.
    assert _unreact(m["commenter"], pid, item, "up").json() == after


def test_rights_a_viewer_does_not_react(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    item = _add_note(owner, pid, "Canapé")
    assert _react(owner, pid, item, "fire").status_code == 200
    assert m["viewer"].get(f"/api/projects/{pid}/reactions").status_code == 200
    assert _react(m["viewer"], pid, item, "fire").status_code == 403
    assert _unreact(m["viewer"], pid, item, "fire").status_code == 403
    for role in ("commenter", "editor"):
        assert _react(m[role], pid, item, "think").status_code == 200
    assert _react(owner, pid, str(uuid4()), "up").status_code == 404
    assert _react(owner, pid, item, "smile").status_code == 422
    assert [p["count"] for p in _pills(owner, pid)] == [2, 1]


def test_a_removed_item_takes_its_reactions(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    kept = _add_note(owner, pid, "Garde")
    gone = _add_note(owner, pid, "Retire")
    for item in (kept, gone):
        assert _react(m["commenter"], pid, item, "heart").status_code == 200
    deleted = owner.patch(f"/api/projects/{pid}/board/items", json={"delete": [gone]})
    assert deleted.status_code == 200, deleted.text
    assert [p["item_id"] for p in _pills(owner, pid)] == [kept]
    store = owner.app.state.store  # type: ignore[attr-defined]
    data = json.loads((store.project_dir(pid) / REACTIONS_FILE).read_text(encoding="utf-8"))
    assert [r["item_id"] for r in data["reactions"]] == [kept]


def test_journal_and_bell_reach_the_author_never_myself(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    item = _add_note(m["editor"], pid, "Rendu 2")
    assert _react(m["commenter"], pid, item, "love").status_code == 200
    # Le journal d'activité : qui, quel emoji, quel élément (filtre « Réactions »).
    lines = owner.get(f"/api/projects/{pid}/activity", params={"kind": "reactions"}).json()["items"]
    assert len(lines) == 1
    line = lines[0]
    assert line["actor_name"] == "commenter" and line["emoji"] == "😍"
    assert line["label"] == "Rendu 2" and line["item_id"] == item
    # La cloche : l'auteur de l'élément (l'éditeur qui l'a posé), pas le propriétaire, pas
    # celui qui réagit.
    assert len(_bell(m["editor"])) == 1 and _bell(m["editor"])[0]["emoji"] == "😍"
    assert _bell(owner) == [] and _bell(m["commenter"]) == []
    # Regroupé : deux réactions de la même personne à la suite font une seule ligne.
    assert _react(m["commenter"], pid, item, "fire").status_code == 200
    grouped = _bell(m["editor"])
    assert len(grouped) == 1 and grouped[0]["count"] == 2 and grouped[0]["emoji"] is None
    assert grouped[0]["label"] == "Rendu 2"
    # Mes propres réactions ne me notifient pas, même sur mon élément.
    assert _react(m["editor"], pid, item, "up").status_code == 200
    assert len(_bell(m["editor"])) == 1
    # Retirer n'entre pas au journal.
    before = len(ProjectEvents(owner.app.state.store.project_dir(pid)).read())  # type: ignore[attr-defined]
    _unreact(m["editor"], pid, item, "up")
    assert len(ProjectEvents(owner.app.state.store.project_dir(pid)).read()) == before  # type: ignore[attr-defined]


def test_an_element_of_unknown_author_notifies_the_owner(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    # Un cadre posé par le projet lui-même (mise en page de départ) : auteur inconnu.
    frame = next(
        i for i in owner.get(f"/api/projects/{pid}/board").json()["items"] if i["kind"] == "frame"
    )
    assert _react(m["commenter"], pid, frame["id"], "up").status_code == 200
    assert len(_bell(owner)) == 1 and _bell(m["editor"]) == []


def test_local_mode_is_the_same_and_the_archive_keeps_them(tmp_path: Path) -> None:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    pid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    local.get(f"/api/projects/{pid}/board")
    item = _add_note(local, pid, "Idée")
    pills = _react(local, pid, item, "heart").json()
    assert pills == [
        {"item_id": item, "key": "heart", "emoji": "❤️", "count": 1, "mine": True, "names": ["Vous"]}
    ]
    lines = local.get(f"/api/projects/{pid}/activity", params={"kind": "reactions"}).json()["items"]
    assert lines[0]["emoji"] == "❤️" and lines[0]["actor_name"] == "Vous"
    archive = zipfile.ZipFile(io.BytesIO(local.get(f"/api/projects/{pid}/exports/archive").content))
    kept = json.loads(archive.read(REACTIONS_FILE))
    assert kept["reactions"][0]["author"] == "local"
    assert _unreact(local, pid, item, "heart").json() == []


def test_an_unknown_event_line_is_ignored(tmp_path: Path) -> None:
    """Le journal d'un code plus récent (ou plus ancien) ne fait jamais d'erreur : une ligne d'un
    type inconnu est ignorée (`pipeline.events`), comme une réaction pour un code d'avant."""
    events = ProjectEvents(tmp_path)
    (tmp_path / "events.jsonl").write_text(
        '{"type": "reaction.future", "project_id": "' + str(uuid4()) + '", "actor": "local"}\n',
        encoding="utf-8",
    )
    assert events.read() == []
