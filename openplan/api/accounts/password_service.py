"""KAN-104 L1 : connexion par mot de passe, oubli (code `reset` de 30 min), changement, mot
de passe posé sur un compte existant (preuve récente par code `reauth`) et sessions.

Derrière OPENPLAN_PASSWORD_LOGIN (faux par défaut) : les routes répondent 404 tant qu'il
n'est pas posé. Le code par e-mail reste toujours possible (D2) : un délai sur le mot de passe
ne bloque jamais le titulaire.

- Refus uniforme : adresse inconnue, compte sans mot de passe, compte administrateur (D3 : code
  seulement), délai en cours ou mot de passe faux donnent la même réponse, après le même calcul
  argon2 (empreinte factice au besoin).
- Délai progressif par compte : à partir du 5e échec d'affilée, 2^(n−5) s, au plus 15 min.
- Par IP : 30 échecs par heure, puis 429 (en plus des 10 requêtes/min sur `/api/auth/*`).
- Poser, changer ou réinitialiser le mot de passe ferme TOUTES les sessions et en ouvre une
  neuve (jeton neuf) ; le titulaire est prévenu par e-mail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update

from openplan.api.access import AuthUser
from openplan.api.accounts.db import aware
from openplan.api.accounts.mail import (
    MailMessage,
    password_changed_message,
    reauth_message,
    reset_message,
)
from openplan.api.accounts.models import AuthSession, User
from openplan.api.accounts.passwords import (
    check_policy,
    hash_password,
    is_pwned,
    normalized,
    verify_password,
)
from openplan.api.ratelimit import SlidingWindowLimiter

if TYPE_CHECKING:
    from openplan.api.accounts.service import AccountService

RESET_TTL = timedelta(minutes=30)
REAUTH_TTL = timedelta(minutes=15)
REAUTH_FRESH = timedelta(minutes=10)
"""Une preuve récente (code `reauth`) vaut 10 min pour poser un mot de passe."""
LOCK_AFTER = 5
LOCK_MAX = timedelta(minutes=15)
IP_FAILURES = 30
IP_FAILURES_WINDOW_S = 3600.0


class PasswordRefusedError(Exception):
    """Adresse ou mot de passe incorrect (même réponse dans tous les cas)."""


class PasswordThrottledError(Exception):
    """Trop d'échecs depuis cette IP (429)."""


class PasswordForbiddenError(Exception):
    """Refus d'une action d'un compte connecté ; le texte est montré tel quel."""


class ReauthRequiredError(Exception):
    """Un compte sans mot de passe doit d'abord confirmer par un code `reauth`."""


@dataclass(frozen=True)
class SessionInfo:
    id: UUID
    created_at: datetime
    last_used_at: datetime
    user_agent: str
    auth_method: str
    current: bool


def lock_delay(failures: int) -> timedelta | None:
    """Le délai après `failures` échecs d'affilée : rien avant le 5e, puis 1 s, 2 s, 4 s…"""
    if failures < LOCK_AFTER:
        return None
    return min(timedelta(seconds=2 ** min(failures - LOCK_AFTER, 20)), LOCK_MAX)


class PasswordFlows:
    """Rattaché à `AccountService` (`service.passwords`)."""

    def __init__(self, accounts: AccountService) -> None:
        self.accounts = accounts
        self.failures = SlidingWindowLimiter(IP_FAILURES, IP_FAILURES_WINDOW_S)
        self.pwned: Callable[[str], bool | None] = is_pwned
        """Remplacé dans les tests (HIBP simulé) : jamais d'appel réseau en test."""

    def _now(self) -> datetime:
        return self.accounts.clock()

    def _is_admin(self, user: User) -> bool:
        return user.is_admin or user.email == self.accounts.admin_email

    def _user(self, email: str) -> User | None:
        from openplan.api.accounts.service import InvalidEmailError, checked_email

        try:
            address = checked_email(email)
        except InvalidEmailError:
            return None
        with self.accounts.session() as db:
            return db.scalar(select(User).where(User.email == address))

    # --- connexion --------------------------------------------------------------------

    def login(
        self, email: str, password: str, user_agent: str, ip: str | None
    ) -> tuple[AuthUser, str]:
        """(utilisateur, session) ; sinon PasswordRefusedError, toujours la même."""
        from openplan.api.accounts.service import _auth_user

        ip_key = ip or "-"
        if self.failures.full(ip_key):
            raise PasswordThrottledError
        now = self._now()
        user = self._user(email)
        usable = user is not None and user.password_hash is not None and not self._is_admin(user)
        locked = (
            usable
            and user is not None
            and user.locked_until is not None
            and aware(user.locked_until) > now
        )
        stored = user.password_hash if user is not None and usable and not locked else None
        ok, rehash = verify_password(stored, password)
        if not ok or user is None:
            self.failures.allow(ip_key)
            if usable and user is not None:
                self._count_failure(user.id, now)
            raise PasswordRefusedError
        with self.accounts.session() as db, db.begin():
            row = db.get(User, user.id)
            if row is None:  # pragma: no cover - compte supprimé entre-temps
                raise PasswordRefusedError
            row.failed_logins = 0
            row.locked_until = None
            row.last_seen_at = now
            if rehash:
                row.password_hash = hash_password(normalized(password))
            found = _auth_user(row)
        return found, self.accounts._open_session(found.id, user_agent, "password", now)

    def _count_failure(self, user_id: UUID, now: datetime) -> None:
        with self.accounts.session() as db, db.begin():
            row = db.get(User, user_id)
            if row is None:  # pragma: no cover
                return
            row.failed_logins = (row.failed_logins or 0) + 1
            delay = lock_delay(row.failed_logins)
            if delay is not None:
                row.locked_until = now + delay

    # --- oubli ------------------------------------------------------------------------

    def start_reset(self, email: str, ip: str | None) -> MailMessage | None:
        """Le code `reset` à envoyer, ou None (adresse inconnue, administrateur, limite) :
        l'appelant répond pareil. InvalidEmailError si l'adresse n'en a pas la forme."""
        from openplan.api.accounts.service import checked_email

        address = checked_email(email)
        if not self.accounts.ip_limiter.allow(ip or "-"):
            return None
        now = self._now()
        with self.accounts.session() as db, db.begin():
            user = db.scalar(select(User).where(User.email == address))
            if user is None or self._is_admin(user):
                return None
            code = self.accounts._issue_code(db, address, "reset", RESET_TTL, now, ip)
        if code is None:
            return None
        return reset_message(address, code, int(RESET_TTL.total_seconds() // 60))

    def complete_reset(
        self, email: str, typed: str, password: str, user_agent: str
    ) -> tuple[AuthUser, str, MailMessage]:
        """Code `reset` juste : nouveau mot de passe, toutes les sessions fermées, une neuve.
        PasswordPolicyError (avant de consommer le code, qui reste valable) ou
        LoginRefusedError (code faux, expiré, épuisé)."""
        from openplan.api.accounts.service import (
            InvalidEmailError,
            LoginRefusal,
            LoginRefusedError,
            checked_email,
        )

        now = self._now()
        try:
            address = checked_email(email)
        except InvalidEmailError:
            raise LoginRefusedError(LoginRefusal.INVALID) from None
        value = check_policy(password, address, self.pwned)
        self.accounts._consume_code(address, typed, "reset", now)
        user = self._user(address)
        if user is None or self._is_admin(user):
            raise LoginRefusedError(LoginRefusal.INVALID)
        found = self._set_password(user.id, value, now)
        self.accounts.revoke_all(user.id)
        raw = self.accounts._open_session(user.id, user_agent, "password", now)
        return found, raw, password_changed_message(address)

    # --- changement, ou mot de passe posé sur un compte existant ----------------------

    def change(
        self,
        user_id: UUID,
        session_raw: str,
        current: str | None,
        password: str,
        user_agent: str,
    ) -> tuple[str, MailMessage]:
        """Le nouveau jeton de session (les autres sessions sont fermées) et l'avis par e-mail.
        Compte avec mot de passe : l'actuel est exigé ; sans : une preuve récente (`reauth`)."""
        from openplan.api.accounts.service import hash_token

        now = self._now()
        with self.accounts.session() as db:
            user = db.get(User, user_id)
            session = db.scalar(
                select(AuthSession).where(AuthSession.token_hash == hash_token(session_raw))
            )
        if user is None or session is None:
            raise PasswordForbiddenError("Session inconnue.")
        if self._is_admin(user):
            raise PasswordForbiddenError("Le compte administrateur se connecte par code seulement.")
        if user.password_hash is not None:
            ok, _ = verify_password(user.password_hash, current or "")
            if not ok:
                self._count_failure(user.id, now)
                raise PasswordForbiddenError("Le mot de passe actuel n'est pas le bon.")
        elif session.reauth_at is None or now - aware(session.reauth_at) > REAUTH_FRESH:
            raise ReauthRequiredError
        value = check_policy(password, user.email, self.pwned)
        self._set_password(user.id, value, now)
        self.accounts.revoke_all(user.id)
        raw = self.accounts._open_session(user.id, user_agent, session.auth_method, now)
        return raw, password_changed_message(user.email)

    def _set_password(self, user_id: UUID, value: str, now: datetime) -> AuthUser:
        from openplan.api.accounts.service import _auth_user

        digest = hash_password(value)
        with self.accounts.session() as db, db.begin():
            row = db.get(User, user_id)
            if row is None:  # pragma: no cover
                raise PasswordForbiddenError("Compte inconnu.")
            row.password_hash = digest
            row.password_set_at = now
            row.failed_logins = 0
            row.locked_until = None
            return _auth_user(row)

    # --- preuve récente ---------------------------------------------------------------

    def start_reauth(self, user_id: UUID, ip: str | None) -> MailMessage | None:
        now = self._now()
        with self.accounts.session() as db, db.begin():
            user = db.get(User, user_id)
            if user is None or self._is_admin(user):
                return None
            code = self.accounts._issue_code(db, user.email, "reauth", REAUTH_TTL, now, ip)
            email = user.email
        if code is None:
            return None
        return reauth_message(email, code, int(REAUTH_TTL.total_seconds() // 60))

    def complete_reauth(self, user_id: UUID, session_raw: str, typed: str) -> None:
        """Code `reauth` juste : la session courante porte la preuve (10 min)."""
        from openplan.api.accounts.service import LoginRefusal, LoginRefusedError, hash_token

        now = self._now()
        with self.accounts.session() as db:
            user = db.get(User, user_id)
        if user is None:
            raise LoginRefusedError(LoginRefusal.INVALID)
        self.accounts._consume_code(user.email, typed, "reauth", now)
        with self.accounts.session() as db, db.begin():
            db.execute(
                update(AuthSession)
                .where(AuthSession.token_hash == hash_token(session_raw))
                .values(reauth_at=now)
            )

    # --- sessions ---------------------------------------------------------------------

    def sessions(self, user_id: UUID, session_raw: str) -> list[SessionInfo]:
        """Mes sessions encore ouvertes, la plus récente d'abord."""
        from openplan.api.accounts.service import hash_token

        now = self._now()
        current = hash_token(session_raw)
        with self.accounts.session() as db:
            rows = db.scalars(
                select(AuthSession)
                .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                .order_by(AuthSession.last_used_at.desc())
            ).all()
            return [
                SessionInfo(
                    id=r.id,
                    created_at=aware(r.created_at),
                    last_used_at=aware(r.last_used_at),
                    user_agent=r.user_agent,
                    auth_method=r.auth_method,
                    current=r.token_hash == current,
                )
                for r in rows
                if aware(r.expires_at) > now
            ]

    def revoke(self, user_id: UUID, session_id: UUID) -> bool:
        """Ferme une de MES sessions ; celle d'un autre compte : rien (faux)."""
        with self.accounts.session() as db, db.begin():
            done = db.execute(
                update(AuthSession)
                .where(
                    AuthSession.id == session_id,
                    AuthSession.user_id == user_id,
                    AuthSession.revoked_at.is_(None),
                )
                .values(revoked_at=self._now())
            )
            return bool(getattr(done, "rowcount", 0))
