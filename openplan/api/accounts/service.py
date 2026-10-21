"""Connexion par code envoyé par e-mail et sessions (note 17, § 2 et § 3 ; avenants du
16 oct. et B1b2 du 18 oct.).

- Code : 6 chiffres tirés par `secrets`, usage unique, 15 minutes, gardé en empreinte SHA-256
  salée par l'identifiant (aléatoire) de la ligne et l'adresse ; comparé en temps constant ;
  5 essais faux au plus par code, puis il est révoqué. Plus de lien : Brevo peut réécrire et
  journaliser les liens d'un e-mail, un code tapé dans la fenêtre « Se connecter » ne voyage
  dans aucune URL.
- La réponse à la demande est la même que l'adresse ait un compte ou non ; 3 codes par adresse
  par 15 min et 10 demandes par IP par heure, au-delà rien ne part (même réponse) ; la
  vérification d'un code répond pareil qu'il soit faux, expiré, épuisé ou déjà servi.
- Compte : retrouvé par l'adresse normalisée ; créé seulement si l'inscription est ouverte
  (`OPENPLAN_SIGNUP=open`), pour l'adresse administrateur, ou (B1e) avec le jeton d'un lien
  d'invitation encore valable, présenté à la demande et à la vérification du code, ou
  (KAN-55) pour une adresse invitée nommément à un projet.
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
from dataclasses import replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from openplan.api.access import SESSION_MAX_AGE_S, AuthUser, ResolvedSession
from openplan.api.accounts.db import aware, utcnow
from openplan.api.accounts.mail import Mailer, MailMessage, login_message
from openplan.api.accounts.models import AuthSession, LoginToken, User
from openplan.api.accounts.repo import AccountsRepo, normalize_email
from openplan.api.accounts.sharing import SharingMixin
from openplan.api.ratelimit import SlidingWindowLimiter
from openplan.api.settings import Settings

TOKEN_BYTES = 32
"""Sessions : 32 octets aléatoires."""
CODE_DIGITS = 6
LOGIN_TTL = timedelta(minutes=15)
MAX_ATTEMPTS = 5
"""Essais faux par code ; au-delà, le code est révoqué."""
ADDRESS_LIMIT = 3
"""Codes par adresse sur la durée de vie d'un code (15 min)."""
IP_LIMIT = 10
IP_WINDOW_S = 3600.0
TOKEN_PURGE_AFTER = timedelta(hours=24)
SESSION_TTL = timedelta(seconds=SESSION_MAX_AGE_S)
SESSION_RENEW_EVERY = timedelta(hours=1)
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
    # Un seul texte pour un code faux, expiré, épuisé ou déjà servi : rien n'est trahi.
    LoginRefusal.INVALID: "ce code n'est pas bon, ou il a expiré.",
    LoginRefusal.EXPIRED: "ce code n'est pas bon, ou il a expiré.",
    LoginRefusal.USED: "ce code n'est pas bon, ou il a expiré.",
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


def new_code() -> str:
    """6 chiffres, zéros de tête compris (000000 à 999999), tirés par `secrets`."""
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(row_id: UUID, email: str, code: str) -> str:
    """Empreinte d'un code : salée par l'identifiant aléatoire de sa ligne et par l'adresse ;
    deux codes égaux n'ont jamais la même empreinte."""
    return hashlib.sha256(f"{row_id}:{email}:{code}".encode()).hexdigest()


def clean_code(value: str) -> str | None:
    """Le code tapé, sans espaces ; None s'il n'a pas la forme de 6 chiffres."""
    code = re.sub(r"\s+", "", value or "")
    return code if re.fullmatch(rf"\d{{{CODE_DIGITS}}}", code) else None


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


class AccountService(AccountsRepo, SharingMixin):
    """Comptes, codes de connexion, sessions et partage (B1e) ; aussi le
    `AccountsBackend` de l'API."""

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

    def _may_sign_in(self, db: Session, email: str, invitation: str | None) -> bool:
        if email == self.admin_email or self.settings.signup == "open":
            return True
        if db.scalar(select(User.id).where(User.email == email)) is not None:
            return True
        if self._live_for_email(db, email):  # KAN-55 : adresse invitée nommément
            return True
        return invitation is not None and self._live_invitation(db, invitation) is not None

    def start_login(
        self,
        email: str,
        next_path: str | None,
        ip: str | None,
        invitation: str | None = None,
    ) -> MailMessage | None:
        """Le message à envoyer (le code), ou None quand rien ne doit partir (adresse qui ne
        peut pas se connecter, limite atteinte). L'appelant répond pareil dans les deux cas.
        `invitation` : le jeton d'un lien d'invitation (B1e), qui ouvre l'inscription."""
        address = checked_email(email)
        if not self.ip_limiter.allow(ip or "-"):
            return None
        now = self.clock()
        code = new_code()
        row_id = uuid4()
        with self.session() as db, db.begin():
            db.execute(delete(LoginToken).where(LoginToken.created_at < now - TOKEN_PURGE_AFTER))
            if not self._may_sign_in(db, address, invitation):
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
                    id=row_id,
                    email=address,
                    token_hash=hash_code(row_id, address, code),
                    next=safe_next(next_path),
                    created_at=now,
                    expires_at=now + LOGIN_TTL,
                    ip=short_ip(ip),
                    attempts=0,
                )
            )
        return login_message(address, code, int(LOGIN_TTL.total_seconds() // 60))

    # --- code tapé dans la fenêtre « Se connecter » -----------------------------------

    def complete_login(
        self, email: str, typed: str, user_agent: str = "", invitation: str | None = None
    ) -> tuple[AuthUser, str, str]:
        """Vérifie le code de cette adresse (tous ses codes encore valables, comparés en temps
        constant) ; juste : le consomme (marqué utilisé avant tout), trouve ou crée le compte,
        ouvre une session ; faux : un essai de plus sur chacun, révoqué au 5e. Renvoie
        (utilisateur, session, next) ; refus : toujours la même raison (INVALID)."""
        now = self.clock()
        try:
            address = checked_email(email)
        except InvalidEmailError:
            raise LoginRefusedError(LoginRefusal.INVALID) from None
        code = clean_code(typed)
        refused = False
        next_path = "/"
        with self.session() as db, db.begin():
            rows = list(
                db.scalars(
                    select(LoginToken).where(
                        LoginToken.email == address,
                        LoginToken.used_at.is_(None),
                        LoginToken.revoked_at.is_(None),
                        LoginToken.attempts < MAX_ATTEMPTS,
                    )
                )
            )
            live = [r for r in rows if aware(r.expires_at) > now]
            matched: LoginToken | None = None
            for row in live:  # chaque empreinte est calculée et comparée, juste ou non
                digest = hash_code(row.id, address, code or "")
                if hmac.compare_digest(row.token_hash, digest) and code is not None:
                    matched = row
            if matched is None:
                # Les essais sont comptés et enregistrés (transaction validée) ; le refus est
                # levé hors de la transaction, qui sinon serait annulée.
                for row in live:
                    row.attempts += 1
                    if row.attempts >= MAX_ATTEMPTS:
                        row.revoked_at = now
                refused = True
            else:
                done = db.execute(
                    update(LoginToken)
                    .where(
                        and_(
                            LoginToken.id == matched.id,
                            LoginToken.used_at.is_(None),
                            LoginToken.revoked_at.is_(None),
                        )
                    )
                    .values(used_at=now)
                )
                # Deux envois simultanés du même code : un seul passe.
                refused = getattr(done, "rowcount", 0) != 1
                next_path = matched.next
        if refused:
            raise LoginRefusedError(LoginRefusal.INVALID)
        user = self._user_for_login(address, now, invitation)
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

    def _user_for_login(self, email: str, now: datetime, invitation: str | None) -> AuthUser:
        is_admin_address = email == self.admin_email
        for _ in range(2):  # une création concurrente de la même adresse : on relit
            try:
                with self.session() as db, db.begin():
                    user = db.scalar(select(User).where(User.email == email))
                    if user is None:
                        if not (
                            is_admin_address
                            or self.settings.signup == "open"
                            or self.valid_invitation(invitation)
                            or self.has_email_invitation(email)
                        ):
                            raise LoginRefusedError(LoginRefusal.CLOSED)
                        user = User(email=email, name="", is_admin=False, created_at=now)
                        db.add(user)
                        created = True
                    else:
                        created = False
                    if is_admin_address and not user.is_admin:
                        user.is_admin = True
                    user.last_seen_at = now
                    db.flush()
                    return replace(_auth_user(user), first_login=created)
            except IntegrityError:
                continue
        raise LoginRefusedError(LoginRefusal.INVALID)  # pragma: no cover - course improbable

    def set_name(self, user_id: UUID, name: str) -> None:
        with self.session() as db, db.begin():
            user = db.get(User, user_id)
            if user is not None:
                user.name = name

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
