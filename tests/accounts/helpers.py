"""Application à comptes pour les tests : base migrée, fausse boîte aux lettres, origine
publique `https://app.example.org`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from openplan.api.accounts.mail import FakeMailer
from openplan.api.accounts.service import AccountService
from openplan.api.app import create_app
from openplan.api.settings import Settings

ORIGIN = "https://app.example.org"
ADMIN = "admin@example.org"


def accounts_settings(tmp_path: Path, url: str, **overrides: object) -> Settings:
    base = Settings(
        data_dir=tmp_path / "data",
        database_url=url,
        admin_email=ADMIN,
        mailer="fake",
        public_url=ORIGIN,
        auto_path=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def accounts_client(
    tmp_path: Path, url: str, **overrides: object
) -> tuple[TestClient, AccountService, FakeMailer]:
    app = create_app(settings=accounts_settings(tmp_path, url, **overrides))
    service: AccountService = app.state.accounts
    assert isinstance(service.mailer, FakeMailer)
    client = TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    return client, service, service.mailer


def code_of(mailer: FakeMailer, email: str) -> str:
    message = mailer.last_to(email)
    assert message is not None, f"aucun code pour {email}"
    return message.code


def sign_in(client: TestClient, mailer: FakeMailer, email: str, next_path: str = "/") -> str:
    """Parcours complet (demande, code tapé) ; renvoie la valeur du cookie de session."""
    done = client.post("/api/auth/email/start", json={"email": email, "next": next_path})
    assert done.status_code == 202, done.text
    confirm = client.post(
        "/api/auth/login/verify",
        data={"email": email, "code": code_of(mailer, email)},
        follow_redirects=False,
    )
    assert confirm.status_code == 303, confirm.text
    value = client.cookies.get("pd_session")
    assert value
    return value
