"""Connexion par lien envoyé par e-mail et sessions (note 17, § 2 et § 3, avenant du 16 oct.).

- Lien : 32 octets aléatoires, usage unique, 15 minutes, gardé en empreinte SHA-256 ; la
  réponse à la demande est la même que l'adresse ait un compte ou non ; 3 liens par adresse
  par 15 min et 10 demandes par IP par heure, au-delà rien ne part (même réponse).
- Le lien ouvre une page « Se connecter » : c'est son bouton (POST) qui consomme le jeton, pas
  l'ouverture du lien (les aperçus de messagerie suivent les liens).
- Compte : retrouvé par l'adresse normalisée ; créé seulement si l'inscription est ouverte
  (`OPENPLAN_SIGNUP=open`), pour l'adresse administrateur, ou (B1e) sur invitation.
- Session : 32 octets aléatoires en cookie `pd_session`, empreinte en base, 30 jours
  prolongés à l'usage (au plus une fois par heure), révocable.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Engine, and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openplan.api.access import SESSION_MAX_AGE_S, AuthUser, ResolvedSession
from openplan.api.accounts.db import aware, utcnow
from openplan.api.accounts.mail import Mailer, MailMessage, login_message
from openplan.api.accounts.models import AuthSession, LoginToken, User
from openplan.api.accounts.repo import AccountsRepo, normalize_email
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.settings import Settings

TOKEN_BYTES = 32
LOGIN_TTL = timedelta(minutes=15)
ADDRESS_LIMIT = 3
"""Liens par adresse sur la durée de vie d'un lien (15 min)."""
IP_LIMIT = 10
IP_WINDOW_S = 3600.0
TOKEN_PURGE_AFTER = timedelta(hours=24)
SESSION_TTL = timedelta(seconds=SESSION_MAX_AGE_S)
SESSION_RENEW_EVERY = timedelta(hours=1)
CALLBACK_PATH = "/api/auth/email/callback"
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s.][^@\s]*\.[^@\s]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f\\]")
MAX_NEXT = 512


class InvalidEmailError(ValueError):
    """L'adresse n'a pas la forme d'une adresse e-mail."""


class LoginRefusal(StrEnum):
    INVALID = "invalid"
    EXPIRED = "expired"
    USED = "used"
    CLOSED = "closed"


REFUSAL_TEXT = {
    LoginRefusal.INVALID: "ce lien de connexion n'est pas valable.",
    LoginRefusal.EXPIRED: "ce lien de connexion a expiré (il vaut 15 minutes).",
    LoginRefusal.USED: "ce lien de connexion a déjà servi (il ne sert qu'une fois).",
    LoginRefusal.CLOSED: "PlanDesign est pour l'instant sur invitation.",
}


class LoginRefusedError(Exception):
    def __init__(self, reason: LoginRefusal) -> None:
        super().__init__(reason.value)
        self.reason = reason


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def checked_email(email: str) -> str:
    value = normalize_email(email)
    if len(value) > 254 or not _EMAIL.match(value):
        msg = "adresse e-mail invalide"
        raise InvalidEmailError(msg)
    return value


def safe_next(value: str | None) -> str:
    """Un chemin relatif de l'application seulement (pas de redirection ouverte)."""
    if (
        not value
        or len(value) > MAX_NEXT
        or not value.startswith("/")
        or value.startswith("//")
        or _CONTROL.search(value)
    ):
        return "/"
    return value


def short_ip(ip: str | None) -> str | None:
    """IP tronquée pour la base : /24 en IPv4, /48 en IPv6."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False))


def _auth_user(user: User) -> AuthUser:
    return AuthUser(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin)


class AccountService(AccountsRepo):
    """Comptes, liens de connexion et sessions ; aussi le `AccountsBackend` de l'API."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        mailer: Mailer,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        super().__init__(engine)
        self.settings = settings
        self.mailer = mailer
        self.clock = clock
        self.ip_limiter = SlidingWindowLimiter(IP_LIMIT, IP_WINDOW_S)
        self.admin_email = normalize_email(settings.admin_email) if settings.admin_email else None

    # --- demande d'un lien ------------------------------------------------------------

    def _may_sign_in(self, db: Session, email: str) -> bool:
        if email == self.admin_email or self.settings.signup == "open":
            return True
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            return True
        return self.pending_invitation(email)

    def pending_invitation(self, email: str) -> bool:
        """Invitation en attente pour cette adresse : arrive avec B1e."""
        del email
        return False

    def start_login(self, email: str, next_path: str | None, ip: str | None) -> MailMessage | None:
        """Le message à envoyer, ou None quand rien ne doit partir (adresse qui ne peut pas se
        connecter, limite atteinte). L'appelant répond pareil dans les deux cas."""
        address = checked_email(email)
        if not self.ip_limiter.allow(ip or "-"):
            return None
        now = self.clock()
        raw = new_token()
        with self.session() as db, db.begin():
            db.execute(delete(LoginToken).where(LoginToken.created_at < now - TOKEN_PURGE_AFTER))
            if not self._may_sign_in(db, address):
                return None
            recent = db.scalar(
                select(func.count())
                .select_from(LoginToken)
                .where(LoginToken.email == address, LoginToken.created_at > now - LOGIN_TTL)
            )
            if (recent or 0) >= ADDRESS_LIMIT:
                return None
            db.add(
                LoginToken(
                    email=address,
                    token_hash=hash_token(raw),
                    next=safe_next(next_path),
                    created_at=now,
                    expires_at=now + LOGIN_TTL,
                    ip=short_ip(ip),
                )
            )
        link = f"{self.settings.public_url}{CALLBACK_PATH}?token={raw}"
        return login_message(address, link, int(LOGIN_TTL.total_seconds() // 60))

    # --- lien ouvert, puis bouton « Se connecter » ------------------------------------

    def _token_row(self, db: Session, raw: str) -> LoginToken:
        digest = hash_token(raw)
        row = db.scalar(select(LoginToken).where(LoginToken.token_hash == digest))
        if row is None or not hmac.compare_digest(row.token_hash, digest):
            raise LoginRefusedError(LoginRefusal.INVALID)
        if row.used_at is not None or row.revoked_at is not None:
            raise LoginRefusedError(LoginRefusal.USED)
        if aware(row.expires_at) <= self.clock():
            raise LoginRefusedError(LoginRefusal.EXPIRED)
        return row

    def check_login(self, raw: str) -> None:
        """Ouverture du lien (GET) : dit s'il est encore bon, **sans** le consommer."""
        with self.session() as db:
            self._token_row(db, raw)

    def complete_login(self, raw: str, user_agent: str = "") -> tuple[AuthUser, str, str]:
        """Bouton « Se connecter » (POST) : consomme le jeton (marqué utilisé avant tout),
        trouve ou crée le compte, ouvre une session. Renvoie (utilisateur, session, next)."""
        now = self.clock()
        with self.session() as db, db.begin():
            row = self._token_row(db, raw)
            done = db.execute(
                update(LoginToken)
                .where(and_(LoginToken.id == row.id, LoginToken.used_at.is_(None)))
                .values(used_at=now)
            )
            if getattr(done, "rowcount", 0) != 1:  # deux clics simultanés : un seul passe
                raise LoginRefusedError(LoginRefusal.USED)
            email, next_path = row.email, row.next
        user = self._user_for_login(email, now)
        session_raw = new_token()
        with self.session() as db, db.begin():
            db.add(
                AuthSession(
                    user_id=user.id,
                    token_hash=hash_token(session_raw),
                    created_at=now,
                    last_used_at=now,
                    expires_at=now + SESSION_TTL,
                    user_agent=user_agent[:200],
                )
            )
        return user, session_raw, safe_next(next_path)

    def _user_for_login(self, email: str, now: datetime) -> AuthUser:
        is_admin_address = email == self.admin_email
        for _ in range(2):  # une création concurrente de la même adresse : on relit
            try:
                with self.session() as db, db.begin():
                    user = db.scalar(select(User).where(User.email == email))
                    if user is None:
                        if not (
                            is_admin_address
                            or self.settings.signup == "open"
                            or self.pending_invitation(email)
                        ):
                            raise LoginRefusedError(LoginRefusal.CLOSED)
                        user = User(email=email, name="", is_admin=False, created_at=now)
                        db.add(user)
                    if is_admin_address and not user.is_admin:
                        user.is_admin = True
                    user.last_seen_at = now
                    db.flush()
                    return _auth_user(user)
            except IntegrityError:
                continue
        raise LoginRefusedError(LoginRefusal.INVALID)  # pragma: no cover - course improbable

    # --- sessions ---------------------------------------------------------------------

    def resolve_session(self, raw: str) -> ResolvedSession | None:
        if not raw or len(raw) > 128:
            return None
        now = self.clock()
        with self.session() as db, db.begin():
            row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(raw)))
            if row is None or row.revoked_at is not None or aware(row.expires_at) <= now:
                return None
            user = db.get(User, row.user_id)
            if user is None:
                return None
            renewed = now - aware(row.last_used_at) >= SESSION_RENEW_EVERY
            if renewed:
                row.last_used_at = now
                row.expires_at = now + SESSION_TTL
                user.last_seen_at = now
            return ResolvedSession(user=_auth_user(user), renewed=renewed)

    def revoke_session(self, raw: str) -> bool:
        with self.session() as db, db.begin():
            done = db.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == hash_token(raw), AuthSession.revoked_at.is_(None))
                .values(revoked_at=self.clock())
            )
            return bool(getattr(done, "rowcount", 0))

    def revoke_all(self, user_id: UUID) -> int:
        """« Me déconnecter partout », ou un administrateur pour un autre utilisateur."""
        with self.session() as db, db.begin():
            done = db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=self.clock())
            )
            return int(getattr(done, "rowcount", 0))
