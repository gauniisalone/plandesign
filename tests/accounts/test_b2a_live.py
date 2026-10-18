"""B2a : présence en temps réel (WebSocket par projet), avis de changement, CSP wss://,
« Votre nom » à la première connexion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openplan.api.accounts.live import CLOSE_BAD_ORIGIN, CLOSE_NO_SESSION, CLOSE_NOT_MEMBER
from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in


def _client(app: object) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})  # type: ignore[arg-type]


def _ws(client: TestClient, pid: str, origin: str = ORIGIN) -> Any:
    # Le client de test n'envoie pas le cookie `Secure` sur ws:// : on le passe nous-mêmes
    # (le navigateur, lui, l'envoie sur wss://).
    headers = {"origin": origin}
    raw = client.cookies.get("pd_session")
    if raw:
        headers["cookie"] = f"pd_session={raw}"
    return client.websocket_connect(f"/ws/projects/{pid}", headers=headers)


def _team(tmp_path: Path, url: str) -> tuple[TestClient, TestClient, Any, str]:
    owner, service, mailer = accounts_client(tmp_path, url, signup="open")
    sign_in(owner, mailer, ADMIN)
    pid = owner.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "viewer"}).json()["token"]
    guest = _client(owner.app)
    sign_in(guest, mailer, "marie.durand@example.org")
    assert guest.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    return owner, guest, service, pid


def test_csp_names_the_wss_origin_except_in_local_mode(tmp_path: Path, migrated_url: str) -> None:
    client, _, _ = accounts_client(tmp_path, migrated_url)
    csp = client.get("/api/health").headers["content-security-policy"]
    assert "connect-src 'self' wss://app.example.org;" in csp
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    assert "connect-src 'self';" in local.get("/api/health").headers["content-security-policy"]
    exposed = TestClient(
        create_app(
            settings=Settings(
                data_dir=tmp_path / "exp",
                host="0.0.0.0",
                api_tokens={"augus": "a" * 32},
                public_url="https://app.example.org",
            )
        )
    )
    assert "wss://app.example.org" in exposed.get("/api/health").headers["content-security-policy"]


def test_handshake_checks_origin_session_and_membership(tmp_path: Path, migrated_url: str) -> None:
    owner, _, service, pid = _team(tmp_path, migrated_url)
    for client, origin, code in (
        (owner, "https://ailleurs.example.org", CLOSE_BAD_ORIGIN),
        (_client(owner.app), ORIGIN, CLOSE_NO_SESSION),  # sans session
    ):
        with pytest.raises(WebSocketDisconnect) as closed, _ws(client, pid, origin) as ws:
            ws.receive_text()
        assert closed.value.code == code
    service.create_user("paul@example.org")
    stranger = _client(owner.app)
    sign_in(stranger, service.mailer, "paul@example.org")
    with pytest.raises(WebSocketDisconnect) as closed, _ws(stranger, pid) as ws:
        ws.receive_text()
    assert closed.value.code == CLOSE_NOT_MEMBER


def test_presence_join_leave_names_and_tabs(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as mine:
        hello = mine.receive_json()
        assert hello["type"] == "hello" and hello["peers"] == []
        assert hello["you"]["name"] == "admin" and hello["you"]["color"].startswith("#")
        with _ws(guest, pid) as hers:
            theirs = hers.receive_json()
            assert [p["name"] for p in theirs["peers"]] == ["admin"]
            joined = mine.receive_json()
            assert joined["type"] == "join" and joined["peer"]["name"] == "marie.durand"
            assert "@" not in str(joined)  # jamais l'adresse complète
            # Un second onglet de la même personne : pas de seconde arrivée.
            with _ws(guest, pid) as second:
                second.receive_json()
                mine.send_text('{"type":"ping"}')
                assert mine.receive_json() == {"type": "pong"}
            # Elle a encore un onglet ouvert : pas de départ.
            mine.send_text('{"type":"ping"}')
            assert mine.receive_json() == {"type": "pong"}
        left = mine.receive_json()
        assert left["type"] == "leave" and left["user_id"] == joined["peer"]["user_id"]


def test_changes_are_announced_to_the_others(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as mine, _ws(guest, pid) as hers:
        conn = mine.receive_json()["conn"]
        hers.receive_json()
        mine.receive_json()  # arrivée de l'invitée
        # Un geste de l'autre onglet du propriétaire (ou d'un autre membre) : annoncé.
        owner.patch(f"/api/projects/{pid}", json={"name": "Grenier bis"})
        assert mine.receive_json() == {"type": "changed", "scope": "project"}
        assert hers.receive_json() == {"type": "changed", "scope": "project"}
        # Le geste de CETTE connexion (X-Live-Conn) : les autres seulement.
        owner.patch(
            f"/api/projects/{pid}", json={"name": "Grenier ter"}, headers={"X-Live-Conn": conn}
        )
        assert hers.receive_json() == {"type": "changed", "scope": "project"}
        mine.send_text('{"type":"ping"}')
        assert mine.receive_json() == {"type": "pong"}  # rien d'autre n'était en attente
        # Le tableau : son propre avis.
        board = owner.get(f"/api/projects/{pid}/board").json()
        assert owner.put(f"/api/projects/{pid}/board", json=board).status_code == 200
        assert hers.receive_json() == {"type": "changed", "scope": "board"}


def test_oversized_messages_close_the_connection(tmp_path: Path, migrated_url: str) -> None:
    owner, _, _, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as ws:
        ws.receive_json()
        ws.send_text("x" * 3000)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == 1008


def test_first_login_and_your_name(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    client.post("/api/auth/email/start", json={"email": "lea@example.org"})
    first = client.post(
        "/api/auth/email/verify",
        json={"email": "lea@example.org", "code": code_of(mailer, "lea@example.org")},
    )
    assert first.json()["first_login"] is True
    named = client.patch("/api/auth/me", json={"name": "  Léa   Martin "})
    assert named.status_code == 200 and named.json()["name"] == "Léa Martin"
    assert client.get("/api/auth/me").json()["name"] == "Léa Martin"
    assert client.patch("/api/auth/me", json={"name": "x" * 81}).status_code == 422
    other = _client(client.app)
    other.post("/api/auth/email/start", json={"email": "lea@example.org"})
    again = other.post(
        "/api/auth/email/verify",
        json={"email": "lea@example.org", "code": code_of(mailer, "lea@example.org")},
    )
    assert again.json()["first_login"] is False


def test_a_removed_member_is_disconnected_and_hears_nothing(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, guest, service, pid = _team(tmp_path, migrated_url)
    marie = service.user_by_email("marie.durand@example.org")
    assert marie is not None
    with _ws(owner, pid) as mine, _ws(guest, pid) as hers:
        mine.receive_json()
        hers.receive_json()
        mine.receive_json()  # arrivée de l'invitée
        assert owner.delete(f"/api/projects/{pid}/members/{marie.id}").status_code == 200
        heard: list[Any] = []
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                heard.append(hers.receive_json())
        assert closed.value.code == CLOSE_NOT_MEMBER
        assert heard == []  # fermée avant l'avis du retrait : elle n'a rien entendu
        # Le propriétaire : son départ, et l'avis du changement (dans un ordre ou l'autre).
        assert {mine.receive_json()["type"], mine.receive_json()["type"]} == {"leave", "changed"}
        # Elle ne peut pas se reconnecter non plus.
        with pytest.raises(WebSocketDisconnect) as again, _ws(guest, pid) as ws:
            ws.receive_text()
        assert again.value.code == CLOSE_NOT_MEMBER


def test_a_revoked_session_is_closed_at_the_next_check(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openplan.api.accounts import live

    monkeypatch.setattr(live, "RECHECK_S", 0.2)
    owner, guest, _, pid = _team(tmp_path, migrated_url)
    with _ws(guest, pid) as hers:
        hers.receive_json()
        # « Me déconnecter partout », depuis un autre appareil : la session de ce WebSocket
        # est révoquée ; à la revérification suivante, il est fermé.
        other = _client(owner.app)
        other.cookies.set("pd_session", guest.cookies.get("pd_session") or "")
        assert other.post("/api/auth/logout-all").status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            while True:
                hers.receive_json()
        assert closed.value.code == CLOSE_NO_SESSION
