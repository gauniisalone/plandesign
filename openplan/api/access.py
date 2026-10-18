"""Qui fait la requête (note 17, § 3 et § 4) : l'utilisateur de la session et ce qu'il peut faire.

Ce module n'importe ni SQLAlchemy ni la base : il ne connaît que le protocole
`AccountsBackend`, qu'implémente `openplan.api.accounts.service` quand les comptes sont actifs
(OPENPLAN_DATABASE_URL). En mode local, `request.app.state.accounts` vaut None et rien ici ne
refuse quoi que ce soit : le mode local ne change pas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

from fastapi import Depends, HTTPException, Request

SESSION_COOKIE = "pd_session"
SESSION_MAX_AGE_S = 30 * 24 * 3600
LOGIN_REQUIRED = "connexion requise"


@dataclass(frozen=True)
class AuthUser:
    """L'utilisateur de la session. L'adresse ne sert qu'à l'affichage : jamais journalisée."""

    id: UUID
    email: str
    name: str
    is_admin: bool
    first_login: bool = False
    """Compte créé par la connexion en cours (B2a : proposer « Votre nom »)."""


@dataclass(frozen=True)
class ResolvedSession:
    user: AuthUser
    renewed: bool
    """Prolongée par cette requête (au plus une fois par heure) : le cookie est reposé."""


class AccountsBackend(Protocol):
    """Ce que l'API attend des comptes (implémenté par `accounts.service.AccountService`)."""

    def resolve_session(self, raw: str) -> ResolvedSession | None: ...

    def role_name(self, project_id: UUID, user_id: UUID) -> str | None: ...

    def add_project(self, project_id: UUID, owner_id: UUID, name: str) -> object: ...

    def remove_project(self, project_id: UUID) -> None: ...

    def project_ids_of(self, user_id: UUID) -> set[UUID]: ...

    def roles_of(self, user_id: UUID) -> dict[UUID, str]: ...


def accounts_of(request: Request) -> AccountsBackend | None:
    backend: AccountsBackend | None = getattr(request.app.state, "accounts", None)
    return backend


def optional_user(request: Request) -> AuthUser | None:
    """L'utilisateur posé par l'intergiciel de session, s'il y en a un."""
    user: AuthUser | None = getattr(request.state, "user", None)
    return user


def current_user(request: Request) -> AuthUser | None:
    """Comptes actifs : la session valide, sinon 401. Mode local : None (pas de compte)."""
    if accounts_of(request) is None:
        return None
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)
    return user


def signed_in_user(request: Request) -> AuthUser:
    """Pour les routes qui n'existent qu'avec les comptes (`/api/auth/me`…) : 401 sans session."""
    user = optional_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=LOGIN_REQUIRED)
    return user


def admin_user(request: Request) -> AuthUser:
    user = signed_in_user(request)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="réservé à un administrateur")
    return user


CurrentUserDep = Annotated[AuthUser | None, Depends(current_user)]
SignedInDep = Annotated[AuthUser, Depends(signed_in_user)]
AdminDep = Annotated[AuthUser, Depends(admin_user)]
