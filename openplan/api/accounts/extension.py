"""KAN-102 : le module navigateur « Ajouter à PlanDesign » (comptes actifs seulement).

Un clic droit sur une image d'une page web l'envoie dans le Moodboard d'un projet. Le module
s'authentifie par un jeton qui lui est propre :

- créé depuis la section Compte de l'application (session obligatoire), listé, révocable ;
  10 par compte au plus ;
- `pde_<id de la ligne>_<32 octets aléatoires>` : la base ne garde que son empreinte SHA-256 ;
  la ligne est retrouvée par son identifiant (public), puis l'empreinte comparée en temps
  constant ; le jeton n'est montré qu'une fois, à sa création ;
- il n'ouvre QUE les deux routes du module (`EXTENSION_PATH`) : la liste des projets où le
  compte est éditeur, avec leurs nodes Moodboard, et l'ajout d'une image ; toute autre route
  l'ignore (pas de session : 401) ; jamais le cookie de session sur ces deux routes ;
- l'image passe par la récupération de KAN-98 L3 (`adapters.remote.link_fetch` : garde
  anti-SSRF, 10 Mo, type reconnu à l'octet), puis par le contrôle Pillow de L3, la place du
  propriétaire (réserve d'un lien collé) et la limite de 10 liens par minute et par compte ;
  la source est gardée : la page dans `MediaRef.source_url` (montrée au survol, comme L3),
  l'adresse de l'image dans le `url` de sa carte.

Porte de l'intergiciel (`ExtensionGate`), comme celle du poste de rendu : seuls les chemins
exacts du module quittent le contrôle de session ; 401 sans jeton valable (429 par adresse IP
au-delà de 10 refus en 10 min) ; 411 sans longueur annoncée, 413 au-delà de 16 Ko de corps
(le corps ne porte que des adresses). Aucun en-tête CORS : le navigateur laisse le module
appeler l'hôte de l'application grâce à sa permission d'hôte (`host_permissions`), sans
cookie (`credentials: "omit"`) ; aucune page web ne peut donc s'en servir.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import delete, func, select

from openplan.adapters.remote import link_fetch
from openplan.api.access import AuthUser, SignedInDep
from openplan.api.accounts.db import aware, utcnow
from openplan.api.accounts.models import ExtensionToken, User
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.board_rules import board_now
from openplan.api.change_events import author_of, record_board
from openplan.api.link_images import link_limiter, link_name
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.pipeline import board as b
from openplan.pipeline import board_links as bl
from openplan.pipeline.board import MediaError
from openplan.scene.store import ProjectStore
from openplan.schemas.board import _URL, MAX_URL, BoardItem, BoardPatch
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["extension"])

TOKEN_PREFIX = "pde_"
MAX_TOKENS = 10
MAX_NAME = 60
MAX_RAW = 128
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
EXTENSION_PATH = re.compile(rf"/api/extension/(?:targets|projects/{_UUID}/images)")
"""Les seuls chemins du module (comparés en entier, sur le chemin décodé ET brut)."""
EXTENSION_REFUSED = "jeton de l'extension requis"
MAX_EXTENSION_BODY = 16 * 1024
FAILED_AUTH_LIMIT = 10
FAILED_AUTH_WINDOW_S = 600.0
USED_EVERY_S = 60.0
"""`last_used_at` n'est réécrit qu'une fois par minute au plus (liste des jetons)."""
_DUMMY_HASH = hashlib.sha256(b"jeton inconnu").hexdigest()


class TooManyTokensError(RuntimeError):
    pass


@dataclass(frozen=True)
class TokenView:
    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


def token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def token_id_of(raw: str) -> UUID | None:
    """L'identifiant de la ligne écrit dans le jeton ; None si le jeton n'a pas sa forme."""
    if len(raw) > MAX_RAW or not raw.startswith(TOKEN_PREFIX):
        return None
    head, sep, secret = raw.removeprefix(TOKEN_PREFIX).partition("_")
    if not sep or len(head) != 32 or not secret:
        return None
    try:
        return UUID(hex=head)
    except ValueError:
        return None


def _view(row: ExtensionToken) -> TokenView:
    return TokenView(row.id, row.name, row.created_at, row.last_used_at)


class ExtensionTokens:
    """Création, liste, révocation et vérification des jetons du module."""

    def __init__(self, repo: AccountsRepo, clock: Callable[[], datetime] = utcnow) -> None:
        self.repo = repo
        self.clock = clock

    def create(self, user_id: UUID, name: str) -> tuple[TokenView, str]:
        """La ligne et le jeton en clair (rendu UNE fois, jamais gardé)."""
        label = " ".join(name.split())[:MAX_NAME] or "Extension"
        with self.repo.session() as db, db.begin():
            count = db.scalar(
                select(func.count())
                .select_from(ExtensionToken)
                .where(ExtensionToken.user_id == user_id)
            )
            if (count or 0) >= MAX_TOKENS:
                msg = f"{MAX_TOKENS} jetons au plus : révoquez-en un d'abord."
                raise TooManyTokensError(msg)
            token_id = uuid4()
            raw = f"{TOKEN_PREFIX}{token_id.hex}_{secrets.token_urlsafe(32)}"
            row = ExtensionToken(
                id=token_id,
                user_id=user_id,
                name=label,
                token_hash=token_hash(raw),
                created_at=self.clock(),
            )
            db.add(row)
            view = _view(row)
        return view, raw

    def list(self, user_id: UUID) -> list[TokenView]:
        with self.repo.session() as db:
            rows = db.scalars(
                select(ExtensionToken)
                .where(ExtensionToken.user_id == user_id)
                .order_by(ExtensionToken.created_at)
            )
            return [_view(r) for r in rows]

    def revoke(self, user_id: UUID, token_id: UUID) -> bool:
        """Seulement un jeton de ce compte : celui d'un autre est « inconnu »."""
        with self.repo.session() as db, db.begin():
            done = db.execute(
                delete(ExtensionToken).where(
                    ExtensionToken.id == token_id, ExtensionToken.user_id == user_id
                )
            )
            return bool(getattr(done, "rowcount", 0))

    def resolve(self, raw: str) -> AuthUser | None:
        """Le compte du jeton, None s'il est faux, révoqué ou d'un compte effacé. L'empreinte
        est toujours calculée et comparée en temps constant, ligne trouvée ou non."""
        token_id = token_id_of(raw)
        digest = token_hash(raw)
        with self.repo.session() as db, db.begin():
            row = db.get(ExtensionToken, token_id) if token_id is not None else None
            expected = row.token_hash if row is not None else _DUMMY_HASH
            if not hmac.compare_digest(expected, digest) or row is None:
                return None
            user = db.get(User, row.user_id)
            if user is None:
                return None
            now = self.clock()
            last = row.last_used_at
            if last is None or (now - aware(last)).total_seconds() >= USED_EVERY_S:
                row.last_used_at = now
            return AuthUser(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


def tokens_of(request: Request) -> ExtensionTokens:
    tokens: ExtensionTokens = request.app.state.extension_tokens
    return tokens


# --- porte de l'intergiciel ----------------------------------------------------------------


def is_extension_path(request: Request) -> bool:
    """Chemin exact du module, sans rien d'encodé ni de normalisable autrement."""
    path: str = request.scope.get("path", "")
    raw = request.scope.get("raw_path")
    raw_text = raw.decode("latin-1") if isinstance(raw, bytes | bytearray) else path
    return EXTENSION_PATH.fullmatch(path) is not None and raw_text == path


def bearer(request: Request) -> str | None:
    scheme, _, candidate = request.headers.get("authorization", "").partition(" ")
    candidate = candidate.strip()
    if scheme.lower() != "bearer" or not candidate or len(candidate) > MAX_RAW:
        return None
    return candidate


class ExtensionGate:
    """Avant toute lecture du corps : le jeton (le compte est posé sur la requête), la taille
    du corps, les refus répétés par adresse IP. La session est ignorée."""

    def __init__(self, tokens: ExtensionTokens, clock: Callable[[], float] = time.monotonic):
        self.tokens = tokens
        self.failures = SlidingWindowLimiter(FAILED_AUTH_LIMIT, FAILED_AUTH_WINDOW_S, clock)

    async def refusal(self, request: Request) -> Response | None:
        raw = bearer(request)
        user = await run_in_threadpool(self.tokens.resolve, raw) if raw else None
        if user is None:
            client = request.client.host if request.client else "-"
            if not self.failures.allow(client):
                return JSONResponse(
                    status_code=429,
                    content={"detail": "trop d'essais refusés, réessayer plus tard"},
                    headers={"Retry-After": str(int(FAILED_AUTH_WINDOW_S))},
                )
            return JSONResponse(
                status_code=401,
                content={"detail": EXTENSION_REFUSED},
                headers={"WWW-Authenticate": "Bearer"},
            )
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse(status_code=411, content={"detail": "longueur requise"})
            if int(length) > MAX_EXTENSION_BODY:
                return JSONResponse(status_code=413, content={"detail": "envoi trop gros"})
        request.state.extension_user = user
        request.state.actor = f"user:{user.id}"
        return None

    async def passage(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        refused = await self.refusal(request)
        return refused if refused is not None else await call_next(request)


def install_gate(app: Any, accounts: object) -> ExtensionGate:
    """Les jetons et la porte, sur l'état de l'application (les tests règlent leur horloge)."""
    if not isinstance(accounts, AccountsRepo):  # pragma: no cover - comptes = AccountService
        msg = "les jetons de l'extension demandent la base des comptes"
        raise TypeError(msg)
    app.state.extension_tokens = tokens = ExtensionTokens(accounts)
    app.state.extension_gate = gate = ExtensionGate(tokens)
    return gate


def require_extension(request: Request) -> AuthUser:
    """Garde de la route : le compte posé par la porte, sinon 401 (jamais la session)."""
    user = getattr(request.state, "extension_user", None)
    if not isinstance(user, AuthUser):
        raise HTTPException(
            status_code=401, detail=EXTENSION_REFUSED, headers={"WWW-Authenticate": "Bearer"}
        )
    return user


# --- gestion des jetons (section Compte, session obligatoire) ------------------------------


class TokenOut(StrictModel):
    id: UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None


class TokenIn(StrictModel):
    name: str = Field(default="", max_length=MAX_NAME)


class TokenCreated(StrictModel):
    token: TokenOut
    secret: str
    """Le jeton en clair, montré une seule fois (à coller dans l'extension)."""


def _out(view: TokenView) -> TokenOut:
    return TokenOut(
        id=view.id, name=view.name, created_at=view.created_at, last_used_at=view.last_used_at
    )


@router.get("/api/account/extension-tokens")
def list_tokens(request: Request, user: SignedInDep) -> list[TokenOut]:
    return [_out(v) for v in tokens_of(request).list(user.id)]


@router.post("/api/account/extension-tokens", status_code=201)
def create_token(body: TokenIn, request: Request, user: SignedInDep) -> TokenCreated:
    try:
        view, raw = tokens_of(request).create(user.id, body.name)
    except TooManyTokensError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return TokenCreated(token=_out(view), secret=raw)


@router.delete("/api/account/extension-tokens/{token_id}", status_code=204)
def revoke_token(token_id: UUID, request: Request, user: SignedInDep) -> Response:
    if not tokens_of(request).revoke(user.id, token_id):
        raise HTTPException(status_code=404, detail="jeton inconnu")
    return Response(status_code=204)


# --- routes du module (jeton de l'extension) -----------------------------------------------


class MoodboardOut(StrictModel):
    id: UUID
    title: str


class TargetOut(StrictModel):
    id: UUID
    name: str
    moodboards: list[MoodboardOut]


class TargetsOut(StrictModel):
    name: str
    """Le nom montré du compte (pour que l'extension dise qui elle est)."""
    projects: list[TargetOut]


EDIT_ROLES = ("editor", "owner")


@router.get("/api/extension/targets")
def extension_targets(request: Request) -> TargetsOut:
    """Les projets où le compte du jeton est éditeur, avec leurs nodes Moodboard (titre et
    id) : de quoi choisir où mettre l'image. Rien d'autre du projet."""
    user = require_extension(request)
    accounts = request.app.state.accounts
    store: ProjectStore = request.app.state.store
    roles: dict[UUID, str] = accounts.roles_of(user.id)
    out: list[TargetOut] = []
    for summary in sorted(store.list(), key=lambda s: s.updated_at, reverse=True):
        if roles.get(summary.id) not in EDIT_ROLES:
            continue
        board = board_now(store.load(summary.id), store)
        moodboards = [
            MoodboardOut(id=i.id, title=bl.card_title(i))
            for i in board.items
            if bl.card_role(i) == "inspirations"
        ]
        out.append(TargetOut(id=summary.id, name=summary.name, moodboards=moodboards))
    return TargetsOut(name=accounts.display_of(user.id) or "", projects=out)


class ImageIn(StrictModel):
    node_id: UUID
    image_url: str = Field(min_length=1, max_length=MAX_URL)
    page_url: str | None = Field(default=None, max_length=MAX_URL)


class ImageAdded(StrictModel):
    project_id: UUID
    node_id: UUID
    media_id: UUID
    item_id: UUID
    name: str


def _web(url: str | None) -> str | None:
    """Une adresse http(s) gardable sur une carte (`BoardItem.url`), sinon None."""
    return url if url is not None and _URL.fullmatch(url) else None


def _moodboard(store: ProjectStore, project_id: UUID, node_id: UUID) -> BoardItem:
    board = board_now(store.load(project_id), store)
    node = next((i for i in board.items if i.id == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="node inconnu dans ce projet")
    if bl.card_role(node) != "inspirations":
        raise HTTPException(status_code=422, detail="ce node n'est pas un Moodboard")
    return node


def _placed(frame: BoardItem, inside: int, ref: str, text: str, url: str | None) -> list[BoardItem]:
    """La carte de l'image à la case suivante du Moodboard (2 colonnes, comme la
    synchronisation), et le Moodboard grandi d'une rangée s'il est plein."""
    row, col = divmod(inside, b.COLS)
    cell_w, cell_h = b.cell_size(frame)
    top = b.PAD_TOP if frame.role else b.PAD
    item = BoardItem(
        kind="file",
        x=frame.x + b.PAD + col * (cell_w + 16.0),
        y=frame.y + top + row * (cell_h + 16.0),
        w=cell_w,
        h=cell_h,
        z=1,
        frame=frame.id,
        ref=ref,
        text=text,
        reference=True,
        url=url,
    )
    need = top + b.PAD + (row + 1) * cell_h + row * 16.0
    return [item, frame.model_copy(update={"h": need})] if frame.h < need else [item]


@router.post("/api/extension/projects/{project_id}/images", status_code=201)
def extension_image(project_id: UUID, body: ImageIn, request: Request) -> ImageAdded:
    """L'image d'une page web, récupérée par le serveur (KAN-98 L3) et posée dans le node
    Moodboard choisi. Rôle éditeur et place du propriétaire : vérifiés par la garde."""
    user = require_extension(request)
    actor = f"user:{user.id}"
    if not link_limiter(request).allow(author_of(actor)):
        raise HTTPException(
            status_code=429, detail="Trop d'images ajoutées d'un coup : réessayez dans une minute."
        )
    store: ProjectStore = request.app.state.store
    _moodboard(store, project_id, body.node_id)  # avant toute requête vers le site
    try:
        got = link_fetch.fetch_link_image(body.image_url)
        media_type = b.checked_link_image(got.data)
    except link_fetch.LinkError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=f"Image refusée : {exc}.") from exc
    page = _web(body.page_url)
    project_dir: Path = store.project_dir(project_id)
    with store.lock(project_id):
        before = b.load_board(project_dir)
        frame = next((i for i in (before.items if before else []) if i.id == body.node_id), None)
        if frame is None:  # retiré entre-temps
            raise HTTPException(status_code=404, detail="node inconnu dans ce projet")
        ref = b.add_media(
            project_dir,
            got.data,
            link_name(got.image_url, media_type),
            placed=True,
            source_url=page or got.page_url or got.image_url,
        )
        inside = sum(1 for i in before.items if i.frame == frame.id) if before else 0
        written = _placed(frame, inside, str(ref.id), ref.name, _web(got.image_url))
        saved = b.save_board(project_dir, b.patched(before, BoardPatch(set=written), actor))
        journaled = record_board(project_dir, project_id, author_of(actor), before, saved)
    _tell_members(request, project_id, str(user.id), journaled=journaled)
    return ImageAdded(
        project_id=project_id,
        node_id=frame.id,
        media_id=ref.id,
        item_id=written[0].id,
        name=ref.name,
    )


def _tell_members(request: Request, project_id: UUID, me: str, *, journaled: bool) -> None:
    """Les onglets ouverts sur le projet relisent l'espace de travail (B2a) ; la cloche des
    autres membres sonne si le geste est entré au journal (KAN-52), comme un collage."""
    from openplan.api.accounts.live import hub_of, ring_project

    app = request.app
    from_thread.run(hub_of(app).publish, project_id, {"type": "changed", "scope": "board"})
    if journaled:
        from_thread.run(ring_project, app, project_id, me)
