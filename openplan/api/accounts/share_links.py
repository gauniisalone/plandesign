"""KAN-101 : partager UN rendu réaliste par un lien public, en lecture seule et révocable.

- Créer, lister, révoquer : éditeur du projet et plus (`guards.PROJECT_ROUTES`) ; créer exige
  en plus que le rendu soit un rendu fait de la personne (comme « Nouvelle variante »).
- Le jeton : `secrets.token_urlsafe(32)` (256 bits), montré une seule fois à la création ;
  la base n'en garde que l'empreinte SHA-256, comparée en temps constant.
- Expiration : 30 jours par défaut. Révoqué, expiré, inconnu ou réglage éteint : la même 404.
- La page publique (`/api/share/<jeton>`, politique `public`) ne montre que l'image (métadonnées
  retirées : image recodée) et ce qui a guidé le rendu ; jamais le plan, les photos de la
  pièce, le Moodboard ni une donnée personnelle. Débit limité par adresse ; le journal ne
  garde jamais l'adresse entière (tronquée, `short_ip`), ni le jeton (`share_page.logged_form`).
- Tout est derrière OPENPLAN_SHARE_LINKS, éteint par défaut : les routes existent (audit des
  droits) mais répondent 404.
- Le lien tombe avec son rendu, son projet ou le compte qui l'a créé (clés étrangères en
  cascade) ; `openplan accounts forget` les compte.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import logging
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Final, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from PIL import Image
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from openplan.api.accounts.db import aware, utcnow
from openplan.api.accounts.models import ShareLink
from openplan.api.accounts.render_queue import public_request
from openplan.api.accounts.render_routes import (
    _own_done,
    allowed_user,
    image_path,
    queue_of,
    store_of,
)
from openplan.api.accounts.render_style import with_guidance
from openplan.api.accounts.service import short_ip
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.settings import Settings
from openplan.api.share_page import (
    NOT_FOUND_TEXT,
    SHARE_HEADERS,
    SHARE_PREFIX,
    TOKEN_PATTERN,
    TOO_MANY_TEXT,
    message_page,
    render_page,
)
from openplan.scene.store import ProjectNotFoundError
from openplan.schemas.common import DECODED_IMAGE_FORMATS, StrictModel
from openplan.schemas.render_queue import RealisticRequest

log = logging.getLogger("openplan.share")
router = APIRouter(tags=["liens publics"])

P = "/api/projects/{project_id}"
TOKEN_BYTES = 32
DEFAULT_DAYS: Final = 30
SHARE_RATE_LIMIT = 30
SHARE_RATE_WINDOW_S = 60.0
"""Une vue = deux requêtes (page, image) : 15 vues par minute et par adresse."""
UNKNOWN_LINK = "lien inconnu"


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ShareLinks:
    """Les liens en base ; le limiteur de débit de la page publique (réglable par les tests)."""

    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow) -> None:
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.clock = clock
        self.limiter = SlidingWindowLimiter(SHARE_RATE_LIMIT, SHARE_RATE_WINDOW_S)

    def session(self) -> Session:
        return self.sessions()

    def create(
        self,
        project_id: UUID,
        request_id: UUID,
        user_id: UUID,
        *,
        days: int = DEFAULT_DAYS,
        show_project_name: bool = True,
    ) -> tuple[ShareLink, str]:
        """Le lien et son jeton en clair (rendu une seule fois, jamais gardé)."""
        raw = secrets.token_urlsafe(TOKEN_BYTES)
        now = self.clock()
        row = ShareLink(
            token_hash=hash_token(raw),
            project_id=project_id,
            request_id=request_id,
            created_by=user_id,
            created_at=now,
            expires_at=now + timedelta(days=days),
            show_project_name=show_project_name,
        )
        with self.session() as db, db.begin():
            db.add(row)
        return row, raw

    def active(self, project_id: UUID) -> list[ShareLink]:
        """Les liens actifs du projet (ni révoqués, ni expirés), les plus récents d'abord."""
        now = self.clock()
        with self.session() as db:
            rows = db.scalars(
                select(ShareLink)
                .where(ShareLink.project_id == project_id, ShareLink.revoked_at.is_(None))
                .order_by(ShareLink.created_at.desc())
            )
            return [r for r in rows if aware(r.expires_at) > now]

    def revoke(self, project_id: UUID, link_id: UUID) -> bool:
        with self.session() as db, db.begin():
            row = db.get(ShareLink, link_id)
            if row is None or row.project_id != project_id or row.revoked_at is not None:
                return False
            row.revoked_at = self.clock()
            return True

    def resolve(self, raw: str) -> ShareLink | None:
        """Le lien actif de ce jeton, sinon None (inconnu, révoqué ou expiré : rien ne les
        distingue). La ligne est cherchée par l'empreinte, puis l'empreinte est comparée en
        temps constant."""
        if not TOKEN_PATTERN.match(raw):
            return None
        digest = hash_token(raw)
        with self.session() as db:
            row = db.scalar(select(ShareLink).where(ShareLink.token_hash == digest))
        if row is None or not hmac.compare_digest(row.token_hash, digest):
            return None
        if row.revoked_at is not None or aware(row.expires_at) <= self.clock():
            return None
        return row


def links_of(request: Request) -> ShareLinks:
    links: ShareLinks = request.app.state.share_links
    return links


def enabled(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    return settings.share_links


def _require_enabled(request: Request) -> None:
    if not enabled(request):
        raise HTTPException(status_code=404, detail="Not Found")


# --- côté membres du projet --------------------------------------------------------------


class ShareLinkCreate(StrictModel):
    show_project_name: bool = True
    """« Afficher le nom du projet » sur la page publique."""
    days: Literal[1, 7, 30] = DEFAULT_DAYS


class ShareLinkOut(StrictModel):
    id: UUID
    request_id: UUID
    created_at: datetime
    expires_at: datetime
    show_project_name: bool


class ShareLinkCreated(ShareLinkOut):
    token: str
    """Montré une seule fois : le front en fait `<origine>/api/share/<jeton>`."""


def _out(row: ShareLink) -> ShareLinkOut:
    return ShareLinkOut(
        id=row.id,
        request_id=row.request_id,
        created_at=aware(row.created_at),
        expires_at=aware(row.expires_at),
        show_project_name=row.show_project_name,
    )


@router.post(P + "/realistic/{request_id}/share-links", status_code=201)
def create_share_link(
    project_id: UUID, request_id: UUID, body: ShareLinkCreate, request: Request
) -> ShareLinkCreated:
    """« Partager » sur un rendu fait de la personne : un lien public vers CE rendu seul."""
    _require_enabled(request)
    user = allowed_user(request)
    _own_done(request, user, project_id, request_id)
    row, raw = links_of(request).create(
        project_id,
        request_id,
        user.id,
        days=body.days,
        show_project_name=body.show_project_name,
    )
    return ShareLinkCreated(**_out(row).model_dump(), token=raw)


@router.get(P + "/share-links")
def list_share_links(project_id: UUID, request: Request) -> list[ShareLinkOut]:
    """Les liens actifs du projet, sans leur jeton."""
    _require_enabled(request)
    return [_out(r) for r in links_of(request).active(project_id)]


@router.delete(P + "/share-links/{link_id}", status_code=204)
def revoke_share_link(project_id: UUID, link_id: UUID, request: Request) -> Response:
    """Révoquer d'un clic : la page publique répond 404 aussitôt."""
    _require_enabled(request)
    if not links_of(request).revoke(project_id, link_id):
        raise HTTPException(status_code=404, detail="lien inconnu ou déjà révoqué")
    return Response(status_code=204)


# --- côté public (sans compte) -----------------------------------------------------------


class _GoneError(Exception):
    """Lien inconnu, révoqué, expiré, rendu ou projet disparu, réglage éteint."""


def _public_response(content: str | bytes, status: int, media_type: str) -> Response:
    if isinstance(content, str):
        return HTMLResponse(content, status_code=status, headers=SHARE_HEADERS)
    return Response(content, status_code=status, media_type=media_type, headers=SHARE_HEADERS)


def _not_found() -> Response:
    return _public_response(message_page(NOT_FOUND_TEXT), 404, "text/html")


def _shared(request: Request, token: str) -> tuple[ShareLink, RealisticRequest]:
    """Le lien actif et son rendu fait, avec l'image sur le disque ; `_GoneError` sinon."""
    if not enabled(request):
        raise _GoneError
    link = links_of(request).resolve(token)
    if link is None:
        raise _GoneError
    row = queue_of(request).get(link.request_id)
    store = store_of(request)
    if (
        row is None
        or row.project_id != link.project_id
        or row.state != "done"
        or not image_path(store, link.project_id, link.request_id).is_file()
    ):
        raise _GoneError
    return link, with_guidance(store, link.project_id, public_request(row, True))


def _throttled(request: Request) -> Response | None:
    """Débit par adresse ; le refus est journalisé avec l'adresse TRONQUÉE seulement."""
    client = request.client.host if request.client else "-"
    if links_of(request).limiter.allow(client):
        return None
    log.warning("lien public : débit dépassé (%s)", short_ip(client) or "-")
    refused = _public_response(message_page(TOO_MANY_TEXT), 429, "text/html")
    refused.headers["Retry-After"] = str(int(SHARE_RATE_WINDOW_S))
    return refused


def guided_text(render: RealisticRequest) -> str | None:
    """« Guidé par » sur la page publique : les mots de style (vocabulaire fermé) ; jamais
    les noms des images du Moodboard, seulement leur nombre."""
    names = render.guided_by
    if names is None:
        return None
    if not names:
        return "Sans inspiration"
    if render.guided_kind == "words":
        return f"Guidé par : {', '.join(names)}"
    count = len(names)
    return f"Guidé par : {count} image{'s' if count > 1 else ''} d'inspiration"


@router.get(SHARE_PREFIX + "{token}", response_model=None)
def shared_page(token: str, request: Request) -> Response:
    """La page publique : l'image, ce qui l'a guidée, le nom PlanDesign ; le nom du projet si
    l'auteur l'a laissé coché."""
    if not enabled(request):
        return _not_found()
    refused = _throttled(request)
    if refused is not None:
        return refused
    try:
        link, render = _shared(request, token)
    except _GoneError:
        return _not_found()
    name = None
    if link.show_project_name:
        try:
            name = store_of(request).load(link.project_id).name or None
        except ProjectNotFoundError:
            return _not_found()
    page = render_page(f"{SHARE_PREFIX}{token}/image", guided_text(render), name)
    return _public_response(page, 200, "text/html")


def clean_png(raw: Image.Image) -> bytes:
    """L'image recodée depuis ses seuls pixels : aucune métadonnée (EXIF, XMP, texte PNG,
    profil, date) ne passe."""
    rgb = raw.convert("RGB")
    fresh = Image.frombytes("RGB", rgb.size, rgb.tobytes())
    out = io.BytesIO()
    fresh.save(out, format="PNG")
    return out.getvalue()


@router.get(SHARE_PREFIX + "{token}/image", response_model=None)
def shared_image(token: str, request: Request) -> Response:
    """L'image du rendu partagé, sans métadonnées."""
    if not enabled(request):
        return _not_found()
    refused = _throttled(request)
    if refused is not None:
        return refused
    try:
        link, _render = _shared(request, token)
        path = image_path(store_of(request), link.project_id, link.request_id)
        with Image.open(path, formats=DECODED_IMAGE_FORMATS) as img:
            data = clean_png(img)
    except (_GoneError, OSError, ValueError):
        return _not_found()
    return _public_response(data, 200, "image/png")
