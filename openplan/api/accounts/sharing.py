"""Partage d'un projet (note 17, B1e) : liens d'invitation et membres.

- Lien : `https://app…/#/invitation/<jeton>`. Le jeton (32 octets aléatoires) est dans l'ancre :
  le navigateur ne l'envoie jamais au serveur dans une URL (ni journal, ni Referer) ; le front
  le transmet dans le corps d'un POST. Il n'est gardé qu'en empreinte SHA-256, montré une seule
  fois (à la création), et ne part jamais par e-mail (pas de Brevo) : le propriétaire copie le
  lien.
- Un lien donne un rôle (lecteur, commentateur ou éditeur, jamais propriétaire), expire (1, 7
  ou 30 jours), sert une seule fois et se révoque tant qu'il n'a pas servi. Faux, expiré,
  révoqué ou déjà servi : une seule réponse.
- Inscription « sur invitation » : un jeton valable permet à une adresse inconnue de recevoir
  un code et d'avoir un compte (service.py).
- Membres : le propriétaire change les rôles et retire des membres ; le dernier propriétaire ne
  peut être ni retiré ni rétrogradé.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from openplan.api.accounts.db import aware
from openplan.api.accounts.models import INVITE_ROLES, ROLES, Invitation, Member, User

INVITE_TOKEN_BYTES = 32
INVITE_DAYS = (1, 7, 30)
"""Durées proposées ; 7 par défaut."""
INVITE_REFUSED = (
    "Ce lien d'invitation n'est plus valable : demandez-en un nouveau au propriétaire du projet."
)
LAST_OWNER = "le dernier propriétaire du projet ne peut être ni retiré ni rétrogradé"
ROLE_RANK = {name: rank for rank, name in enumerate(ROLES)}


class InvitationRefusedError(Exception):
    """Lien faux, expiré, révoqué ou déjà servi : toujours la même réponse."""


class LastOwnerError(Exception):
    """Retrait ou rétrogradation du dernier propriétaire."""


def hash_invite(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class PendingInvitation:
    id: UUID
    role: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class MemberView:
    user_id: UUID
    email: str
    name: str
    role: str


class SharingMixin:
    """Mélangé à `AccountService` (session de base et horloge)."""

    session: Callable[[], Session]
    clock: Callable[[], datetime]

    # --- liens ------------------------------------------------------------------------

    def create_invitation(
        self, project_id: UUID, role: str, days: int, created_by: UUID
    ) -> tuple[PendingInvitation, str]:
        """Le lien en attente et son jeton (rendu une seule fois)."""
        if role not in INVITE_ROLES:
            msg = f"rôle d'invitation inconnu : {role}"
            raise ValueError(msg)
        if days not in INVITE_DAYS:
            msg = f"durée d'invitation : {', '.join(map(str, INVITE_DAYS))} jours"
            raise ValueError(msg)
        raw = secrets.token_urlsafe(INVITE_TOKEN_BYTES)
        now = self.clock()
        row = Invitation(
            project_id=project_id,
            role=role,
            token_hash=hash_invite(raw),
            created_by=created_by,
            created_at=now,
            expires_at=now + timedelta(days=days),
        )
        with self.session() as db, db.begin():
            db.add(row)
        return PendingInvitation(row.id, row.role, row.created_at, row.expires_at), raw

    def pending_invitations(self, project_id: UUID) -> list[PendingInvitation]:
        now = self.clock()
        with self.session() as db:
            rows = db.scalars(
                select(Invitation)
                .where(
                    Invitation.project_id == project_id,
                    Invitation.used_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
                .order_by(Invitation.created_at)
            )
            return [
                PendingInvitation(r.id, r.role, aware(r.created_at), aware(r.expires_at))
                for r in rows
                if aware(r.expires_at) > now
            ]

    def revoke_invitation(self, project_id: UUID, invitation_id: UUID) -> bool:
        with self.session() as db, db.begin():
            done = db.execute(
                update(Invitation)
                .where(
                    Invitation.id == invitation_id,
                    Invitation.project_id == project_id,
                    Invitation.used_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
                .values(revoked_at=self.clock())
            )
        return getattr(done, "rowcount", 0) == 1

    def valid_invitation(self, raw: str | None) -> bool:
        """Jeton d'un lien encore valable (pour l'inscription « sur invitation »)."""
        if not raw:
            return False
        with self.session() as db:
            return self._live_invitation(db, raw) is not None

    def _live_invitation(self, db: Session, raw: str) -> Invitation | None:
        row = db.scalar(select(Invitation).where(Invitation.token_hash == hash_invite(raw)))
        if row is None or row.used_at is not None or row.revoked_at is not None:
            return None
        return row if aware(row.expires_at) > self.clock() else None

    def accept_invitation(self, raw: str, user_id: UUID) -> tuple[UUID, str]:
        """Consomme le lien (une seule fois, même en cas d'envois simultanés) et rend
        l'utilisateur membre avec son rôle ; déjà membre avec plus : on garde le plus haut.
        Renvoie (projet, rôle effectif)."""
        now = self.clock()
        with self.session() as db, db.begin():
            row = self._live_invitation(db, raw or "")
            if row is None:
                # Rouvert par celui qui l'a servi et toujours membre : le projet s'ouvre (rien
                # n'est révélé, il l'a déjà). Pour tout autre, le message unique.
                mine = self._used_by_member(db, raw or "", user_id)
                if mine is not None:
                    return mine
                raise InvitationRefusedError(INVITE_REFUSED)
            done = db.execute(
                update(Invitation)
                .where(
                    Invitation.id == row.id,
                    Invitation.used_at.is_(None),
                    Invitation.revoked_at.is_(None),
                )
                .values(used_at=now, used_by=user_id)
            )
            if getattr(done, "rowcount", 0) != 1:
                raise InvitationRefusedError(INVITE_REFUSED)
            member = db.get(Member, (row.project_id, user_id))
            if member is None:
                db.add(
                    Member(
                        project_id=row.project_id,
                        user_id=user_id,
                        role=row.role,
                        added_by=row.created_by,
                        added_at=now,
                    )
                )
                return row.project_id, row.role
            if ROLE_RANK[row.role] > ROLE_RANK[member.role]:
                member.role = row.role
            return row.project_id, member.role

    def _used_by_member(self, db: Session, raw: str, user_id: UUID) -> tuple[UUID, str] | None:
        row = db.scalar(select(Invitation).where(Invitation.token_hash == hash_invite(raw)))
        if row is None or row.used_by != user_id:
            return None
        member = db.get(Member, (row.project_id, user_id))
        return None if member is None else (row.project_id, member.role)

    # --- membres ----------------------------------------------------------------------

    def members(self, project_id: UUID) -> list[MemberView]:
        with self.session() as db:
            rows = db.execute(
                select(Member.user_id, User.email, User.name, Member.role)
                .join(User, User.id == Member.user_id)
                .where(Member.project_id == project_id)
                .order_by(Member.added_at)
            )
            return [MemberView(uid, email, name, role) for uid, email, name, role in rows]

    def _owner_count(self, db: Session, project_id: UUID) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(Member)
                .where(Member.project_id == project_id, Member.role == "owner")
            )
            or 0
        )

    def change_role(self, project_id: UUID, user_id: UUID, role: str) -> bool:
        """False si l'utilisateur n'est pas membre."""
        if role not in ROLES:
            msg = f"rôle inconnu : {role}"
            raise ValueError(msg)
        with self.session() as db, db.begin():
            member = db.get(Member, (project_id, user_id))
            if member is None:
                return False
            if member.role == "owner" and role != "owner" and self._owner_count(db, project_id) < 2:
                raise LastOwnerError(LAST_OWNER)
            member.role = role
        return True

    def remove_membership(self, project_id: UUID, user_id: UUID) -> bool:
        """False si l'utilisateur n'est pas membre."""
        with self.session() as db, db.begin():
            member = db.get(Member, (project_id, user_id))
            if member is None:
                return False
            if member.role == "owner" and self._owner_count(db, project_id) < 2:
                raise LastOwnerError(LAST_OWNER)
            db.delete(member)
        return True
