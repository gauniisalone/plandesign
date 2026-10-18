"""Menu « Partager » d'un projet (note 17, B1e) : liens d'invitation et membres. Réservé au
propriétaire (`openplan.api.guards.PROJECT_ROUTES`). N'existe que si les comptes sont actifs."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from anyio import from_thread
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field

from openplan.api.access import SignedInDep
from openplan.api.accounts.routes import service
from openplan.api.accounts.sharing import LastOwnerError, MemberView, PendingInvitation
from openplan.schemas.common import StrictModel

router = APIRouter(tags=["partage"])

InviteRole = Literal["viewer", "commenter", "editor"]
AnyRole = Literal["viewer", "commenter", "editor", "owner"]


class InvitationCreate(StrictModel):
    role: InviteRole = "viewer"
    days: Literal[1, 7, 30] = 7


class InvitationOut(StrictModel):
    id: UUID
    role: str
    created_at: datetime
    expires_at: datetime


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
    return InvitationOut(id=p.id, role=p.role, created_at=p.created_at, expires_at=p.expires_at)


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
    return [_member(m, user.id) for m in accounts.members(project_id)]
