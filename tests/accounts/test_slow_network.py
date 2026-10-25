"""Connexion lente (MEP 14, 25 oct.) : la profondeur du navigateur arrive compressée (gzip ou
deflate) et se décompresse sous un plafond (bombe refusée sans gonfler) ; la diffusion en
direct n'attend plus une connexion bloquée (envois en parallèle, bornés, la bloquée fermée)."""

from __future__ import annotations

import asyncio
import gzip
import zlib
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from openplan.api.accounts import live
from openplan.api.accounts.live import MemoryHub, Peer
from openplan.api.body_encoding import decoded_body
from openplan.pipeline.client_depth import MAX_BYTES
from openplan.pipeline.reference import depth_paths, load_depth
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in
from tests.accounts.test_kan79_client_depth import FIXTURE, _photo, box_grid


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _status(body: bytes, encoding: str | None, cap: int = 1000) -> int:
    with pytest.raises(HTTPException) as caught:
        decoded_body(body, encoding, cap)
    return caught.value.status_code


def test_decoded_body_formats_and_limits() -> None:
    data = bytes(range(256)) * 3
    assert decoded_body(data, None, 1000) == data
    assert decoded_body(data, "identity", 1000) == data
    assert decoded_body(gzip.compress(data), "gzip", 1000) == data
    assert decoded_body(gzip.compress(data), " GZip ", 1000) == data
    assert decoded_body(zlib.compress(data), "deflate", 1000) == data
    assert _status(data, "br") == 415
    # Bombe : 50 Mo de zéros en ~50 Ko ; refusée au plafond, sans tout décompresser.
    bomb = gzip.compress(b"\0" * 50_000_000)
    assert len(bomb) < 100_000
    assert _status(bomb, "gzip", cap=MAX_BYTES) == 413
    # Compressé plus gros que le plafond : refusé avant décompression.
    assert _status(b"x" * 1001, "gzip") == 413
    assert _status(gzip.compress(data)[:-12], "gzip") == 400  # tronqué
    assert _status(b"pas du gzip", "gzip") == 400


def _put(client: Any, pid: str, lid: str, iid: str, raw: bytes, encoding: str | None) -> Any:
    headers = {"Content-Type": "application/octet-stream", "Origin": ORIGIN}
    if encoding:
        headers["Content-Encoding"] = encoding
    return client.put(
        f"/api/projects/{pid}/levels/{lid}/images/{iid}/depth",
        params={"image_width": 1280, "image_height": 960},
        content=raw,
        headers=headers,
    )


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_depth_route_accepts_gzip_and_refuses_a_bomb(tmp_path: Path, migrated_url: str) -> None:
    owner, _service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Pièce"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    iid = _photo(owner, pid)
    raw = box_grid().tobytes()
    store = owner.app.state.store  # type: ignore[attr-defined]
    project_dir = store.project_dir(UUID(pid))

    bomb = gzip.compress(b"\0" * 20_000_000)
    refused = _put(owner, pid, lid, iid, bomb, "gzip")
    assert refused.status_code == 413, refused.text
    assert not depth_paths(project_dir, UUID(iid))[0].exists()
    assert _put(owner, pid, lid, iid, raw, "br").status_code == 415

    packed = gzip.compress(raw)
    assert len(packed) < len(raw)
    done = _put(owner, pid, lid, iid, packed, "gzip")
    assert done.status_code == 200, done.text
    assert done.json()["depth_size"] == [640, 480]
    stored = load_depth(project_dir, UUID(iid))
    assert stored is not None and stored.depth.shape == (480, 640)
    # Sans compression, comme avant.
    assert _put(owner, pid, lid, iid, raw, None).status_code == 200


def test_uvicorn_pings_are_spaced_and_patient(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avec les défauts d'uvicorn (ping 20 s, fermeture sans pong sous 20 s), les WebSockets
    d'une connexion lente tombaient au bout de 60 ou 120 s tout ronds. `openplan serve` (le CMD
    de l'image de prod) espace le ping et l'attend plus longtemps, rechargement compris."""
    import argparse

    import uvicorn

    from openplan.api.settings import Settings
    from openplan.cli import main

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *_a, **kw: calls.append(kw))
    settings = Settings()
    for reload in (False, True):
        main._run_server(
            argparse.Namespace(reload=reload),
            settings,
            lambda **_kw: object(),  # type: ignore[arg-type]
        )
    assert len(calls) == 2
    for kw in calls:
        assert kw["ws_ping_interval"] == main.WS_PING_INTERVAL_S >= 25.0
        assert kw["ws_ping_timeout"] == main.WS_PING_TIMEOUT_S >= 60.0
    assert calls[1]["reload"] is True


class _Socket:
    def __init__(self, stuck: bool = False) -> None:
        self.stuck = stuck
        self.sent: list[str] = []
        self.closed: int | None = None

    async def send_text(self, text: str) -> None:
        if self.stuck:
            await asyncio.sleep(3600)
        self.sent.append(text)

    async def close(self, code: int = 1000) -> None:
        self.closed = code


def test_a_stuck_peer_no_longer_holds_the_broadcast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live, "SEND_TIMEOUT_S", 0.05)

    async def scenario() -> tuple[float, list[_Socket]]:
        hub = MemoryHub()
        project = uuid4()
        sockets = [_Socket(), _Socket(stuck=True), _Socket()]
        for i, socket in enumerate(sockets):
            hub.rooms.setdefault(project, {})[f"c{i}"] = live.Conn(
                socket,  # type: ignore[arg-type]
                Peer(user_id=f"u{i}", name=f"p{i}", color="#000"),
            )
        loop = asyncio.get_running_loop()
        started = loop.time()
        await hub.publish(project, {"type": "changed", "scope": "board"})
        took = loop.time() - started
        await asyncio.sleep(0.1)  # la fermeture de la connexion bloquée
        return took, sockets

    took, sockets = asyncio.run(scenario())
    assert took < 0.5, f"diffusion retenue {took:.2f} s"
    assert len(sockets[0].sent) == 1 and len(sockets[2].sent) == 1
    assert sockets[1].sent == [] and sockets[1].closed == 1013
