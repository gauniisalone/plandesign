"""KAN-119 : `POST /api/projects/delete-all` (« Supprimer tous mes projets »). Le mot
SUPPRIMER est exigé par le serveur ; seuls les projets dont on est propriétaire partent ; un
projet partagé avec soi n'est pas touché ; les WS ouvertes sont fermées ; débit limité ; au
journal d'usage, le nombre seul."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

from openplan.api.accounts.live import CLOSE_NOT_MEMBER
from openplan.api.accounts.models import Event
from openplan.api.app import create_app
from openplan.api.routes.projects import DELETE_ALL_LIMIT
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in

ROUTE = "/api/projects/delete-all"
MARIE = "marie.durand@example.org"


def _ws(client: TestClient, pid: str) -> Any:
    headers = {"origin": ORIGIN, "cookie": f"pd_session={client.cookies.get('pd_session')}"}
    return client.websocket_connect(f"/ws/projects/{pid}", headers=headers)


def _team(tmp_path: Path, url: str) -> tuple[TestClient, TestClient, str, str]:
    """Le propriétaire a « Grenier », partagé avec Marie ; Marie a « Bureau », à elle."""
    owner, _, mailer = accounts_client(tmp_path, url, signup="open")
    sign_in(owner, mailer, ADMIN)
    shared = owner.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    token = owner.post(f"/api/projects/{shared}/invitations", json={"role": "editor"}).json()
    marie = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(marie, mailer, MARIE)
    accepted = marie.post("/api/auth/invitation/accept", json={"token": token["token"]})
    assert accepted.status_code == 200
    hers = marie.post("/api/projects", json={"name": "Bureau"}).json()["id"]
    return owner, marie, shared, hers


def _ids(client: TestClient) -> set[str]:
    return {p["id"] for p in client.get("/api/projects").json()}


def test_a_member_deletes_hers_and_the_shared_project_stays(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, marie, shared, hers = _team(tmp_path, migrated_url)
    stranger = str(uuid4())
    r = marie.post(
        ROUTE, json={"confirm": "SUPPRIMER", "project_ids": [hers, shared, stranger, hers]}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": [hers], "failed": [shared, stranger]}
    assert _ids(marie) == {shared}
    assert _ids(owner) == {shared}
    assert owner.get(f"/api/projects/{shared}").status_code == 200
    events = owner.app.state.events
    with events.session() as db:
        rows = [e for e in db.scalars(select(Event)) if e.name == "projects_deleted"]
    assert [(e.value, e.project) for e in rows] == [(1, None)], "le nombre seul"


def test_the_owner_deletes_and_open_sockets_are_closed(tmp_path: Path, migrated_url: str) -> None:
    owner, marie, shared, hers = _team(tmp_path, migrated_url)
    with _ws(marie, shared) as ws:
        ws.receive_json()
        r = owner.post(ROUTE, json={"confirm": "SUPPRIMER", "project_ids": [shared]})
        assert r.status_code == 200 and r.json() == {"deleted": [shared], "failed": []}
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                ws.receive_json()
        assert closed.value.code == CLOSE_NOT_MEMBER
    assert _ids(owner) == set()
    assert _ids(marie) == {hers}


@pytest.mark.parametrize("word", ["", "supprimer", " SUPPRIMER", "SUPPRIME", "OUI"])
def test_a_wrong_word_is_refused_by_the_server(
    tmp_path: Path, migrated_url: str, word: str
) -> None:
    owner, _, shared, _ = _team(tmp_path, migrated_url)
    r = owner.post(ROUTE, json={"confirm": word, "project_ids": [shared]})
    assert r.status_code == 400
    assert "SUPPRIMER" in r.json()["detail"]
    assert _ids(owner) == {shared}


def test_requests_are_rate_limited_per_account(tmp_path: Path, migrated_url: str) -> None:
    owner, marie, shared, hers = _team(tmp_path, migrated_url)
    for _ in range(DELETE_ALL_LIMIT):
        assert owner.post(ROUTE, json={"confirm": "NON", "project_ids": []}).status_code == 400
    slow = owner.post(ROUTE, json={"confirm": "SUPPRIMER", "project_ids": [shared]})
    assert slow.status_code == 429 and slow.headers["retry-after"] == "3600"
    assert _ids(owner) == {shared}
    # L'autre compte a sa propre limite.
    r = marie.post(ROUTE, json={"confirm": "SUPPRIMER", "project_ids": [hers]})
    assert r.json() == {"deleted": [hers], "failed": []}


def test_without_accounts_every_listed_project_goes(tmp_path: Path) -> None:
    with TestClient(create_app(settings=Settings(data_dir=tmp_path / "data"))) as c:
        ids = [c.post("/api/projects", json={"name": f"P{i}"}).json()["id"] for i in range(3)]
        r = c.post(ROUTE, json={"confirm": "SUPPRIMER", "project_ids": ids[:2]})
        assert r.status_code == 200 and r.json() == {"deleted": ids[:2], "failed": []}
        assert [p["id"] for p in c.get("/api/projects").json()] == [ids[2]]
