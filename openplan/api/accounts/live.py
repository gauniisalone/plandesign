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
- B2b, éphémère et sans écriture : curseur (coordonnées de l'espace de travail), sélection
  (ids), « Suivre » (qui suit qui) et vue de la personne suivie. Le serveur valide, puis relaie
  aux autres personnes (jamais à ses propres onglets) ; la vue ne part qu'à ceux qui suivent.
  Un message invalide est ignoré.
- Un membre retiré (ou un projet supprimé) : ses connexions sont fermées tout de suite
  (`evict`). Et toutes les 30 s, chaque connexion revérifie sa session et l'appartenance
  (session révoquée, « me déconnecter partout ») : un ancien membre ne voit plus rien passer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from openplan.api.access import SESSION_COOKIE, AuthUser
from openplan.api.accounts.lease import leases_of
from openplan.api.accounts.lease import message as lease_message
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
MAX_SELECTION = 100
MAX_ID_CHARS = 64
MAX_COORD = 1e7
ZOOM_RANGE = (0.01, 16.0)


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


@dataclass
class Conn:
    """Une connexion ouverte et son état éphémère (B2b) : jamais stocké."""

    socket: WebSocket
    peer: Peer
    selection: list[str] = field(default_factory=list)
    following: str | None = None


class LiveHub(Protocol):
    async def join(self, project_id: UUID, conn: str, socket: WebSocket, peer: Peer) -> None: ...

    async def leave(self, project_id: UUID, conn: str) -> None: ...

    async def publish(
        self, project_id: UUID, message: dict[str, Any], exclude: str | None = None
    ) -> None: ...

    def peers(self, project_id: UUID) -> list[Peer]: ...

    def selections(self, project_id: UUID, but: str) -> dict[str, list[str]]: ...

    def followers(self, project_id: UUID, user_id: str) -> int: ...

    async def presence(self, project_id: UUID, conn: str, message: dict[str, Any]) -> None: ...

    async def tell_user(self, project_id: UUID, user_id: str, message: dict[str, Any]) -> None: ...

    def present(self, project_id: UUID, user_id: str) -> bool: ...

    async def evict(self, project_id: UUID, user_id: UUID | None = None) -> None: ...


def _coord(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) and abs(number) <= MAX_COORD else None


def _ids(value: object) -> list[str] | None:
    if not isinstance(value, list) or len(value) > MAX_SELECTION:
        return None
    if not all(isinstance(i, str) and 0 < len(i) <= MAX_ID_CHARS for i in value):
        return None
    return list(dict.fromkeys(value))


def _cursor(me: str, message: dict[str, Any]) -> dict[str, Any] | None:
    """Un curseur : dans l'espace de travail, ou (B2d) sur un plan, en mètres, avec son id ;
    `x` nul : hors du canevas."""
    if message.get("x") is None:
        return {"type": "cursor", "user_id": me, "x": None}
    x, y = _coord(message.get("x")), _coord(message.get("y"))
    level = message.get("level_id")
    bad_level = level is not None and (
        not isinstance(level, str) or not 0 < len(level) <= MAX_ID_CHARS
    )
    if x is None or y is None or bad_level:
        return None
    cursor: dict[str, Any] = {"type": "cursor", "user_id": me, "x": x, "y": y}
    if level is not None:
        cursor["level_id"] = level
    return cursor


def _relayed(entry: Conn, message: dict[str, Any]) -> dict[str, Any] | None:
    """Le message à relayer pour un curseur, une sélection ou une vue ; None s'il est invalide.
    Une sélection valable devient la sélection courante de la connexion."""
    me = entry.peer.user_id
    kind = message.get("type")
    if kind == "cursor":
        return _cursor(me, message)
    if kind == "select":
        ids = _ids(message.get("ids"))
        if ids is not None:
            entry.selection = ids
        return None if ids is None else {"type": "select", "user_id": me, "ids": ids}
    if kind == "view":
        cx, cy, zoom = (_coord(message.get(k)) for k in ("cx", "cy", "zoom"))
        if cx is None or cy is None or zoom is None or not ZOOM_RANGE[0] <= zoom <= ZOOM_RANGE[1]:
            return None
        return {"type": "view", "user_id": me, "cx": cx, "cy": cy, "zoom": zoom}
    return None


class MemoryHub:
    """Diffusion dans le processus : par projet, les connexions ouvertes et leur personne.
    Plusieurs onglets d'une même personne comptent pour une présence."""

    def __init__(self) -> None:
        self.rooms: dict[UUID, dict[str, Conn]] = {}

    def peers(self, project_id: UUID) -> list[Peer]:
        seen: dict[str, Peer] = {}
        for entry in self.rooms.get(project_id, {}).values():
            seen.setdefault(entry.peer.user_id, entry.peer)
        return list(seen.values())

    def selections(self, project_id: UUID, but: str) -> dict[str, list[str]]:
        """Sélections courantes des autres personnes (la dernière d'un de leurs onglets)."""
        out: dict[str, list[str]] = {}
        for entry in self.rooms.get(project_id, {}).values():
            if entry.peer.user_id != but and entry.selection:
                out[entry.peer.user_id] = entry.selection
        return out

    def followers(self, project_id: UUID, user_id: str) -> int:
        """Combien de personnes (pas d'onglets) suivent celle-ci."""
        return len(
            {
                e.peer.user_id
                for e in self.rooms.get(project_id, {}).values()
                if e.following == user_id and e.peer.user_id != user_id
            }
        )

    def _present(self, project_id: UUID, user_id: str) -> bool:
        return any(e.peer.user_id == user_id for e in self.rooms.get(project_id, {}).values())

    async def join(self, project_id: UUID, conn: str, socket: WebSocket, peer: Peer) -> None:
        first = not self._present(project_id, peer.user_id)
        self.rooms.setdefault(project_id, {})[conn] = Conn(socket, peer)
        if first:
            await self.publish(project_id, {"type": "join", "peer": asdict(peer)}, exclude=conn)

    async def leave(self, project_id: UUID, conn: str) -> None:
        room = self.rooms.get(project_id, {})
        entry = room.pop(conn, None)
        if not room:
            self.rooms.pop(project_id, None)
        if entry is None:
            return
        if entry.following is not None:
            await self._tell_followed(project_id, entry.following)
        if not self._present(project_id, entry.peer.user_id):
            await self.publish(project_id, {"type": "leave", "user_id": entry.peer.user_id})

    async def publish(
        self, project_id: UUID, message: dict[str, Any], exclude: str | None = None
    ) -> None:
        await self._send(project_id, message, lambda key, _: key != exclude)

    async def _send(
        self,
        project_id: UUID,
        message: dict[str, Any],
        to: Callable[[str, Conn], bool],
    ) -> None:
        text = json.dumps(message)
        for key, entry in list(self.rooms.get(project_id, {}).items()):
            if not to(key, entry):
                continue
            try:
                await entry.socket.send_text(text)
            except Exception:  # connexion morte : elle partira à sa fermeture
                log.debug("diffusion impossible vers une connexion fermée")

    async def _tell_followed(self, project_id: UUID, user_id: str) -> None:
        """Dit à la personne suivie (tous ses onglets) combien la suivent."""
        count = self.followers(project_id, user_id)
        await self._send(
            project_id,
            {"type": "followed", "count": count},
            lambda _, e: e.peer.user_id == user_id,
        )

    async def presence(self, project_id: UUID, conn: str, message: dict[str, Any]) -> None:
        """Curseur, sélection, « Suivre », vue : validés puis relayés ; invalides, ignorés."""
        entry = self.rooms.get(project_id, {}).get(conn)
        if entry is None:
            return
        kind = message.get("type")
        if kind == "follow":
            await self._follow(project_id, entry, message.get("user_id"))
            return
        relay = _relayed(entry, message)
        if relay is None:
            return
        me = entry.peer.user_id
        if kind == "view":  # seulement à ceux qui me suivent
            await self._send(project_id, relay, lambda _, e: e.following == me)
        else:  # aux autres personnes, jamais à mes propres onglets
            await self._send(project_id, relay, lambda _, e: e.peer.user_id != me)

    async def _follow(self, project_id: UUID, entry: Conn, target: object) -> None:
        if target is not None and (
            not isinstance(target, str)
            or target == entry.peer.user_id
            or not self._present(project_id, target)
        ):
            return
        before, entry.following = entry.following, target
        for changed in {before, target} - {None}:
            await self._tell_followed(project_id, str(changed))

    async def tell_user(self, project_id: UUID, user_id: str, message: dict[str, Any]) -> None:
        """B2d : un message pour une seule personne (tous ses onglets), « Demander la main »."""
        await self._send(project_id, message, lambda _, e: e.peer.user_id == user_id)

    def present(self, project_id: UUID, user_id: str) -> bool:
        return self._present(project_id, user_id)

    async def evict(self, project_id: UUID, user_id: UUID | None = None) -> None:
        """Ferme les connexions de cette personne sur le projet (toutes si `user_id` est None) ;
        leur départ est annoncé aux autres par la fermeture elle-même."""
        for entry in list(self.rooms.get(project_id, {}).values()):
            if user_id is None or entry.peer.user_id == str(user_id):
                try:
                    await entry.socket.close(code=CLOSE_NOT_MEMBER)
                except Exception:  # déjà fermée
                    log.debug("connexion déjà fermée")


def hub_of(app: Any) -> LiveHub:
    hub: LiveHub | None = getattr(app.state, "live", None)
    if hub is None:
        hub = MemoryHub()
        app.state.live = hub
    return hub


async def _incoming(
    hub: LiveHub, project_id: UUID, conn: str, socket: WebSocket, text: str
) -> None:
    """Un message reçu (déjà limité en taille et en débit) : ping, sinon présence (B2b)."""
    if text == '{"type":"ping"}':
        await socket.send_text('{"type":"pong"}')
        return
    try:
        message = json.loads(text)
    except ValueError:
        return
    if isinstance(message, dict):
        await hub.presence(project_id, conn, message)


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
                # B2b : sélections courantes des autres, et combien me suivent déjà.
                "selections": hub.selections(project_id, but=peer.user_id),
                "followed": hub.followers(project_id, peer.user_id),
                # B2d : qui tient quel plan.
                "leases": leases_of(socket.app).state(project_id),
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
            await _incoming(hub, project_id, conn, socket, text)
    except (WebSocketDisconnect, RuntimeError):
        # Fermée par le client, ou par le serveur (membre retiré, session révoquée).
        pass
    finally:
        await _left(socket.app, hub, project_id, conn, peer.user_id)


async def _left(app: Any, hub: LiveHub, project_id: UUID, conn: str, user_id: str) -> None:
    """Une connexion fermée ; la dernière de cette personne rend ses baux de plan (B2d)."""
    await hub.leave(project_id, conn)
    if hub.present(project_id, user_id):
        return
    leases = leases_of(app)
    for level in leases.release(project_id, user_id):
        await hub.publish(project_id, lease_message(level, None, leases.clock()))
