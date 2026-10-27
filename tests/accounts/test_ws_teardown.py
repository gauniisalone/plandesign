"""Passage 20 : `make check` bloqué sur un test WebSocket. En fermant une session, le client de
test de Starlette envoie la déconnexion puis annule aussitôt la tâche du serveur ; sous charge,
l'annulation tombait pendant le départ (`_left`) : ni « leave » ni bail rendu, et le test qui
les attendait sur une autre connexion attendait sans fin. Le départ se fait désormais à l'abri
de l'annulation. Ici, un départ ralenti rend la coïncidence certaine."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from openplan.api.accounts import live
from tests.accounts.test_b2a_live import _team, _ws


def _receive_within(ws: Any, seconds: float) -> Any:
    """Le prochain message, ou None s'il n'arrive pas à temps (jamais d'attente sans fin)."""
    got: list[Any] = []
    reader = threading.Thread(target=lambda: got.append(ws.receive_json()), daemon=True)
    reader.start()
    reader.join(seconds)
    return got[0] if got else None


def test_a_slow_departure_is_still_announced(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    leave = live.MemoryHub.leave

    async def slow(self: live.MemoryHub, project_id: UUID, conn: str) -> None:
        await asyncio.sleep(0.2)  # une fermeture sous charge : l'annulation tombe ici
        await leave(self, project_id, conn)

    monkeypatch.setattr(live.MemoryHub, "leave", slow)
    owner, guest, _service, pid = _team(tmp_path, migrated_url)
    with _ws(owner, pid) as watch:
        watch.receive_json()
        with _ws(guest, pid) as hers:
            her_id = hers.receive_json()["you"]["user_id"]
            assert watch.receive_json()["type"] == "join"
        assert _receive_within(watch, 3.0) == {"type": "leave", "user_id": her_id}
