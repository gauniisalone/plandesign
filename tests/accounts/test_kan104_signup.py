"""KAN-104 L2 : inscription (adresse prouvée par un code, puis mot de passe), mode d'ouverture
`invite` (défaut) ou `open`, anti-abus et quotas des comptes inscrits.

Fermé par défaut : OPENPLAN_PASSWORD_LOGIN faux (routes 404) et OPENPLAN_SIGNUP=invite. HIBP
est SIMULÉ, aucun appel réseau."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from openplan.api import security
from openplan.api.accounts import passwords
from openplan.api.accounts.mail import FakeMailer
from openplan.api.accounts.models import User
from openplan.api.accounts.service import AccountService
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

NEW = "zoe@example.org"
GOOD = "cerisier en fleur 2026"
NEUTRAL = {"detail": "Si cette adresse peut créer un compte, un code vient de partir."}

Made = tuple[TestClient, AccountService, FakeMailer]


@pytest.fixture(autouse=True)
def simulated_hibp(monkeypatch: pytest.MonkeyPatch) -> None:
    """HIBP simulé (aucun mot de passe n'y figure) ; tout vrai appel réseau échoue le test."""

    def no_network(*_a: object, **_k: object) -> None:
        pytest.fail("appel réseau réel dans un test")

    monkeypatch.setattr(passwords, "fetch_range", lambda _prefix: "0" * 35 + ":0")
    monkeypatch.setattr("httpx.get", no_network)
    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 1000)


def _client(tmp_path: Path, url: str, **overrides: object) -> Made:
    return accounts_client(tmp_path, url, password_login=True, **overrides)


def _users(service: AccountService) -> int:
    with service.session() as db:
        return int(db.scalar(select(func.count()).select_from(User)) or 0)


def _signup(client: TestClient, mailer: FakeMailer, email: str = NEW, **body: object) -> dict:
    assert (
        client.post("/api/auth/signup/start", json={"email": email, "cgu": True}).status_code == 202
    )
    done = client.post(
        "/api/auth/signup/verify",
        json={"email": email, "code": code_of(mailer, email), "password": GOOD, **body},
    )
    assert done.status_code == 200, done.text
    return dict(done.json())


# --- fermé par défaut -----------------------------------------------------------------------


def test_closed_by_default(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    for path, body in (
        ("/api/auth/signup/start", {"email": NEW, "cgu": True}),
        ("/api/auth/signup/verify", {"email": NEW, "code": "123456", "password": GOOD}),
    ):
        assert client.post(path, json=body).status_code == 404, path
    assert mailer.outbox == []
    assert Settings().signup == "invite"
    assert (Settings().signup_quota_mb, Settings().signup_max_projects) == (200, 5)
    assert (Settings().mail_daily_cap, Settings().mail_daily_total) == (200, 280)
    env = Settings.from_env(
        {"OPENPLAN_SIGNUP_QUOTA_MB": "100", "OPENPLAN_MAIL_DAILY_CAP": "0"}, dotenv=None
    )
    assert (env.signup_quota_mb, env.mail_daily_cap, env.signup_max_projects) == (100, 0, 5)
    with pytest.raises(ValueError, match="entier"):
        Settings.from_env({"OPENPLAN_SIGNUP_MAX_PROJECTS": "beaucoup"}, dotenv=None)


def test_invite_mode_only_for_invited_addresses(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = _client(tmp_path, migrated_url)
    owner = service.create_user("bruno@example.org")
    project = uuid4()
    service.add_project(project, owner.id, "Atelier")
    _, token = service.create_invitation(project, "viewer", 7, owner.id)
    start = client.post("/api/auth/signup/start", json={"email": NEW, "cgu": True})
    assert (start.status_code, start.json()) == (202, NEUTRAL)
    assert mailer.last_to(NEW) is None  # sur invitation : rien ne part
    invited = client.post(
        "/api/auth/signup/start", json={"email": NEW, "cgu": True, "invitation": token}
    )
    assert (invited.status_code, invited.json()) == (202, NEUTRAL)
    me = client.post(
        "/api/auth/signup/verify",
        json={"email": NEW, "code": code_of(mailer, NEW), "password": GOOD, "invitation": token},
    )
    assert me.status_code == 200 and me.json()["first_login"] is True


# --- inscription ouverte ----------------------------------------------------------------------


def test_signup_proves_the_address_before_any_account(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = _client(tmp_path, migrated_url, signup="open")
    before = _users(service)
    assert (
        client.post("/api/auth/signup/start", json={"email": NEW, "cgu": False}).status_code == 422
    )
    assert mailer.last_to(NEW) is None
    start = client.post("/api/auth/signup/start", json={"email": f" {NEW.upper()} ", "cgu": True})
    assert (start.status_code, start.json()) == (202, NEUTRAL)
    message = mailer.last_code_to(NEW)
    assert message is not None and "créer votre compte" in message.subject
    assert _users(service) == before  # rien d'écrit avant la preuve
    # Un code d'inscription n'ouvre pas de connexion par code.
    login = client.post("/api/auth/email/verify", json={"email": NEW, "code": message.code})
    assert login.status_code in (400, 403)
    weak = client.post(
        "/api/auth/signup/verify", json={"email": NEW, "code": message.code, "password": "court"}
    )
    assert weak.status_code == 422 and _users(service) == before
    done = client.post(
        "/api/auth/signup/verify",
        json={"email": NEW, "code": message.code, "password": GOOD, "name": "  Zoé   M. "},
    )
    assert done.status_code == 200, done.text  # le code restait valable
    assert done.json()["first_login"] is True and done.json()["name"] == "Zoé M."
    assert client.get("/api/auth/me").json()["email"] == NEW
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == NEW))
    assert user is not None and user.email_verified_at and user.accepted_terms_at
    assert user.terms_version and user.password_hash and user.password_hash.startswith("$argon2id$")
    again = client.post(
        "/api/auth/signup/verify", json={"email": NEW, "code": message.code, "password": GOOD}
    )
    assert again.status_code == 400  # usage unique
    fresh = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    assert (
        fresh.post("/api/auth/password/login", json={"email": NEW, "password": GOOD}).status_code
        == 200
    )


def test_same_answer_for_every_address(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = _client(tmp_path, migrated_url, signup="open")
    service.create_user("lea@example.org")
    answers = [
        client.post("/api/auth/signup/start", json={"email": e, "cgu": True})
        for e in ("lea@example.org", ADMIN, "x@yopmail.com", NEW)
    ]
    assert {(a.status_code, a.json()["detail"]) for a in answers} == {(202, NEUTRAL["detail"])}
    already = mailer.last_to("lea@example.org")
    assert already is not None and "déjà un compte" in already.subject and not already.code
    assert mailer.last_to(ADMIN) is None  # l'administrateur : code seulement (D3)
    assert mailer.last_to("x@yopmail.com") is None  # domaine jetable
    assert mailer.last_code_to(NEW) is not None
    # Un code de connexion ne crée pas de compte.
    sign_in(client, mailer, "lea@example.org")
    refused = client.post(
        "/api/auth/signup/verify",
        json={
            "email": "lea@example.org",
            "code": code_of(mailer, "lea@example.org"),
            "password": GOOD,
        },
    )
    assert refused.status_code == 400


# --- anti-abus ----------------------------------------------------------------------------


def test_three_signups_per_day_and_ip(tmp_path: Path, migrated_url: str) -> None:
    _, service, _ = _client(tmp_path, migrated_url, signup="open")
    ip = "198.51.100.243"
    for n in range(3):
        email = f"inscrit{n}@example.org"
        message = service.signup.start(email, ip)
        assert message is not None
        service.signup.complete(email, message.code, GOOD, "", ip, "")
    assert service.signup.start("inscrit3@example.org", ip) is None
    assert service.signup.start("inscrit3@example.org", "198.51.100.244") is None  # même /24
    assert service.signup.start("inscrit3@example.org", "203.0.113.244") is not None


def test_ten_codes_per_day_and_address(tmp_path: Path, migrated_url: str) -> None:
    _, service, _ = _client(tmp_path, migrated_url, signup="open")
    now = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    for n in range(10):
        moment = now + timedelta(minutes=16 * n)  # hors de la limite de 3 par 15 min
        service.clock = lambda moment=moment: moment
        assert service.signup.start(NEW, f"198.51.100.{100 + n}") is not None, n
    later = now + timedelta(minutes=16 * 10)
    service.clock = lambda: later
    assert service.signup.start(NEW, "198.51.100.120") is None
    next_day = now + timedelta(days=1, minutes=1)
    service.clock = lambda: next_day
    assert service.signup.start(NEW, "198.51.100.121") is not None


def test_new_doors_close_first_and_login_codes_still_pass(
    tmp_path: Path, migrated_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    """KAN-117 : la classe `signup` (inscription, oubli, preuve récente) s'arrête à
    OPENPLAN_MAIL_DAILY_CAP ; ensuite 503, la même pour toutes les adresses ; le code de
    connexion passe encore."""
    client, service, mailer = _client(tmp_path, migrated_url, signup="open", mail_daily_cap=3)
    service.create_user("lea@example.org")
    with caplog.at_level(logging.WARNING, logger="openplan.accounts"):
        for n in range(3):
            start = client.post(
                "/api/auth/signup/start", json={"email": f"p{n}@example.org", "cgu": True}
            )
            assert start.status_code == 202
        refused = [
            client.post("/api/auth/signup/start", json={"email": e, "cgu": True})
            for e in ("p3@example.org", "lea@example.org")
        ] + [client.post("/api/auth/password/forgot", json={"email": "lea@example.org"})]
    for answer in refused:
        assert answer.status_code == 503
        assert answer.headers["X-PlanDesign-Limit"] == "mail-quota"
    assert len(mailer.outbox) == 3
    assert "« signup » fermés" in caplog.text and "@" not in caplog.text
    sign_in(client, mailer, "lea@example.org")  # P1 : toujours ouvert


# --- quotas des comptes inscrits ----------------------------------------------------------


def test_signed_up_accounts_have_tighter_quotas(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = _client(tmp_path, migrated_url, signup="open", signup_max_projects=2)
    _signup(client, mailer)
    assert client.get("/api/auth/usage").json()["quota_bytes"] == 200 * 1024 * 1024
    for _ in range(2):
        assert client.post("/api/projects", json={}).status_code == 201
    capped = client.post("/api/projects", json={})
    assert capped.status_code == 403 and "2 projets" in capped.json()["detail"]
    assert capped.json()["reason"] == "project_cap"  # KAN-105 L2
    # Un compte d'avant (code, sur invitation) n'est pas touché.
    service.create_user("lea@example.org")
    other = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(other, mailer, "lea@example.org")
    assert other.get("/api/auth/usage").json()["quota_bytes"] == 500 * 1024 * 1024
    for _ in range(3):
        assert other.post("/api/projects", json={}).status_code == 201
