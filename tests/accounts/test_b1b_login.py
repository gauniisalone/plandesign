"""B1b, puis B1b2 (18 oct.) : connexion par code à 6 chiffres envoyé par e-mail, sessions,
contrôle d'origine, limites de débit."""

from __future__ import annotations

import json
import logging
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.mail import (
    BrevoApiMailer,
    ConsoleMailer,
    MailerNotConfiguredError,
    MailSendError,
    build_mailer,
    login_message,
)
from openplan.api.accounts.models import AuthSession, LoginToken, User
from openplan.api.accounts.service import (
    ADDRESS_LIMIT,
    IP_LIMIT,
    MAX_ATTEMPTS,
    InvalidEmailError,
    LoginRefusedError,
    clean_code,
    hash_code,
    hash_token,
    new_code,
    safe_next,
    short_ip,
)
from openplan.api.settings import DEFAULT_MAIL_FROM, Settings, parse_mailer, parse_signup
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

NEUTRAL = {"detail": "Si cette adresse peut se connecter, un code vient de partir."}
REFUSED = "ce code n&#x27;est pas bon, ou il a expiré."


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


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
        "OPENPLAN_BREVO_API_KEY": "cle-de-test",
    }
    settings = Settings.from_env(env, dotenv=None)
    assert settings.accounts_enabled and settings.signup == "open"
    assert settings.public_origin == "https://app.example.org"
    assert settings.admin_email == ADMIN
    assert settings.brevo_api_key == "cle-de-test" and "cle-de-test" not in repr(settings)
    assert settings.mail_from == DEFAULT_MAIL_FROM == "connexion@plandesign.fr"
    assert not Settings.from_env({}, dotenv=None).accounts_enabled


def test_next_ip_and_email_rules() -> None:
    assert safe_next("/projets/1?x=2") == "/projets/1?x=2"
    for bad in (None, "", "https://ailleurs.example.org/", "//ailleurs.example.org", "/\\x", "x"):
        assert safe_next(bad) == "/"
    assert short_ip("203.0.113.77") == "203.0.113.0/24"
    assert short_ip("2001:db8:1:2::5") == "2001:db8:1::/48"
    assert short_ip("pas une ip") is None


def test_code_shape() -> None:
    codes = {new_code() for _ in range(300)}
    assert all(re.fullmatch(r"\d{6}", c) for c in codes) and len(codes) > 250
    assert clean_code(" 012 345 ") == "012345"
    for bad in ("12345", "1234567", "abcdef", "", "12-345"):
        assert clean_code(bad) is None


def test_message_is_text_only_with_code_and_no_link(caplog: pytest.LogCaptureFixture) -> None:
    message = login_message("marie@example.org", "012345", 15)
    assert "012345" in message.subject and "012345" in message.text
    assert "Ce code expire dans 15 minutes." in message.text
    assert "ne pas répondre" in message.text
    assert "http" not in message.text and "<" not in message.text and "/api/" not in message.text
    with caplog.at_level(logging.WARNING, logger="openplan.mail"):
        ConsoleMailer().send(message)
    assert "012345" in caplog.text and "m***@example.org" in caplog.text
    assert "marie@example.org" not in caplog.text


def test_brevo_payload_and_send(monkeypatch: pytest.MonkeyPatch) -> None:
    message = login_message("marie@example.org", "012345", 15)
    brevo = build_mailer("smtp_api", DEFAULT_MAIL_FROM, "cle-de-test")
    assert isinstance(brevo, BrevoApiMailer)
    payload = brevo.payload(message)
    assert payload == {
        "sender": {"name": "PlanDesign", "email": "connexion@plandesign.fr"},
        "to": [{"email": "marie@example.org", "contactPixelTrackingConsent": False}],
        "subject": message.subject,
        "textContent": message.text,
    }
    assert "htmlContent" not in payload and "headers" not in payload
    calls: list[dict[str, object]] = []

    class Answer:
        status_code = 201

    def post(url: str, **kwargs: object) -> Answer:
        calls.append({"url": url, **kwargs})
        return Answer()

    import httpx

    monkeypatch.setattr(httpx, "post", post)
    brevo.send(message)
    (call,) = calls
    assert call["url"] == "https://api.brevo.com/v3/smtp/email"
    assert call["json"] == payload
    headers = call["headers"]
    assert isinstance(headers, dict) and headers["api-key"] == "cle-de-test"
    assert not any(k.lower().startswith("x-mailin") for k in headers)
    Answer.status_code = 401
    with pytest.raises(MailSendError, match="HTTP 401") as refused:
        brevo.send(message)
    assert "cle-de-test" not in str(refused.value)
    with pytest.raises(MailerNotConfiguredError):
        BrevoApiMailer(DEFAULT_MAIL_FROM).send(message)  # sans clé : aucun appel
    with pytest.raises(MailerNotConfiguredError):
        build_mailer("smtp_api", DEFAULT_MAIL_FROM)


def test_full_login_with_the_two_step_page(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    assert client.get("/api/auth/me").status_code == 401
    page = client.get("/api/auth/login?next=/p")
    assert page.status_code == 200 and "Se connecter" in page.text
    assert 'action="/api/auth/login/code"' in page.text and "<script" not in page.text
    asked = client.post("/api/auth/login/code", data={"email": " Admin@Example.ORG ", "next": "/p"})
    assert asked.status_code == 200 and NEUTRAL["detail"] in asked.text
    assert 'action="/api/auth/login/verify"' in asked.text and 'name="code"' in asked.text
    assert 'value="Admin@Example.ORG"' in asked.text and "<script" not in asked.text
    code = code_of(mailer, ADMIN)
    with service.session() as db:
        row = db.scalar(select(LoginToken))
        assert row is not None and row.next == "/p" and row.attempts == 0
        assert row.token_hash == hash_code(row.id, ADMIN, code)
        assert code not in json.dumps([row.token_hash, row.email, row.next, row.ip])
    bad = client.post(
        "/api/auth/login/verify",
        data={"email": "Admin@Example.ORG", "code": wrong(code), "next": "/p"},
        follow_redirects=False,
    )
    assert bad.status_code == 400 and REFUSED in bad.text and 'name="code"' in bad.text
    confirm = client.post(
        "/api/auth/login/verify",
        data={"email": "Admin@Example.ORG", "code": f"{code[:3]} {code[3:]}", "next": "/p"},
        follow_redirects=False,
    )
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
    # Usage unique : le même code ne sert pas deux fois.
    again = client.post(
        "/api/auth/login/verify", data={"email": ADMIN, "code": code}, follow_redirects=False
    )
    assert again.status_code == 400 and REFUSED in again.text
    assert "Recevoir un nouveau code" in again.text


def test_json_verify_for_the_front(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url)
    done = client.post("/api/auth/email/start", json={"email": ADMIN, "next": "/p"})
    assert done.status_code == 202 and done.json() == NEUTRAL
    code = code_of(mailer, ADMIN)
    bad = client.post("/api/auth/email/verify", json={"email": ADMIN, "code": wrong(code)})
    assert (
        bad.status_code == 400 and bad.json()["detail"] == "ce code n'est pas bon, ou il a expiré."
    )
    good = client.post("/api/auth/email/verify", json={"email": ADMIN, "code": code})
    assert good.status_code == 200 and good.json()["email"] == ADMIN
    assert "pd_session=" in good.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 200


def test_five_wrong_tries_revoke_the_code(tmp_path: Path, migrated_url: str) -> None:
    _, service, mailer = accounts_client(tmp_path, migrated_url)
    message = service.start_login(ADMIN, "/", "198.51.100.1")
    assert message is not None and not mailer.outbox  # start_login n'envoie rien lui-même
    code = message.code
    # 4 essais faux n'empêchent pas le bon code ensuite.
    for _ in range(MAX_ATTEMPTS - 1):
        with pytest.raises(LoginRefusedError):
            service.complete_login(ADMIN, wrong(code))
    user, raw, _ = service.complete_login(ADMIN, code)
    assert user.email == ADMIN and raw
    # Au 5e essai faux, chaque code encore valable est révoqué : le bon ne passe plus.
    message = service.start_login(ADMIN, "/", "198.51.100.1")
    assert message is not None
    for _ in range(MAX_ATTEMPTS):
        with pytest.raises(LoginRefusedError):
            service.complete_login(ADMIN, wrong(message.code))
    with pytest.raises(LoginRefusedError):
        service.complete_login(ADMIN, message.code)
    with service.session() as db:
        rows = list(db.scalars(select(LoginToken).where(LoginToken.used_at.is_(None))))
    assert rows and all(r.attempts == MAX_ATTEMPTS and r.revoked_at for r in rows)
    # Un code mal formé compte comme un essai faux, sans jamais passer.
    with pytest.raises(LoginRefusedError):
        service.complete_login(ADMIN, "abc")
    with pytest.raises(LoginRefusedError):
        service.complete_login("pas-une-adresse", "123456")


def test_same_answer_whether_or_not_the_address_has_an_account(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    service.create_user("marie@example.org")
    known = client.post("/api/auth/email/start", json={"email": "marie@example.org"})
    unknown = client.post("/api/auth/email/start", json={"email": "inconnu@example.org"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())
    known_page = client.post("/api/auth/login/code", data={"email": "marie@example.org"})
    unknown_page = client.post("/api/auth/login/code", data={"email": "inconnu@example.org"})
    assert known_page.status_code == unknown_page.status_code == 200
    assert known_page.text.replace("marie", "X") == unknown_page.text.replace("inconnu", "X")
    assert [m.to for m in mailer.outbox] == ["marie@example.org"] * 2
    with service.session() as db:  # rien de gardé pour l'adresse inconnue
        assert (
            db.scalar(select(LoginToken).where(LoginToken.email == "inconnu@example.org")) is None
        )
    assert (
        client.post("/api/auth/email/start", json={"email": "pas-une-adresse"}).status_code == 422
    )
    assert client.post("/api/auth/login/code", data={"email": "x@"}).status_code == 422


def test_signup_invite_refuses_and_open_creates(tmp_path: Path, migrated_url: str) -> None:
    # Inscription sur invitation : l'adresse inconnue n'a ni code ni compte, même en base.
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    row_id = uuid4()
    with service.session() as db, db.begin():
        db.add(
            LoginToken(
                id=row_id,
                email="intrus@example.org",
                token_hash=hash_code(row_id, "intrus@example.org", "424242"),
                next="/",
                created_at=utcnow(),
                expires_at=utcnow() + timedelta(minutes=15),
            )
        )
    refused = client.post(
        "/api/auth/login/verify", data={"email": "intrus@example.org", "code": "424242"}
    )
    assert refused.status_code == 400 and "sur invitation" in refused.text
    assert service.user_by_email("intrus@example.org") is None
    client.close()
    # Inscription ouverte : le compte est créé, sans droit d'administrateur.
    client, service, mailer = accounts_client(tmp_path / "b", migrated_url, signup="open")
    sign_in(client, mailer, "nouveau@example.org")
    user = service.user_by_email("nouveau@example.org")
    assert user is not None and not user.is_admin


def test_expired_code_is_refused(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    client.post("/api/auth/email/start", json={"email": ADMIN})
    code = code_of(mailer, ADMIN)
    later = utcnow() + timedelta(minutes=16)
    service.clock = lambda: later
    page = client.post("/api/auth/login/verify", data={"email": ADMIN, "code": code})
    assert page.status_code == 400 and REFUSED in page.text
    assert client.cookies.get("pd_session") is None


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
