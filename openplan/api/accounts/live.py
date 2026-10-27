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
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from uuid import UUID

from anyio import CancelScope
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from openplan.api.access import SESSION_COOKIE, AuthUser
from openplan.api.accounts.lease import leases_of
from openplan.api.accounts.lease import message as lease_message
from openplan.api.accounts.routes import service
from openplan.api.accounts.service import AccountService

log = logging.getLogger("openplan.live")
router = APIRouter(tags=["présence"])

MAX_MESSAGE_BYTES = 2048
MAX_MESSAGES_PER_S = 20
CLOSE_NO_SESSION = 4401
CLOSE_BAD_ORIGIN = 4403
CLOSE_NOT_MEMBER = 4404
CLOSE_TOO_CHATTY = 1008
RECHECK_S = 30.0
"""Revérification de la session et de l'appartenance de chaque connexion, toutes les 30 s de
temps écoulé, que la connexion parle ou non (KAN-106 : le client pinge toutes les 25 s ; une
revérification au silence ne tombait donc jamais pour un onglet ouvert)."""
PALETTE = ("#e5484d", "#f76b15", "#ffc53d", "#46a758", "#12a594", "#0090ff", "#6e56cf", "#d6409f")
MAX_SELECTION = 100
MAX_ID_CHARS = 64
MAX_COORD = 1e7
ZOOM_RANGE = (0.01, 16.0)
BELL = '{"type":"bell"}'
"""KAN-52 : « ta cloche a changé », sans dire de quoi (le client relit `/api/notifications`)."""
SEND_TIMEOUT_S = 5.0
"""Connexion lente (25 oct.) : un envoi qui ne part pas en 5 s ferme cette connexion (le client
se reconnecte et resynchronise) ; les autres reçoivent en parallèle. Avant, les envois partaient
l'un après l'autre et chaque écriture REST les attendait : un pair au réseau mort retenait la
réponse de tout le monde jusqu'au délai du ping d'uvicorn (20 + 20 s)."""


async def _deliver(socket: WebSocket, text: str) -> None:
    """Un envoi borné ; une connexion morte ou bloquée est fermée, jamais attendue."""
    try:
        await asyncio.wait_for(socket.send_text(text), timeout=SEND_TIMEOUT_S)
    except TimeoutError:
        log.debug("connexion trop lente : fermée")
        _closing.add(task := asyncio.ensure_future(_close_quietly(socket)))
        task.add_done_callback(_closing.discard)
    except Exception:  # connexion morte : elle partira à sa fermeture
        log.debug("diffusion impossible vers une connexion fermée")


_closing: set[asyncio.Future[None]] = set()


async def _close_quietly(socket: WebSocket) -> None:
    try:
        await asyncio.wait_for(socket.close(code=1013), timeout=SEND_TIMEOUT_S)
    except Exception:  # déjà fermée, ou impossible à fermer proprement
        log.debug("fermeture d'une connexion bloquée")


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

    def join_me(self, user_id: str, conn: str, socket: WebSocket) -> None: ...

    def leave_me(self, user_id: str, conn: str) -> None: ...

    async def ring(self, user_ids: Iterable[str]) -> None: ...


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
        self.accounts: dict[str, dict[str, WebSocket]] = {}
        """KAN-52 : par compte, ses connexions `/ws/me` (tous ses onglets et appareils)."""
        self.loop: asyncio.AbstractEventLoop | None = None
        """La boucle du serveur, pour sonner depuis le fil d'une tâche (rendu terminé)."""

    def join_me(self, user_id: str, conn: str, socket: WebSocket) -> None:
        self.loop = asyncio.get_running_loop()
        self.accounts.setdefault(user_id, {})[conn] = socket

    def leave_me(self, user_id: str, conn: str) -> None:
        sockets = self.accounts.get(user_id, {})
        sockets.pop(conn, None)
        if not sockets:
            self.accounts.pop(user_id, None)

    async def ring(self, user_ids: Iterable[str]) -> None:
        """La cloche de ces comptes a changé : un avis sans contenu, le client relit."""
        sockets = [
            socket
            for user_id in set(user_ids)
            for socket in list(self.accounts.get(user_id, {}).values())
        ]
        await asyncio.gather(*(_deliver(socket, BELL) for socket in sockets))

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
        targets = [e.socket for k, e in list(self.rooms.get(project_id, {}).items()) if to(k, e)]
        await asyncio.gather(*(_deliver(socket, text) for socket in targets))

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


class _Receiver:
    """La lecture des messages d'une connexion, jamais annulée par la revérification : à
    l'échéance, la lecture en cours continue au tour suivant. Une lecture annulée au moment où
    son message arrive le perd avec le client de test de Starlette (blocage du passage 20) ;
    uvicorn, lui, le garde, mais on ne dépend pas du transport."""

    def __init__(self, socket: WebSocket) -> None:
        self.socket = socket
        self.pending: asyncio.Task[str] | None = None

    async def until(self, due: float) -> str | None:
        """Le prochain message, ou None si la revérification (`due`) arrive d'abord."""
        if self.pending is None:
            self.pending = asyncio.ensure_future(self.socket.receive_text())
        done, _ = await asyncio.wait({self.pending}, timeout=max(0.0, due - time.monotonic()))
        if not done:
            return None
        task, self.pending = self.pending, None
        return task.result()

    def stop(self) -> None:
        """À la fermeture : la lecture en cours est abandonnée, son erreur éventuelle lue."""
        task, self.pending = self.pending, None
        if task is None:
            return
        if task.done():
            if not task.cancelled():
                task.exception()
        else:
            task.cancel()


async def _refusal(
    accounts: AccountService, raw: str, project_id: UUID | None, user_id: UUID
) -> int | None:
    """KAN-106 : le code de fermeture si la session (révoquée, expirée) ou, pour un projet,
    l'appartenance ne tient plus ; None si la connexion peut continuer."""
    if await run_in_threadpool(accounts.resolve_session, raw) is None:
        return CLOSE_NO_SESSION
    if project_id is not None and (
        await run_in_threadpool(accounts.role_name, project_id, user_id) is None
    ):
        return CLOSE_NOT_MEMBER
    return None


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
    due = time.monotonic() + RECHECK_S
    receiver = _Receiver(socket)
    try:
        while True:
            text = await receiver.until(due)
            if time.monotonic() >= due:
                refused = await _refusal(accounts, raw, project_id, user.id)
                if refused is not None:
                    await socket.close(code=refused)
                    break
                due = time.monotonic() + RECHECK_S
            if text is None:
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
        receiver.stop()
        # Même si la tâche est annulée pendant la fermeture (le client de test annule juste
        # après avoir fermé ; arrêt du serveur), le départ est annoncé et les baux rendus
        # (blocage du passage 20). Borné : chaque envoi de `_left` part en 5 s au plus.
        with CancelScope(shield=True):
            await _left(socket.app, hub, project_id, conn, peer.user_id)


@router.websocket("/ws/me")
async def my_live(socket: WebSocket) -> None:
    """KAN-52 : le canal d'un compte, pour sa cloche (« bell » quand elle change). Mêmes
    contrôles que `/ws/projects` : origine de l'application, session valable (revérifiée toutes
    les 30 s), messages petits et peu nombreux ; il ne reçoit que des pings."""
    accounts = service(socket)
    if (socket.headers.get("origin") or "").strip().lower() != accounts.settings.public_origin:
        await socket.close(code=CLOSE_BAD_ORIGIN)
        return
    raw = socket.cookies.get(SESSION_COOKIE, "")
    resolved = await run_in_threadpool(accounts.resolve_session, raw) if raw else None
    if resolved is None:
        await socket.close(code=CLOSE_NO_SESSION)
        return
    await socket.accept()
    hub = hub_of(socket.app)
    conn = secrets.token_urlsafe(12)
    me = str(resolved.user.id)
    hub.join_me(me, conn, socket)
    await socket.send_text(json.dumps({"type": "hello", "conn": conn}))
    recent: deque[float] = deque()
    due = time.monotonic() + RECHECK_S
    receiver = _Receiver(socket)
    try:
        while True:
            text = await receiver.until(due)
            if time.monotonic() >= due:
                refused = await _refusal(accounts, raw, None, resolved.user.id)
                if refused is not None:
                    await socket.close(code=refused)
                    break
                due = time.monotonic() + RECHECK_S
            if text is None:
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
        pass
    finally:
        receiver.stop()
        hub.leave_me(me, conn)


async def ring_project(app: Any, project_id: UUID, but: str | None = None) -> None:
    """KAN-52 : la cloche des membres du projet a pu changer (sauf celle de l'auteur)."""
    members = await run_in_threadpool(app.state.accounts.members, project_id)
    await hub_of(app).ring(str(m.user_id) for m in members if str(m.user_id) != but)


def ring_project_from_thread(app: Any, project_id: UUID) -> None:
    """Depuis le fil d'une tâche (rendu terminé) : sonne sur la boucle du serveur, si un
    compte est à l'écoute (sinon, la cloche se relira à l'ouverture)."""
    hub = hub_of(app)
    loop = getattr(hub, "loop", None)
    if loop is None or loop.is_closed():
        return
    members = app.state.accounts.members(project_id)
    asyncio.run_coroutine_threadsafe(hub.ring(str(m.user_id) for m in members), loop)


async def _left(app: Any, hub: LiveHub, project_id: UUID, conn: str, user_id: str) -> None:
    """Une connexion fermée ; la dernière de cette personne rend ses baux de plan (B2d)."""
    await hub.leave(project_id, conn)
    if hub.present(project_id, user_id):
        return
    leases = leases_of(app)
    for level in leases.release(project_id, user_id):
        await hub.publish(project_id, lease_message(level, None, leases.clock()))
