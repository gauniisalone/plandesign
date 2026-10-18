"""Présence en temps réel (note 14 § 7, B2a) : un WebSocket par projet ouvert,
`/ws/projects/{id}`.

- Le WebSocket ne sert qu'à DIFFUSER : présence (arrivées, départs, liste) et avis « le projet
  a changé ». Toutes les écritures restent en REST, derrière les gardes de chaque route.
- Poignée de main : cookie de session valable, en-tête `Origin` = l'origine de l'application
  (SameSite=Lax ne protège pas un WebSocket ouvert depuis un autre site), membre du projet
  (lecteur et plus). Refus : fermeture avec un code 44xx, sans rien révéler du projet.
- Diffusion en mémoire, dans le processus (un seul worker uvicorn) : `LiveHub` est la seule
  porte, pour passer un jour à Postgres LISTEN/NOTIFY sans toucher au reste.
- Rien n'est stocké. Le nom montré aux autres : celui du compte, sinon le début de l'adresse
  avant le @ ; jamais l'adresse complète.
- Messages entrants : petits (2 Kio au plus), peu nombreux (20 par seconde au plus) ; au-delà,
  la connexion est fermée.
- Un membre retiré (ou un projet supprimé) : ses connexions sont fermées tout de suite
  (`evict`). Et toutes les 30 s, chaque connexion revérifie sa session et l'appartenance
  (session révoquée, « me déconnecter partout ») : un ancien membre ne voit plus rien passer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from openplan.api.access import SESSION_COOKIE, AuthUser
from openplan.api.accounts.routes import service

log = logging.getLogger("openplan.live")
router = APIRouter(tags=["présence"])

MAX_MESSAGE_BYTES = 2048
MAX_MESSAGES_PER_S = 20
CLOSE_NO_SESSION = 4401
CLOSE_BAD_ORIGIN = 4403
CLOSE_NOT_MEMBER = 4404
CLOSE_TOO_CHATTY = 1008
RECHECK_S = 30.0
"""Revérification de la session et de l'appartenance de chaque connexion."""
PALETTE = ("#e5484d", "#f76b15", "#ffc53d", "#46a758", "#12a594", "#0090ff", "#6e56cf", "#d6409f")


def display_name(user: AuthUser) -> str:
    """Le nom du compte, sinon le début de l'adresse avant le @ (jamais l'adresse entière)."""
    name = user.name.strip()
    return name[:80] if name else user.email.split("@", 1)[0][:40]


def color_of(user_id: UUID) -> str:
    return PALETTE[hashlib.sha256(user_id.bytes).digest()[0] % len(PALETTE)]


@dataclass(frozen=True)
class Peer:
    user_id: str
    name: str
    color: str


class LiveHub(Protocol):
    async def join(self, project_id: UUID, conn: str, socket: WebSocket, peer: Peer) -> None: ...

    async def leave(self, project_id: UUID, conn: str) -> None: ...

    async def publish(
        self, project_id: UUID, message: dict[str, Any], exclude: str | None = None
    ) -> None: ...

    def peers(self, project_id: UUID) -> list[Peer]: ...

    async def evict(self, project_id: UUID, user_id: UUID | None = None) -> None: ...


class MemoryHub:
    """Diffusion dans le processus : par projet, les connexions ouvertes et leur personne.
    Plusieurs onglets d'une même personne comptent pour une présence."""

    def __init__(self) -> None:
        self.rooms: dict[UUID, dict[str, tuple[WebSocket, Peer]]] = {}

    def peers(self, project_id: UUID) -> list[Peer]:
        seen: dict[str, Peer] = {}
        for _, peer in self.rooms.get(project_id, {}).values():
            seen.setdefault(peer.user_id, peer)
        return list(seen.values())

    def _present(self, project_id: UUID, user_id: str) -> bool:
        return any(p.user_id == user_id for _, p in self.rooms.get(project_id, {}).values())

    async def join(self, project_id: UUID, conn: str, socket: WebSocket, peer: Peer) -> None:
        first = not self._present(project_id, peer.user_id)
        self.rooms.setdefault(project_id, {})[conn] = (socket, peer)
        if first:
            await self.publish(project_id, {"type": "join", "peer": asdict(peer)}, exclude=conn)

    async def leave(self, project_id: UUID, conn: str) -> None:
        room = self.rooms.get(project_id, {})
        entry = room.pop(conn, None)
        if not room:
            self.rooms.pop(project_id, None)
        if entry is not None and not self._present(project_id, entry[1].user_id):
            await self.publish(project_id, {"type": "leave", "user_id": entry[1].user_id})

    async def publish(
        self, project_id: UUID, message: dict[str, Any], exclude: str | None = None
    ) -> None:
        text = json.dumps(message)
        for conn, (socket, _) in list(self.rooms.get(project_id, {}).items()):
            if conn == exclude:
                continue
            try:
                await socket.send_text(text)
            except Exception:  # connexion morte : elle partira à sa fermeture
                log.debug("diffusion impossible vers une connexion fermée")

    async def evict(self, project_id: UUID, user_id: UUID | None = None) -> None:
        """Ferme les connexions de cette personne sur le projet (toutes si `user_id` est None) ;
        leur départ est annoncé aux autres par la fermeture elle-même."""
        for socket, peer in list(self.rooms.get(project_id, {}).values()):
            if user_id is None or peer.user_id == str(user_id):
                try:
                    await socket.close(code=CLOSE_NOT_MEMBER)
                except Exception:  # déjà fermée
                    log.debug("connexion déjà fermée")


def hub_of(app: Any) -> LiveHub:
    hub: LiveHub | None = getattr(app.state, "live", None)
    if hub is None:
        hub = MemoryHub()
        app.state.live = hub
    return hub


@router.websocket("/ws/projects/{project_id}")
async def project_live(socket: WebSocket, project_id: UUID) -> None:
    accounts = service(socket)
    settings = accounts.settings
    if (socket.headers.get("origin") or "").strip().lower() != settings.public_origin:
        await socket.close(code=CLOSE_BAD_ORIGIN)
        return
    resolved = await run_in_threadpool(
        accounts.resolve_session, socket.cookies.get(SESSION_COOKIE, "")
    )
    if resolved is None:
        await socket.close(code=CLOSE_NO_SESSION)
        return
    user = resolved.user
    if await run_in_threadpool(accounts.role_name, project_id, user.id) is None:
        await socket.close(code=CLOSE_NOT_MEMBER)
        return
    await socket.accept()
    hub = hub_of(socket.app)
    conn = secrets.token_urlsafe(12)
    peer = Peer(user_id=str(user.id), name=display_name(user), color=color_of(user.id))
    await socket.send_text(
        json.dumps(
            {
                "type": "hello",
                "conn": conn,
                "you": asdict(peer),
                # État complet à chaque (re)connexion : la resynchronisation part de là.
                "peers": [asdict(p) for p in hub.peers(project_id) if p.user_id != peer.user_id],
            }
        )
    )
    await hub.join(project_id, conn, socket, peer)
    recent: deque[float] = deque()
    raw = socket.cookies.get(SESSION_COOKIE, "")
    try:
        while True:
            try:
                text = await asyncio.wait_for(socket.receive_text(), timeout=RECHECK_S)
            except TimeoutError:
                if await run_in_threadpool(accounts.resolve_session, raw) is None:
                    await socket.close(code=CLOSE_NO_SESSION)
                    break
                if await run_in_threadpool(accounts.role_name, project_id, user.id) is None:
                    await socket.close(code=CLOSE_NOT_MEMBER)
                    break
                continue
            now = time.monotonic()
            recent.append(now)
            while recent and now - recent[0] > 1.0:
                recent.popleft()
            if len(text.encode()) > MAX_MESSAGE_BYTES or len(recent) > MAX_MESSAGES_PER_S:
                await socket.close(code=CLOSE_TOO_CHATTY)
                break
            if text == '{"type":"ping"}':
                await socket.send_text('{"type":"pong"}')
    except (WebSocketDisconnect, RuntimeError):
        # Fermée par le client, ou par le serveur (membre retiré, session révoquée).
        pass
    finally:
        await hub.leave(project_id, conn)
