"""KAN-104 L2 : inscription par adresse prouvée, puis mot de passe (conception, § 4).

Derrière OPENPLAN_PASSWORD_LOGIN (routes 404 sans lui) ET OPENPLAN_SIGNUP :
- `invite` (défaut) : seule une adresse invitée nommément à un projet (KAN-55) ou porteuse
  d'un lien d'invitation encore valable (B1e) reçoit un code ;
- `open` : toute adresse (hors domaines jetables).
Pas de liste d'attente (décision du PO du 2 nov.).

La réponse publique est TOUJOURS la même ; seul l'e-mail diffère, que seul le titulaire lit :
adresse inconnue = un code `signup` ; compte existant = « vous avez déjà un compte », sans
code ; inscription fermée, domaine jetable, administrateur ou limite atteinte = rien.
Aucune ligne `users` n'est écrite avant la preuve de l'adresse (personne ne réserve l'adresse
d'un autre). L'administrateur ne s'inscrit jamais par mot de passe (D3).

Anti-abus : 10 demandes/h par IP (comme la connexion), 3 inscriptions par jour et par IP
(tronquée), 3 codes par adresse par 15 min et 10 par jour, plafond quotidien d'e-mails,
domaines jetables refusés.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from openplan.api.access import AuthUser
from openplan.api.accounts.abuse import disposable
from openplan.api.accounts.mail import MailMessage, already_message, signup_message
from openplan.api.accounts.models import LoginToken, User
from openplan.api.accounts.passwords import check_policy, hash_after
from openplan.api.accounts.terms import TERMS_VERSION  # KAN-133 : la page /cgu/ du site
from openplan.api.ratelimit import SlidingWindowLimiter

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from openplan.api.accounts.service import AccountService

SIGNUP_TTL = timedelta(minutes=15)
DAY = timedelta(days=1)
SIGNUPS_PER_IP_PER_DAY = 3
CODES_PER_ADDRESS_PER_DAY = 10


class SignupFlows:
    """Rattaché à `AccountService` (`service.signup`)."""

    def __init__(self, accounts: AccountService) -> None:
        self.accounts = accounts
        self.already = SlidingWindowLimiter(3, 900.0)
        """« Vous avez déjà un compte » : 3 par adresse par 15 min (aucun code n'est écrit)."""

    def _now(self) -> datetime:
        return self.accounts.clock()

    def _open_to(self, db: Session, address: str, invitation: str | None) -> bool:
        if self.accounts.settings.signup == "open":
            return True
        if self.accounts._live_for_email(db, address):  # KAN-55
            return True
        return invitation is not None and self.accounts._live_invitation(db, invitation) is not None

    def _signups_today(self, db: Session, ip: str | None, now: datetime) -> int:
        from openplan.api.accounts.service import short_ip

        net = short_ip(ip)
        if net is None:
            return 0
        return int(
            db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.signup_ip == net, User.created_at > now - DAY)
            )
            or 0
        )

    def start(
        self, email: str, ip: str | None, invitation: str | None = None
    ) -> MailMessage | None:
        """Le message à envoyer, ou None ; l'appelant répond pareil dans tous les cas.
        InvalidEmailError si l'adresse n'en a pas la forme."""
        from openplan.api.accounts.service import checked_email

        address = checked_email(email)
        if (
            not self.accounts.ip_limiter.allow(ip or "-")
            or address == self.accounts.admin_email
            or disposable(address)
        ):
            return None
        now = self._now()
        with self.accounts.session() as db, db.begin():
            if db.scalar(select(User.id).where(User.email == address)) is not None:
                return already_message(address) if self.already.allow(address) else None
            code = (
                self.accounts._issue_code(db, address, "signup", SIGNUP_TTL, now, ip)
                if self._may_send_code(db, address, ip, invitation, now)
                else None
            )
        if code is None:
            return None
        return signup_message(address, code, int(SIGNUP_TTL.total_seconds() // 60))

    def _may_send_code(
        self, db: Session, address: str, ip: str | None, invitation: str | None, now: datetime
    ) -> bool:
        """Inscription ouverte à cette adresse, 3 inscriptions par jour et par IP, 10 codes
        par jour et par adresse (le plafond d'e-mails du jour se contrôle à la route et se
        compte à l'envoi, KAN-117)."""
        if not self._open_to(db, address, invitation):
            return False
        if self._signups_today(db, ip, now) >= SIGNUPS_PER_IP_PER_DAY:
            return False
        today = db.scalar(
            select(func.count())
            .select_from(LoginToken)
            .where(LoginToken.email == address, LoginToken.created_at > now - DAY)
        )
        return (today or 0) < CODES_PER_ADDRESS_PER_DAY

    def complete(
        self,
        email: str,
        typed: str,
        password: str,
        name: str,
        ip: str | None,
        user_agent: str,
        invitation: str | None = None,
    ) -> tuple[AuthUser, str]:
        """Code `signup` juste : le compte est créé avec son mot de passe et la session s'ouvre.
        PasswordPolicyError (avant de consommer le code, qui reste valable) ;
        LoginRefusedError (code faux, adresse déjà prise entre-temps, inscription fermée)."""
        from dataclasses import replace

        from openplan.api.accounts.service import (
            InvalidEmailError,
            LoginRefusal,
            LoginRefusedError,
            _auth_user,
            checked_email,
            short_ip,
        )

        now = self._now()
        try:
            address = checked_email(email)
        except InvalidEmailError:
            raise LoginRefusedError(LoginRefusal.INVALID) from None
        if address == self.accounts.admin_email:
            raise LoginRefusedError(LoginRefusal.INVALID)
        value = check_policy(password, address, self.accounts.passwords.pwned)
        digest = hash_after(
            lambda: self.accounts._consume_code(address, typed, "signup", now), value
        )
        with self.accounts.session() as db, db.begin():
            if db.scalar(select(User.id).where(User.email == address)) is not None:
                raise LoginRefusedError(LoginRefusal.INVALID)
            if not self._open_to(db, address, invitation):
                raise LoginRefusedError(LoginRefusal.CLOSED)
            user = User(
                email=address,
                name=" ".join(name.split())[:80],
                is_admin=False,
                created_at=now,
                last_seen_at=now,
                email_verified_at=now,
                signup_ip=short_ip(ip),
                accepted_terms_at=now,
                terms_version=TERMS_VERSION,
                terms_accepted_at=now,  # kan126b_0014 : la date de l'acceptation
                password_hash=digest,
                password_set_at=now,
            )
            db.add(user)
            db.flush()
            found = replace(_auth_user(user), first_login=True)
        raw = self.accounts._open_session(found.id, user_agent, "password", now)
        return found, raw
