"""B1b : connexion par lien e-mail, sessions, contrôle d'origine, limites de débit."""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.mail import (
    BrevoApiMailer,
    ConsoleMailer,
    MailerNotConfiguredError,
    build_mailer,
    login_message,
)
from openplan.api.accounts.models import AuthSession, LoginToken, User
from openplan.api.accounts.service import (
    ADDRESS_LIMIT,
    IP_LIMIT,
    InvalidEmailError,
    hash_token,
    safe_next,
    short_ip,
)
from openplan.api.settings import Settings, parse_mailer, parse_signup
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in, token_of

NEUTRAL = {"detail": "Si cette adresse peut se connecter, un lien vient de partir."}


def test_settings_signup_and_mailer() -> None:
    assert parse_signup(None) == "invite" == parse_signup("Invitation")
    assert parse_signup("ouvert") == "open" == parse_signup("open")
    with pytest.raises(ValueError, match="OPENPLAN_SIGNUP"):
        parse_signup("tout le monde")
    assert parse_mailer(None) == "console"
    with pytest.raises(ValueError, match="OPENPLAN_MAILER"):
        parse_mailer("sendgrid")
    env = {
        "OPENPLAN_DATABASE_URL": "sqlite:///x.db",
        "OPENPLAN_SIGNUP": "ouvert",
        "OPENPLAN_PUBLIC_URL": "https://App.Example.org/",
        "OPENPLAN_ADMIN_EMAIL": ADMIN,
    }
    settings = Settings.from_env(env, dotenv=None)
    assert settings.accounts_enabled and settings.signup == "open"
    assert settings.public_origin == "https://app.example.org"
    assert settings.admin_email == ADMIN
    assert not Settings.from_env({}, dotenv=None).accounts_enabled


def test_next_ip_and_email_rules() -> None:
    assert safe_next("/projets/1?x=2") == "/projets/1?x=2"
    for bad in (None, "", "https://ailleurs.example.org/", "//ailleurs.example.org", "/\\x", "x"):
        assert safe_next(bad) == "/"
    assert short_ip("203.0.113.77") == "203.0.113.0/24"
    assert short_ip("2001:db8:1:2::5") == "2001:db8:1::/48"
    assert short_ip("pas une ip") is None


def test_messages_and_mailers(caplog: pytest.LogCaptureFixture) -> None:
    link = "https://app.example.org/api/auth/email/callback?token=abc&x=<b>"
    message = login_message("marie@example.org", link, 15)
    assert link in message.text and "15 minutes" in message.text
    assert "ignorez ce message" in message.text and "ignorez ce message" in message.html
    assert "<b>" not in message.html and "&lt;b&gt;" in message.html
    with caplog.at_level(logging.WARNING, logger="openplan.mail"):
        ConsoleMailer().send(message)
    assert link in caplog.text and "m***@example.org" in caplog.text
    assert "marie@example.org" not in caplog.text
    brevo = BrevoApiMailer("connexion@example.org")
    assert brevo.payload(message)["to"] == [{"email": "marie@example.org"}]
    with pytest.raises(MailerNotConfiguredError):
        brevo.send(message)  # squelette : aucun appel réel
    with pytest.raises(MailerNotConfiguredError):
        build_mailer("smtp_api", "connexion@example.org")


def test_full_login_with_confirmation_page(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    assert client.get("/api/auth/me").status_code == 401
    done = client.post("/api/auth/email/start", json={"email": " Admin@Example.ORG ", "next": "/p"})
    assert done.status_code == 202 and done.json() == NEUTRAL
    message = mailer.last_to(ADMIN)
    assert message is not None and message.link.startswith(f"{ORIGIN}/api/auth/email/callback?")
    token = token_of(message.link)
    assert len(token) >= 43  # 32 octets en base64url
    with service.session() as db:
        row = db.scalar(select(LoginToken))
        assert row is not None and row.token_hash == hash_token(token) and row.next == "/p"
        assert token not in json.dumps([row.token_hash, row.email, row.next, row.ip])
    # L'ouverture du lien (aperçu de messagerie compris) ne consomme rien.
    for _ in range(2):
        page = client.get(f"/api/auth/email/callback?token={token}")
        assert page.status_code == 200 and "Se connecter" in page.text
        assert 'method="post"' in page.text and "<script" not in page.text
    confirm = client.post("/api/auth/email/callback", data={"token": token}, follow_redirects=False)
    assert confirm.status_code == 303 and confirm.headers["location"] == "/p"
    cookie = confirm.headers["set-cookie"].lower()
    for part in ("pd_session=", "httponly", "secure", "samesite=lax", "path=/", "max-age=2592000"):
        assert part in cookie
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == ADMIN and me.json()["is_admin"]
    with service.session() as db:
        stored = db.scalar(select(AuthSession))
        raw = client.cookies.get("pd_session")
        assert stored is not None and raw and stored.token_hash == hash_token(raw)
    # Usage unique.
    again = client.post("/api/auth/email/callback", data={"token": token}, follow_redirects=False)
    assert again.status_code == 400 and "déjà servi" in again.text
    assert "Recevoir un nouveau lien" in again.text
    assert client.get(f"/api/auth/email/callback?token={token}").status_code == 400
    bogus = client.post("/api/auth/email/callback", data={"token": "x" * 43})
    assert bogus.status_code == 400 and "pas valable" in bogus.text


def test_same_answer_whether_or_not_the_address_has_an_account(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    service.create_user("marie@example.org")
    known = client.post("/api/auth/email/start", json={"email": "marie@example.org"})
    unknown = client.post("/api/auth/email/start", json={"email": "inconnu@example.org"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    assert [m.to for m in mailer.outbox] == ["marie@example.org"]
    with service.session() as db:  # rien de gardé pour l'adresse inconnue
        assert (
            db.scalar(select(LoginToken).where(LoginToken.email == "inconnu@example.org")) is None
        )
    assert (
        client.post("/api/auth/email/start", json={"email": "pas-une-adresse"}).status_code == 422
    )


def test_signup_invite_refuses_and_open_creates(tmp_path: Path, migrated_url: str) -> None:
    # Inscription sur invitation : l'adresse inconnue n'a ni lien ni compte, même avec un jeton.
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    message = service.start_login("admin@example.org", "/", "198.51.100.1")
    assert message is not None
    with service.session() as db, db.begin():
        db.add(
            LoginToken(
                email="intrus@example.org",
                token_hash=hash_token("t" * 43),
                next="/",
                created_at=utcnow(),
                expires_at=utcnow() + timedelta(minutes=15),
            )
        )
    refused = client.post("/api/auth/email/callback", data={"token": "t" * 43})
    assert refused.status_code == 400 and "sur invitation" in refused.text
    assert service.user_by_email("intrus@example.org") is None
    client.close()
    # Inscription ouverte : le compte est créé, sans droit d'administrateur.
    client, service, mailer = accounts_client(tmp_path / "b", migrated_url, signup="open")
    sign_in(client, mailer, "nouveau@example.org")
    user = service.user_by_email("nouveau@example.org")
    assert user is not None and not user.is_admin


def test_expired_link_is_refused(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    client.post("/api/auth/email/start", json={"email": ADMIN})
    message = mailer.last_to(ADMIN)
    assert message is not None
    later = utcnow() + timedelta(minutes=16)
    service.clock = lambda: later
    page = client.get(f"/api/auth/email/callback?token={token_of(message.link)}")
    assert page.status_code == 400 and "expiré" in page.text


def test_rate_limits_per_address_and_per_ip(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    for _ in range(ADDRESS_LIMIT + 2):
        done = client.post("/api/auth/email/start", json={"email": ADMIN})
        assert done.status_code == 202 and done.json() == NEUTRAL
    assert len(mailer.outbox) == ADDRESS_LIMIT
    # 10 demandes par IP et par heure (adresses différentes, toutes inconnues ou non).
    service.create_user("marie@example.org")
    sent = [service.start_login("marie@example.org", "/", "192.0.2.9") for _ in range(IP_LIMIT)]
    assert sum(m is not None for m in sent) == ADDRESS_LIMIT
    assert service.start_login("marie@example.org", "/", "192.0.2.10") is None  # adresse
    service.create_user("paul@example.org")
    assert service.start_login("paul@example.org", "/", "192.0.2.9") is None  # IP
    assert service.start_login("paul@example.org", "/", "192.0.2.11") is not None
    with pytest.raises(InvalidEmailError):
        service.start_login("x@", "/", "192.0.2.12")
    # Limite générale de /api/auth/* : 10 tentatives par minute et par IP, puis 429.
    codes = [
        client.post("/api/auth/email/start", json={"email": "z@example.org"}).status_code
        for _ in range(12)
    ]
    assert 429 in codes
    assert client.get("/api/health").status_code == 200


def test_origin_is_checked_on_every_mutation(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(client, mailer, ADMIN)
    body = {"name": "Grenier"}
    bare = TestClient(client.app, base_url=ORIGIN)
    bare.cookies.set("pd_session", client.cookies.get("pd_session") or "")
    assert bare.post("/api/projects", json=body).status_code == 403
    other = {"Origin": "https://ailleurs.example.org"}
    assert bare.post("/api/projects", json=body, headers=other).status_code == 403
    assert bare.post("/api/projects", json=body, headers={"Origin": "null"}).status_code == 403
    by_referer = {"Referer": f"{ORIGIN}/espace"}
    assert bare.post("/api/projects", json=body, headers=by_referer).status_code == 201
    assert bare.post("/api/auth/email/start", json={"email": ADMIN}).status_code == 403
    assert bare.get("/api/projects").status_code == 200  # lecture : pas d'origine exigée
    page = bare.get("/api/projects")
    assert page.headers["referrer-policy"] == "same-origin"


def test_sessions_slide_expire_and_revoke(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    assert client.get("/api/projects").status_code == 401
    raw = sign_in(client, mailer, ADMIN)
    assert client.get("/api/projects").status_code == 200
    start = utcnow()
    # Prolongée à l'usage, au plus une fois par heure.
    service.clock = lambda: start + timedelta(minutes=30)
    assert "set-cookie" not in client.get("/api/projects").headers
    service.clock = lambda: start + timedelta(hours=2)
    renewed = client.get("/api/projects")
    assert renewed.status_code == 200 and "pd_session=" in renewed.headers["set-cookie"]
    # 30 jours après la dernière prolongation (et non après la connexion), elle expire.
    service.clock = lambda: start + timedelta(days=30, hours=3)
    expired = client.get("/api/projects")
    assert expired.status_code == 401
    service.clock = utcnow
    # Révocation : déconnexion, puis « me déconnecter partout ».
    client.cookies.clear()
    raw = sign_in(client, mailer, ADMIN)
    assert client.post("/api/auth/logout").status_code == 204
    replay = TestClient(client.app, base_url=ORIGIN)
    replay.cookies.set("pd_session", raw)
    assert replay.get("/api/projects").status_code == 401
    with service.session() as db:
        assert db.scalar(
            select(AuthSession.revoked_at).where(AuthSession.token_hash == hash_token(raw))
        )


def test_logout_everywhere_and_admin_revocation(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    laptop = sign_in(client, mailer, "marie@example.org")
    phone_client = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(phone_client, mailer, "marie@example.org")
    assert phone_client.post("/api/auth/logout-all").status_code == 204
    stale = TestClient(client.app, base_url=ORIGIN)
    stale.cookies.set("pd_session", laptop)
    assert stale.get("/api/auth/me").status_code == 401
    # Un administrateur révoque les sessions d'un autre ; un non-administrateur ne peut pas.
    marie_client = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(marie_client, mailer, "marie@example.org")
    marie = service.user_by_email("marie@example.org")
    assert marie is not None
    url = f"/api/admin/users/{marie.id}/sessions/revoke"
    assert marie_client.post(url).status_code == 403
    admin_client = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(admin_client, mailer, ADMIN)
    assert admin_client.post(url).json() == {"revoked": 1}
    assert marie_client.get("/api/auth/me").status_code == 401
    with service.session() as db:
        admins = list(db.scalars(select(User.email).where(User.is_admin)))
    assert admins == [ADMIN]  # seule l'adresse OPENPLAN_ADMIN_EMAIL devient administrateur


def test_exposed_access_log_names_the_user_never_the_address(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url, host="0.0.0.0")
    assert (
        client.post("/api/session").status_code == 401
    )  # l'ancien cookie de lecture n'existe plus
    sign_in(client, mailer, ADMIN)
    assert client.get("/api/projects").status_code == 200
    admin = service.user_by_email(ADMIN)
    assert admin is not None
    log = (tmp_path / "data" / "access.jsonl").read_text(encoding="utf-8")
    assert f"user:{admin.id}" in log
    assert ADMIN not in log and "token" not in log
