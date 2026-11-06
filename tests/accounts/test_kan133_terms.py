"""KAN-133 : les conditions d'utilisation à la connexion par code (MH3) et l'écran de nouvelle
acceptation (MH6).

Adresses de test sur le domaine réservé `example.test`. Aucun e-mail réel : la boîte
`FakeMailer` des tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts.mail import FakeMailer
from openplan.api.accounts.models import User
from openplan.api.accounts.service import AccountService
from openplan.api.accounts.terms import TERMS_URL, TERMS_VERSION
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

NEW = "nouvelle.kan133@example.test"
OLD = "ancien.kan133@example.test"


def _client(app: object) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})  # type: ignore[arg-type]


def _user(service: AccountService, email: str) -> User:
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


def _invited(tmp_path: Path, url: str) -> tuple[TestClient, AccountService, FakeMailer]:
    """Un projet de l'administrateur, et NEW invitée nommément (inscription sur invitation)."""
    owner, service, mailer = accounts_client(tmp_path, url)
    sign_in(owner, mailer, ADMIN)
    pid = owner.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    sent = owner.post(
        f"/api/projects/{pid}/invitations/email", json={"email": NEW, "role": "editor"}
    )
    assert sent.status_code in (200, 201, 202), sent.text
    return owner, service, mailer


def test_mh3_a_new_address_gets_a_code_only_with_the_box(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = _invited(tmp_path, migrated_url)
    guest = _client(owner.app)
    before = mailer.last_code_to(NEW)
    # Sans la case : même réponse, mais aucun code (rien ne dit si l'adresse a un compte).
    quiet = guest.post("/api/auth/email/start", json={"email": NEW})
    assert quiet.status_code == 202
    assert mailer.last_code_to(NEW) is before
    asked = guest.post("/api/auth/email/start", json={"email": NEW, "cgu": True})
    assert asked.status_code == 202
    assert asked.json() == quiet.json()
    done = guest.post(
        "/api/auth/email/verify",
        json={"email": NEW, "code": code_of(mailer, NEW)},
    )
    assert done.status_code == 200, done.text
    me = done.json()
    assert me["first_login"] is True
    assert me["terms_pending"] is False
    assert me["terms_version"] == TERMS_VERSION and me["terms_url"] == TERMS_URL
    user = _user(service, NEW)
    assert user.terms_version == TERMS_VERSION
    # accepted_terms_at reste le repère de l'inscription par mot de passe (quotas serrés).
    assert user.accepted_terms_at is None
    assert service.self_signed_up(user.id) is False


def test_mh3_an_existing_account_signs_in_without_the_box(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    service.create_user(OLD)
    asked = client.post("/api/auth/email/start", json={"email": OLD})
    assert asked.status_code == 202
    done = client.post("/api/auth/email/verify", json={"email": OLD, "code": code_of(mailer, OLD)})
    assert done.status_code == 200, done.text
    # Jamais accepté (compte d'avant KAN-133) : l'écran de nouvelle acceptation.
    assert done.json()["terms_pending"] is True
    assert client.get("/api/auth/me").json()["terms_pending"] is True


def test_mh3_the_box_ticked_at_sign_in_records_the_version(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    service.create_user(OLD)
    client.post("/api/auth/email/start", json={"email": OLD, "cgu": True})
    done = client.post(
        "/api/auth/email/verify",
        json={"email": OLD, "code": code_of(mailer, OLD), "cgu": True},
    )
    assert done.status_code == 200, done.text
    assert done.json()["terms_pending"] is False
    assert _user(service, OLD).terms_version == TERMS_VERSION


def test_mh6_accepting_the_new_version(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    user = service.create_user(OLD)
    with service.session() as db, db.begin():
        db.get(User, user.id).terms_version = "2000-01"  # type: ignore[union-attr]
    anonymous = _client(client.app)
    refused = anonymous.post("/api/auth/terms/accept", json={"version": TERMS_VERSION})
    assert refused.status_code == 401
    client.post("/api/auth/email/start", json={"email": OLD})
    client.post("/api/auth/email/verify", json={"email": OLD, "code": code_of(mailer, OLD)})
    assert client.get("/api/auth/me").json()["terms_pending"] is True
    stale = client.post("/api/auth/terms/accept", json={"version": "2000-01"})
    assert stale.status_code == 409
    assert client.get("/api/auth/me").json()["terms_pending"] is True
    ok = client.post("/api/auth/terms/accept", json={"version": TERMS_VERSION})
    assert ok.status_code == 204, ok.text
    assert client.get("/api/auth/me").json()["terms_pending"] is False
    accepted = _user(service, OLD)
    assert accepted.terms_version == TERMS_VERSION
    assert accepted.accepted_terms_at is None  # les quotas du compte ne changent pas
    assert service.self_signed_up(accepted.id) is False


def test_mh6_the_administrator_is_never_asked(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(client, mailer, ADMIN)
    assert client.get("/api/auth/me").json()["terms_pending"] is False
    assert client.post("/api/auth/terms/accept", json={"version": TERMS_VERSION}).status_code == 204
    assert _user(service, ADMIN).terms_version is None


def test_mh3_the_page_without_script_has_the_box(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer = _invited(tmp_path, migrated_url)
    guest = _client(owner.app)
    page = guest.get("/api/auth/login")
    assert page.status_code == 200
    assert 'name="cgu" type="checkbox" value="1" required' in page.text
    assert f'href="{TERMS_URL}"' in page.text
    before = mailer.last_code_to(NEW)
    quiet = guest.post("/api/auth/login/code", data={"email": NEW})
    assert quiet.status_code == 200
    assert mailer.last_code_to(NEW) is before
    asked = guest.post("/api/auth/login/code", data={"email": NEW, "cgu": "1"})
    assert asked.status_code == 200
    assert 'name="cgu" value="1"' in asked.text  # repassée à la vérification
    done = guest.post(
        "/api/auth/login/verify",
        data={"email": NEW, "code": code_of(mailer, NEW), "cgu": "1"},
        follow_redirects=False,
    )
    assert done.status_code == 303, done.text
    assert _user(service, NEW).terms_version == TERMS_VERSION
