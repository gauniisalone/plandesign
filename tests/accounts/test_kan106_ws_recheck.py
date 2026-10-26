"""KAN-106 : la session et l'appartenance d'un WebSocket sont revérifiées au temps écoulé, même
quand le client parle. Avant, la revérification n'avait lieu qu'après 30 s de silence ; le front
pinge toutes les 25 s, elle ne tombait donc jamais : une session révoquée (« Me déconnecter
partout », administrateur) ou expirée gardait le direct du projet.

Temps à l'échelle (comme test_b2a_live) : revérification toutes les 0,6 s au lieu de 30 s, un
ping toutes les 0,2 s au lieu de 10 s ; « fermé en 30 à 40 s » devient « fermé en 0,8 s au plus »
après l'événement."""

from __future__ import annotations

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
