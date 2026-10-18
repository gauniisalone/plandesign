"""B1e : liens d'invitation (rôle, expiration, usage unique, révocation), inscription « sur
invitation » par le jeton, menu Partager réservé au propriétaire, membres, rôle dans la galerie."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.models import Invitation
from openplan.api.accounts.service import AccountService
from openplan.api.accounts.sharing import INVITE_REFUSED, LastOwnerError, hash_invite
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in


def _client(app: object) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})  # type: ignore[arg-type]


def _project(client: TestClient, name: str = "Grenier") -> str:
    made = client.post("/api/projects", json={"name": name})
    assert made.status_code == 201, made.text
    return str(made.json()["id"])


def _invite(client: TestClient, pid: str, role: str = "viewer", days: int = 7) -> dict[str, str]:
    done = client.post(f"/api/projects/{pid}/invitations", json={"role": role, "days": days})
    assert done.status_code == 201, done.text
    body: dict[str, str] = done.json()
    return body


def _join_with_link(client: TestClient, mailer: object, email: str, token: str) -> None:
    """Le parcours d'un invité sans compte : code demandé et vérifié avec le jeton, puis
    acceptation."""
    asked = client.post("/api/auth/email/start", json={"email": email, "invitation": token})
    assert asked.status_code == 202
    verified = client.post(
        "/api/auth/email/verify",
        json={"email": email, "code": code_of(mailer, email), "invitation": token},  # type: ignore[arg-type]
    )
    assert verified.status_code == 200, verified.text


def test_link_token_is_hashed_single_use_and_role_bound(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    link = _invite(owner, pid, role="editor")
    token = link["token"]
    assert len(token) >= 43 and link["role"] == "editor"
    with service.session() as db:
        row = db.scalar(select(Invitation))
        assert row is not None and row.token_hash == hash_invite(token)
        assert token not in json.dumps([row.token_hash, str(row.id), row.role])
    listed = owner.get(f"/api/projects/{pid}/invitations").json()
    assert [i["id"] for i in listed] == [link["id"]] and "token" not in listed[0]
    # Invité sans compte, inscription « sur invitation » (défaut) : le jeton ouvre le compte.
    guest = _client(owner.app)
    _join_with_link(guest, mailer, "marie@example.org", token)
    accepted = guest.post("/api/auth/invitation/accept", json={"token": token})
    assert accepted.status_code == 200 and accepted.json() == {"project_id": pid, "role": "editor"}
    assert guest.get(f"/api/projects/{pid}/role").json() == {"role": "editor"}
    assert owner.get(f"/api/projects/{pid}/invitations").json() == []
    # Usage unique : un second compte ne peut plus s'en servir, ni pour s'inscrire.
    other = _client(owner.app)
    assert (
        other.post(
            "/api/auth/email/start", json={"email": "paul@example.org", "invitation": token}
        ).status_code
        == 202
    )
    assert mailer.last_to("paul@example.org") is None  # réponse neutre, rien ne part
    service.create_user("paul@example.org")
    sign_in(other, mailer, "paul@example.org")
    again = other.post("/api/auth/invitation/accept", json={"token": token})
    assert again.status_code == 410 and again.json()["detail"] == INVITE_REFUSED


def test_expired_revoked_and_bogus_links_give_the_same_answer(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    revoked = _invite(owner, pid)
    assert owner.delete(f"/api/projects/{pid}/invitations/{revoked['id']}").status_code == 204
    assert owner.delete(f"/api/projects/{pid}/invitations/{revoked['id']}").status_code == 404
    expiring = _invite(owner, pid, days=1)
    service.create_user("marie@example.org")
    guest = _client(owner.app)
    sign_in(guest, mailer, "marie@example.org")
    later = utcnow() + timedelta(days=1, minutes=1)
    service.clock = lambda: later
    for token in (revoked["token"], expiring["token"], "x" * 43):
        refused = guest.post("/api/auth/invitation/accept", json={"token": token})
        assert refused.status_code == 410 and refused.json()["detail"] == INVITE_REFUSED
    assert guest.get(f"/api/projects/{pid}/role").status_code == 404
    # Un jeton faux n'ouvre pas l'inscription : même réponse neutre, rien ne part.
    nobody = _client(owner.app)
    start = nobody.post(
        "/api/auth/email/start", json={"email": "inconnu@example.org", "invitation": "x" * 43}
    )
    assert start.status_code == 202 and mailer.last_to("inconnu@example.org") is None


def test_invitation_choices_and_owner_only_sharing(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    for bad in ({"role": "owner"}, {"days": 3}, {"role": "admin"}):
        assert owner.post(f"/api/projects/{pid}/invitations", json=bad).status_code == 422
    link = _invite(owner, pid, role="viewer")
    guest = _client(owner.app)
    _join_with_link(guest, mailer, "marie@example.org", link["token"])
    guest.post("/api/auth/invitation/accept", json={"token": link["token"]})
    # Un lecteur ne voit ni les liens ni les membres et n'invite personne.
    for method, path in (
        ("GET", f"/api/projects/{pid}/invitations"),
        ("POST", f"/api/projects/{pid}/invitations"),
        ("GET", f"/api/projects/{pid}/members"),
    ):
        assert guest.request(method, path, json={}).status_code == 403
    # Un non-membre : le projet n'existe pas pour lui.
    service.create_user("paul@example.org")
    stranger = _client(owner.app)
    sign_in(stranger, mailer, "paul@example.org")
    assert stranger.get(f"/api/projects/{pid}/members").status_code == 404


def test_members_roles_and_last_owner(tmp_path: Path, migrated_url: str) -> None:
    owner, _, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    link = _invite(owner, pid, role="commenter")
    guest = _client(owner.app)
    _join_with_link(guest, mailer, "marie@example.org", link["token"])
    guest.post("/api/auth/invitation/accept", json={"token": link["token"]})
    members = owner.get(f"/api/projects/{pid}/members").json()
    assert [(m["email"], m["role"], m["you"]) for m in members] == [
        (ADMIN, "owner", True),
        ("marie@example.org", "commenter", False),
    ]
    marie = members[1]["user_id"]
    me = members[0]["user_id"]
    changed = owner.patch(f"/api/projects/{pid}/members/{marie}", json={"role": "editor"})
    assert changed.status_code == 200 and changed.json()[1]["role"] == "editor"
    # Le dernier propriétaire ne peut être ni rétrogradé ni retiré.
    assert (
        owner.patch(f"/api/projects/{pid}/members/{me}", json={"role": "viewer"}).status_code == 409
    )
    assert owner.delete(f"/api/projects/{pid}/members/{me}").status_code == 409
    gone = owner.delete(f"/api/projects/{pid}/members/{marie}")
    assert gone.status_code == 200 and len(gone.json()) == 1
    assert guest.get(f"/api/projects/{pid}/role").status_code == 404
    assert owner.delete(f"/api/projects/{pid}/members/{marie}").status_code == 404


def test_accepting_keeps_the_higher_role(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    editor = _invite(owner, pid, role="editor")
    viewer = _invite(owner, pid, role="viewer")
    guest = _client(owner.app)
    _join_with_link(guest, mailer, "marie@example.org", editor["token"])
    assert (
        guest.post("/api/auth/invitation/accept", json={"token": editor["token"]}).json()["role"]
        == "editor"
    )
    again = guest.post("/api/auth/invitation/accept", json={"token": viewer["token"]})
    assert again.json() == {"project_id": pid, "role": "editor"}
    # Le service seul : retirer le dernier propriétaire est refusé.
    admin = service.user_by_email(ADMIN)
    assert admin is not None
    with pytest.raises(LastOwnerError):
        service.remove_membership(UUID(pid), admin.id)


def test_gallery_carries_the_role(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = _project(owner)
    link = _invite(owner, pid, role="viewer")
    guest = _client(owner.app)
    _join_with_link(guest, mailer, "marie@example.org", link["token"])
    guest.post("/api/auth/invitation/accept", json={"token": link["token"]})
    _project(guest, "Mon salon")
    mine = {p["name"]: p["role"] for p in guest.get("/api/projects").json()}
    assert mine == {"Grenier": "viewer", "Mon salon": "owner"}
    assert [p["role"] for p in owner.get("/api/projects").json()] == ["owner"]
    # Supprimer un projet retire ses liens d'invitation.
    _invite(owner, pid)
    assert owner.delete(f"/api/projects/{pid}").status_code == 204
    with service.session() as db:
        assert db.scalar(select(Invitation).where(Invitation.project_id == UUID(pid))) is None


def test_service_level_invitation_rules(tmp_path: Path, migrated_url: str) -> None:
    _, service, _ = accounts_client(tmp_path, migrated_url)
    assert isinstance(service, AccountService)
    admin = service.create_user("owner@example.org")
    pid = UUID(int=7)
    service.add_project(pid, admin.id, "Projet")
    with pytest.raises(ValueError, match="rôle"):
        service.create_invitation(pid, "owner", 7, admin.id)
    with pytest.raises(ValueError, match="durée"):
        service.create_invitation(pid, "viewer", 2, admin.id)
    pending, raw = service.create_invitation(pid, "viewer", 30, admin.id)
    assert service.valid_invitation(raw) and not service.valid_invitation(None)
    assert (pending.expires_at - pending.created_at).days == 30
