"""Lien rouvert par son propre membre (le projet s'ouvre) et formulaire de contact du site
(`POST /api/contact` : origine du site seulement, CORS sans cookies, texte seul, Reply-To,
champ piège, 3 envois par IP et par heure, réponse neutre, rien en base)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from openplan.api.accounts.contact import SUBJECT, THANKS
from openplan.api.accounts.mail import BrevoApiMailer, login_message
from openplan.api.accounts.sharing import INVITE_REFUSED
from openplan.api.settings import DEFAULT_MAIL_FROM, Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

SITE = "https://site.example.org"
BODY = {"email": "visiteur@example.org", "message": "Bonjour,\nune question.", "consent": True}


def _client(app: object, origin: str = ORIGIN) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": origin})  # type: ignore[arg-type]


def test_a_member_reopening_their_own_link_gets_the_project(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(owner, mailer, ADMIN)
    pid = owner.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()["token"]
    guest = _client(owner.app)
    guest.post("/api/auth/email/start", json={"email": "marie@example.org", "invitation": token})
    guest.post(
        "/api/auth/email/verify",
        json={
            "email": "marie@example.org",
            "code": code_of(mailer, "marie@example.org"),
            "invitation": token,
        },
    )
    first = guest.post("/api/auth/invitation/accept", json={"token": token})
    assert first.status_code == 200
    # Rouvert par elle (rechargement à froid : c'est le serveur qui décide) : le projet.
    again = guest.post("/api/auth/invitation/accept", json={"token": token})
    assert again.status_code == 200 and again.json() == {"project_id": pid, "role": "editor"}
    # Pour quelqu'un d'autre : le message unique.
    service.create_user("paul@example.org")
    paul = _client(owner.app)
    sign_in(paul, mailer, "paul@example.org")
    refused = paul.post("/api/auth/invitation/accept", json={"token": token})
    assert refused.status_code == 410 and refused.json()["detail"] == INVITE_REFUSED
    # Retirée du projet depuis : le message unique aussi, pas le projet.
    marie = service.user_by_email("marie@example.org")
    assert marie is not None
    owner.delete(f"/api/projects/{pid}/members/{marie.id}")
    gone = guest.post("/api/auth/invitation/accept", json={"token": token})
    assert gone.status_code == 410 and gone.json()["detail"] == INVITE_REFUSED


def test_contact_sends_text_with_reply_to_and_stores_nothing(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(
        tmp_path, migrated_url, site_origin=SITE, contact_to="contact@example.org"
    )
    tables_before = set(inspect(service.engine).get_table_names())
    site = _client(client.app, SITE)
    pre = site.options("/api/contact", headers={"Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-credentials" not in pre.headers  # sans cookies
    done = site.post("/api/contact", json=BODY)
    assert done.status_code == 202 and done.json() == {"detail": THANKS}
    assert done.headers["access-control-allow-origin"] == SITE
    assert "access-control-allow-credentials" not in done.headers
    message = mailer.last_to("contact@example.org")
    assert message is not None and message.subject == SUBJECT == "PlanDesign · contact"
    assert message.reply_to == "visiteur@example.org" and "une question." in message.text
    assert "<" not in message.text and not message.code
    payload = BrevoApiMailer(DEFAULT_MAIL_FROM, "cle").payload(message)
    assert payload["replyTo"] == {"email": "visiteur@example.org"}
    assert "htmlContent" not in payload
    assert "replyTo" not in BrevoApiMailer(DEFAULT_MAIL_FROM, "cle").payload(
        login_message("a@example.org", "123456", 15)
    )
    assert set(inspect(service.engine).get_table_names()) == tables_before  # rien de nouveau


def test_contact_defaults_to_admin_and_validates(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    site = _client(client.app, SITE)
    assert site.post("/api/contact", json=BODY).status_code == 202
    assert mailer.last_to(ADMIN) is not None  # défaut : OPENPLAN_ADMIN_EMAIL
    assert site.post("/api/contact", json={**BODY, "consent": False}).status_code == 422
    assert site.post("/api/contact", json={**BODY, "email": "pas-une-adresse"}).status_code == 422
    assert site.post("/api/contact", json={**BODY, "message": "x" * 2001}).status_code == 422
    assert site.post("/api/contact", json={**BODY, "message": ""}).status_code == 422
    env = {"OPENPLAN_CONTACT_TO": "contact@example.org"}
    assert Settings.from_env(env, dotenv=None).contact_to == "contact@example.org"


def test_contact_honeypot_and_limit_answer_the_same(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    site = _client(client.app, SITE)
    trap = site.post("/api/contact", json={**BODY, "website": "http://spam.example.org"})
    assert trap.status_code == 202 and trap.json() == {"detail": THANKS}
    assert mailer.outbox == []  # piège rempli : rien ne part
    answers = [site.post("/api/contact", json=BODY) for _ in range(5)]
    assert {(a.status_code, a.json()["detail"]) for a in answers} == {(202, THANKS)}
    assert len(mailer.outbox) == 3  # 3 envois par IP et par heure


def test_contact_is_for_the_site_origin_only(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, site_origin=SITE)
    # L'application elle-même, une autre origine, ou pas d'origine : refusé.
    for origin in (ORIGIN, "https://ailleurs.example.org"):
        refused = _client(client.app, origin).post("/api/contact", json=BODY)
        assert refused.status_code == 403 and "access-control-allow-origin" not in refused.headers
    bare = TestClient(client.app, base_url=ORIGIN)
    assert bare.post("/api/contact", json=BODY).status_code == 403
    assert mailer.outbox == []
    # Sans OPENPLAN_SITE_ORIGIN : refusé partout.
    plain, _, plain_mailer = accounts_client(tmp_path / "b", migrated_url)
    assert _client(plain.app, SITE).post("/api/contact", json=BODY).status_code == 403
    assert plain_mailer.outbox == []
