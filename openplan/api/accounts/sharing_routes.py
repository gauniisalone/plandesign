"""Menu « Partager » d'un projet (note 17, B1e) : liens d'invitation et membres. Réservé au
propriétaire (`openplan.api.guards.PROJECT_ROUTES`). N'existe que si les comptes sont actifs."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal
from uuid import UUID

from anyio import from_thread
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response
from pydantic import Field

from openplan.api.access import SignedInDep
from openplan.api.accounts.mail import invitation_message
from openplan.api.accounts.routes import announce_join, service
from openplan.api.accounts.service import AccountService, InvalidEmailError, checked_email
from openplan.api.accounts.sharing import (
    EMAIL_INVITE_DAYS,
    EMAIL_INVITE_REFUSED,
    INVITE_QUOTA,
    INVITE_SENT,
    InvitationRefusedError,
    InviteQuotaError,
    LastOwnerError,
    MemberView,
    PendingInvitation,
)
from openplan.brand import APP_NAME
from openplan.pipeline.board_views import ViewStore
from openplan.scene.store import ProjectStore
from openplan.schemas.common import StrictModel

log = logging.getLogger("openplan.accounts")
router = APIRouter(tags=["partage"])

InviteRole = Literal["viewer", "commenter", "editor"]
AnyRole = Literal["viewer", "commenter", "editor", "owner"]
ROLE_WORD = {"viewer": "lecteur", "commenter": "commentateur", "editor": "éditeur"}


class InvitationCreate(StrictModel):
    role: InviteRole = "viewer"
    days: Literal[1, 7, 30] = 7


class EmailInvitationCreate(StrictModel):
    """KAN-55 : inviter une adresse avec un rôle (14 jours)."""

    email: str = Field(min_length=3, max_length=320)
    role: InviteRole = "viewer"


class InviteSent(StrictModel):
    detail: str = INVITE_SENT


class MyInvitationOut(StrictModel):
    """KAN-55 : une invitation dans la cloche de l'invité : qui, quel projet, quel rôle."""

    id: UUID
    project_id: UUID
    project_name: str
    role: str
    inviter_name: str
    created_at: datetime
    expires_at: datetime


class Joined(StrictModel):
    project_id: UUID
    role: str


class InvitationOut(StrictModel):
    id: UUID
    role: str
    created_at: datetime
    expires_at: datetime
    email: str | None = None
    """KAN-55 : l'adresse d'une invitation nominative (route réservée au propriétaire)."""


class InvitationCreated(InvitationOut):
    token: str
    """Montré une seule fois : le front en fait `<origine>/#/invitation/<jeton>`."""


class MemberOut(StrictModel):
    user_id: UUID
    email: str
    name: str
    role: str
    you: bool = False


class RoleChange(StrictModel):
    role: AnyRole = Field()


def _out(p: PendingInvitation) -> InvitationOut:
    return InvitationOut(
        id=p.id, role=p.role, created_at=p.created_at, expires_at=p.expires_at, email=p.email
    )


def _member(m: MemberView, me: UUID) -> MemberOut:
    return MemberOut(
        user_id=m.user_id, email=m.email, name=m.name, role=m.role, you=m.user_id == me
    )


@router.post("/projects/{project_id}/invitations", status_code=201)
def create_invitation(
    project_id: UUID, body: InvitationCreate, request: Request, user: SignedInDep
) -> InvitationCreated:
    pending, token = service(request).create_invitation(project_id, body.role, body.days, user.id)
    return InvitationCreated(**_out(pending).model_dump(), token=token)


@router.post("/projects/{project_id}/invitations/email", status_code=202)
def invite_by_email(
    project_id: UUID,
    body: EmailInvitationCreate,
    request: Request,
    user: SignedInDep,
    background: BackgroundTasks,
) -> InviteSent:
    """KAN-55 : toujours la même réponse, que l'adresse ait un compte ou non ; l'avis (cloche
    ou e-mail) part après la réponse, pour que sa durée ne trahisse rien non plus."""
    accounts = service(request)
    try:
        address = checked_email(body.email)
    except InvalidEmailError as exc:
        raise HTTPException(status_code=422, detail="adresse e-mail invalide") from exc
    try:
        done = accounts.invite_by_email(project_id, address, body.role, user.id)
    except InviteQuotaError as exc:
        raise HTTPException(status_code=429, detail=INVITE_QUOTA) from exc
    if done.invitee is not None:
        from openplan.api.accounts.live import hub_of

        background.add_task(hub_of(request.app).ring, [str(done.invitee)])
    if done.send_mail:
        store = request.app.state.store
        background.add_task(
            _send_invitation, accounts, store, project_id, user.id, address, body.role
        )
    return InviteSent()


def _send_invitation(
    accounts: AccountService,
    store: ProjectStore,
    project_id: UUID,
    inviter: UUID,
    address: str,
    role: str,
) -> None:
    """Après la réponse : le message est composé puis envoyé (Brevo en production)."""
    try:
        project = store.load(project_id).name if store.exists(project_id) else ""
        message = invitation_message(
            address,
            accounts.display_of(inviter) or APP_NAME,
            project,
            ROLE_WORD[role],
            accounts.settings.public_url,
            EMAIL_INVITE_DAYS,
        )
        accounts.mailer.send(message)
    except Exception:  # jamais l'adresse dans le journal (KAN-55, MH6), ni la trace
        log.error("envoi d'une invitation impossible")


@router.get("/invitations")
def my_invitations(request: Request, user: SignedInDep) -> list[MyInvitationOut]:
    """KAN-55 : mes invitations nominatives en attente (la cloche) ; ni adresse ni jeton."""
    accounts = service(request)
    names = {s.id: s.name for s in request.app.state.store.list()}
    return [
        MyInvitationOut(
            id=i.id,
            project_id=i.project_id,
            project_name=names.get(i.project_id, i.project_name),
            role=i.role,
            inviter_name=(accounts.display_of(i.inviter) if i.inviter else None) or "Quelqu'un",
            created_at=i.created_at,
            expires_at=i.expires_at,
        )
        for i in accounts.my_invitations(user.email)
    ]


@router.post("/invitations/{invitation_id}/accept")
def accept_my_invitation(invitation_id: UUID, request: Request, user: SignedInDep) -> Joined:
    """« Accepter » : membre avec le rôle prévu ; le front ouvre le projet."""
    accounts = service(request)
    before = set(accounts.roles_of(user.id))
    try:
        project_id, role = accounts.accept_email_invitation(invitation_id, user.id, user.email)
    except InvitationRefusedError as exc:
        raise HTTPException(status_code=410, detail=EMAIL_INVITE_REFUSED) from exc
    if project_id not in before:
        announce_join(request, project_id, user.id)
    return Joined(project_id=project_id, role=role)


@router.post("/invitations/{invitation_id}/decline", status_code=204)
def decline_my_invitation(invitation_id: UUID, request: Request, user: SignedInDep) -> Response:
    if not service(request).decline_email_invitation(invitation_id, user.email):
        raise HTTPException(status_code=410, detail=EMAIL_INVITE_REFUSED)
    return Response(status_code=204)


@router.get("/projects/{project_id}/invitations")
def list_invitations(project_id: UUID, request: Request) -> list[InvitationOut]:
    """Les liens en attente (ni servis, ni révoqués, ni expirés), sans leur jeton."""
    return [_out(p) for p in service(request).pending_invitations(project_id)]


@router.delete("/projects/{project_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(project_id: UUID, invitation_id: UUID, request: Request) -> Response:
    if not service(request).revoke_invitation(project_id, invitation_id):
        raise HTTPException(status_code=404, detail="lien inconnu, déjà servi ou déjà révoqué")
    return Response(status_code=204)


@router.get("/projects/{project_id}/members")
def list_members(project_id: UUID, request: Request, user: SignedInDep) -> list[MemberOut]:
    return [_member(m, user.id) for m in service(request).members(project_id)]


@router.patch("/projects/{project_id}/members/{user_id}")
def change_member_role(
    project_id: UUID, user_id: UUID, body: RoleChange, request: Request, user: SignedInDep
) -> list[MemberOut]:
    accounts = service(request)
    try:
        found = accounts.change_role(project_id, user_id, body.role)
    except LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=404, detail="ce compte n'est pas membre du projet")
    return [_member(m, user.id) for m in accounts.members(project_id)]


def _evict(request: Request, project_id: UUID, user_id: UUID | None) -> None:
    """B2a : les connexions de présence de la personne retirée sont fermées tout de suite."""
    from openplan.api.accounts.live import hub_of

    from_thread.run(hub_of(request.app).evict, project_id, user_id)


@router.delete("/projects/{project_id}/members/{user_id}")
def remove_member(
    project_id: UUID, user_id: UUID, request: Request, user: SignedInDep
) -> list[MemberOut]:
    accounts = service(request)
    try:
        found = accounts.remove_membership(project_id, user_id)
    except LastOwnerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not found:
        raise HTTPException(status_code=404, detail="ce compte n'est pas membre du projet")
    _evict(request, project_id, user_id)
    # KAN-43 : un membre retiré perd sa vue de l'espace de travail.
    store = request.app.state.store
    with store.lock(project_id):
        ViewStore(store.project_dir(project_id)).forget(str(user_id))
    return [_member(m, user.id) for m in accounts.members(project_id)]
