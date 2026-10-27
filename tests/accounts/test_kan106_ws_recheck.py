"""KAN-106 : la session et l'appartenance d'un WebSocket sont revérifiées au temps écoulé, même
quand le client parle. Avant, la revérification n'avait lieu qu'après 30 s de silence ; le front
pinge toutes les 25 s, elle ne tombait donc jamais : une session révoquée (« Me déconnecter
partout », administrateur) ou expirée gardait le direct du projet.

Temps à l'échelle (comme test_b2a_live) : revérification toutes les 0,6 s au lieu de 30 s, un
ping toutes les 0,2 s au lieu de 10 s ; « fermé en 30 à 40 s » devient « fermé en 0,8 s au plus »
après l'événement."""

from __future__ import annotations

import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from openplan.api.accounts import live
from openplan.api.accounts.live import CLOSE_NO_SESSION, CLOSE_NOT_MEMBER
from openplan.api.accounts.service import SESSION_TTL
from tests.accounts.test_b2a_live import _client, _team, _ws
from tests.accounts.test_kan52_bell import _me_ws

RECHECK_S = 0.6
PING_S = RECHECK_S / 3  # 10 s pour 30 s
AT_MOST_S = RECHECK_S * 40 / 30  # « en 30 à 40 s »
PING = '{"type":"ping"}'


@pytest.fixture(autouse=True)
def refused(monkeypatch: pytest.MonkeyPatch) -> list[tuple[float, int]]:
    """Revérification à l'échelle ; chaque refus du serveur noté avec son heure (la fermeture
    côté serveur, avant que le client ne la voie à son ping suivant)."""
    monkeypatch.setattr(live, "RECHECK_S", RECHECK_S)
    seen: list[tuple[float, int]] = []
    refusal = live._refusal

    async def noted(*args: Any) -> int | None:
        code = await refusal(*args)
        if code is not None:
            seen.append((time.monotonic(), code))
        return code

    monkeypatch.setattr(live, "_refusal", noted)
    return seen


def _ping_for(ws: Any, seconds: float) -> tuple[int | None, float]:
    """Un client qui pinge toutes les PING_S pendant `seconds` : le code de fermeture et le
    temps écoulé, ou (None, seconds) s'il est resté ouvert."""
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        try:
            ws.send_text(PING)
            while ws.receive_json().get("type") != "pong":
                pass
        except WebSocketDisconnect as closed:
            return closed.code, time.monotonic() - start
        time.sleep(PING_S)
    return None, seconds


def _revoke_elsewhere(owner: TestClient, guest: TestClient) -> None:
    """« Me déconnecter partout », depuis un autre appareil de l'invitée."""
    other = _client(owner.app)
    other.cookies.set("pd_session", guest.cookies.get("pd_session") or "")
    assert other.post("/api/auth/logout-all").status_code == 204


def _expire(service: Any) -> None:
    clock = service.clock
    service.clock = lambda: clock() + SESSION_TTL + timedelta(days=1)


def test_a_legitimate_client_that_pings_is_never_disconnected(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quatre revérifications pendant qu'il pinge : elles ont bien lieu, et il reste connecté."""
    owner, guest, service, pid = _team(tmp_path, migrated_url)
    checks: list[float] = []
    resolve = service.resolve_session

    def counted(raw: str) -> object:
        checks.append(time.monotonic())
        return resolve(raw)

    with _ws(guest, pid) as hers, _me_ws(owner) as mine:
        hers.receive_json()
        mine.receive_json()
        monkeypatch.setattr(service, "resolve_session", counted)
        assert _ping_for(hers, RECHECK_S * 4) == (None, RECHECK_S * 4)
        assert _ping_for(mine, RECHECK_S * 2) == (None, RECHECK_S * 2)
    assert len(checks) >= 4, "la revérification doit tomber même quand le client parle"


def _in_time(refused: list[tuple[float, int]], at: float, code: int | None, elapsed: float) -> None:
    """Le serveur ferme au plus une période (30 s) après l'événement ; le client qui pinge le
    voit au plus un ping plus tard (40 s)."""
    assert [c for _t, c in refused] == [code], refused
    assert refused[0][0] - at <= RECHECK_S, refused[0][0] - at
    assert elapsed <= AT_MOST_S + 0.1, elapsed  # 0,1 s : battement du client de test


@pytest.mark.parametrize("event", ["révoquée", "expirée", "rôle retiré"])
def test_a_project_socket_that_pings_is_closed_in_time(
    tmp_path: Path, migrated_url: str, event: str, refused: list[tuple[float, int]]
) -> None:
    owner, guest, service, pid = _team(tmp_path, migrated_url)
    with _ws(guest, pid) as hers:
        hers.receive_json()
        assert _ping_for(hers, RECHECK_S * 1.5)[0] is None  # une revérification passée : ouvert
        at = time.monotonic()
        if event == "révoquée":
            _revoke_elsewhere(owner, guest)
        elif event == "expirée":
            _expire(service)
        else:
            # Retrait sans l'éviction explicite de la route des membres : c'est la
            # revérification qui doit le voir.
            marie = service.user_by_email("marie.durand@example.org")
            assert marie is not None and service.remove_membership(UUID(pid), marie.id)
        code, elapsed = _ping_for(hers, RECHECK_S * 4)
    expected = CLOSE_NOT_MEMBER if event == "rôle retiré" else CLOSE_NO_SESSION
    assert code == expected, (event, code)
    _in_time(refused, at, code, elapsed)


@pytest.mark.parametrize("event", ["révoquée", "expirée"])
def test_my_channel_that_pings_is_closed_in_time(
    tmp_path: Path, migrated_url: str, event: str, refused: list[tuple[float, int]]
) -> None:
    owner, guest, service, _pid = _team(tmp_path, migrated_url)
    with _me_ws(guest) as hers:
        hers.receive_json()
        assert _ping_for(hers, RECHECK_S * 1.5)[0] is None
        at = time.monotonic()
        if event == "révoquée":
            _revoke_elsewhere(owner, guest)
        else:
            _expire(service)
        code, elapsed = _ping_for(hers, RECHECK_S * 4)
    assert code == CLOSE_NO_SESSION, (event, code)
    _in_time(refused, at, code, elapsed)


def _pong_within(ws: Any, seconds: float) -> bool:
    """Un ping et son pong, attendu au plus `seconds` (un message perdu ne bloque pas le test)."""
    ws.send_text(PING)
    got: list[Any] = []
    reader = threading.Thread(target=lambda: got.append(ws.receive_json()), daemon=True)
    reader.start()
    reader.join(seconds)
    return got == [{"type": "pong"}]


@pytest.mark.parametrize("channel", ["projet", "compte"])
def test_a_message_arriving_with_the_recheck_is_never_lost(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch, channel: str
) -> None:
    """Passage 20 : une lecture annulée par l'échéance de la revérification perdait le message
    qui arrivait au même instant (client de test), et le test attendait sans fin. Revérification
    à chaque milliseconde : la coïncidence tombe à coup sûr en 1 500 pings (limite de débit
    levée : à 20 messages par seconde, le serveur fermerait au 21e)."""
    monkeypatch.setattr(live, "RECHECK_S", 0.001)
    monkeypatch.setattr(live, "MAX_MESSAGES_PER_S", 10**6)

    async def accepted(*_: Any) -> int | None:
        return None

    monkeypatch.setattr(live, "_refusal", accepted)
    _owner, guest, _service, pid = _team(tmp_path, migrated_url)
    with _ws(guest, pid) if channel == "projet" else _me_ws(guest) as ws:
        ws.receive_json()
        lost = next((i for i in range(1500) if not _pong_within(ws, 2.0)), None)
        assert lost is None, f"pong perdu au ping {lost}"


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Chaque tâche de lecture laissée en attente d'un tour à l'autre (échéance passée)."""
    seen: list[Any] = []
    until = live._Receiver.until

    async def noted(self: Any, due: float) -> str | None:
        text = await until(self, due)
        if self.pending is not None and self.pending not in seen:
            seen.append(self.pending)
        return text

    monkeypatch.setattr(live._Receiver, "until", noted)
    return seen


def _all_done(tasks: list[Any], seconds: float = 1.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if all(t.done() for t in tasks):
            return True
        time.sleep(0.01)
    return all(t.done() for t in tasks)


@pytest.mark.parametrize("channel", ["projet", "compte"])
def test_no_read_survives_a_server_close(
    tmp_path: Path,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    reads: list[Any],
    channel: str,
) -> None:
    """Le serveur ferme (session refusée à la revérification) pendant qu'une lecture attend :
    elle est arrêtée tout de suite, sans attendre que le client parle ou parte."""
    monkeypatch.setattr(live, "RECHECK_S", 0.05)

    async def revoked(*_: Any) -> int | None:
        return CLOSE_NO_SESSION

    monkeypatch.setattr(live, "_refusal", revoked)
    _owner, guest, _service, pid = _team(tmp_path, migrated_url)
    with _ws(guest, pid) if channel == "projet" else _me_ws(guest) as ws:
        ws.receive_json()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == CLOSE_NO_SESSION
        assert reads, "aucune lecture en attente : le test ne prouve rien"
        # Le client est encore là, muet : aucune lecture orpheline côté serveur.
        assert _all_done(reads), "lecture orpheline après la fermeture"


@pytest.mark.parametrize("channel", ["projet", "compte"])
def test_no_read_survives_a_client_close(
    tmp_path: Path,
    migrated_url: str,
    monkeypatch: pytest.MonkeyPatch,
    reads: list[Any],
    channel: str,
) -> None:
    """Le client part pendant qu'une lecture attend : rien ne reste derrière."""
    monkeypatch.setattr(live, "RECHECK_S", 0.05)
    _owner, guest, _service, pid = _team(tmp_path, migrated_url)
    with _ws(guest, pid) if channel == "projet" else _me_ws(guest) as ws:
        ws.receive_json()
        time.sleep(0.2)  # quelques échéances : la même lecture reste en attente
        assert _pong_within(ws, 2.0)
        time.sleep(0.2)
    assert reads
    assert _all_done(reads), "lecture orpheline après le départ du client"
