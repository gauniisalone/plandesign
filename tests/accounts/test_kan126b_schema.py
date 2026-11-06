"""Lot schéma du 6 nov. (migration kan126b_0014) :

- KAN-126b : `users.offer` (`essentiel` | `pro`, Pro par défaut pour les comptes existants et
  nouveaux), posée par l'administrateur (POST /api/admin/users/find, PATCH
  /api/admin/users/{id}/offer), lue par `offer_of` ;
- la date d'acceptation des CGU, `users.terms_accepted_at`, distincte de `accepted_terms_at`,
  écrite par les trois voies de KAN-133 ;
- KAN-145 : `owner.changed` à l'héritier (cloche et activité), au changement de rôle.

Adresses de test sur le domaine réservé `example.test`. Aucun e-mail réel."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts.db import make_engine, upgrade
from openplan.api.accounts.models import User
from openplan.api.accounts.service import AccountService
from openplan.api.accounts.terms import TERMS_VERSION
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in
from tests.accounts.test_kan51_comments import _team

OLD = "ancien.kan126b@example.test"


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def _client(app: object) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})  # type: ignore[arg-type]


def _user(service: AccountService, email: str) -> User:
    with service.session() as db:
        user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


# ---------------------------------------------------------------- migration


def test_the_migration_keeps_every_account_pro_and_copies_the_signup_date(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine, "kan104_0012")
    signed = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    with engine.begin() as con:
        for email, accepted in (("a@example.test", signed), ("b@example.test", None)):
            con.execute(
                sa.text(
                    "INSERT INTO users (id, email, name, is_admin, created_at, failed_logins, "
                    "accepted_terms_at) VALUES (:id, :email, '', :f, :now, 0, :acc)"
                ),
                {
                    "id": uuid4().hex,
                    "email": email,
                    "f": False,
                    "now": signed,
                    "acc": accepted,
                },
            )
    upgrade(engine)
    with engine.connect() as con:
        rows = {
            r.email: r
            for r in con.execute(
                sa.text("SELECT email, offer, terms_accepted_at FROM users ORDER BY email")
            )
        }
    engine.dispose()
    assert {r.offer for r in rows.values()} == {"pro"}
    assert rows["a@example.test"].terms_accepted_at is not None
    assert rows["b@example.test"].terms_accepted_at is None


# ---------------------------------------------------------------- offre (KAN-126b)


def test_a_new_account_is_pro(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(client, mailer, ADMIN)
    assert _user(service, ADMIN).offer == "pro"
    assert client.get("/api/auth/me").json()["offer"] == "pro"
    assert service.create_user(OLD).offer == "pro"


def test_the_administrator_sets_the_offer_and_it_applies_at_once(
    tmp_path: Path, migrated_url: str
) -> None:
    admin, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(admin, mailer, ADMIN)
    service.create_user(OLD)
    other = _client(admin.app)
    other.post("/api/auth/email/start", json={"email": OLD})
    other.post("/api/auth/email/verify", json={"email": OLD, "code": code_of(mailer, OLD)})
    pid = other.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    assert other.get(f"/api/projects/{pid}/exports/dxf").status_code != 403

    found = admin.post("/api/admin/users/find", json={"email": " Ancien.KAN126b@example.test "})
    assert found.status_code == 200, found.text
    account: dict[str, Any] = found.json()
    assert account["email"] == OLD and account["offer"] == "pro"
    set_to = admin.patch(f"/api/admin/users/{account['id']}/offer", json={"offer": "essentiel"})
    assert set_to.status_code == 200, set_to.text
    assert set_to.json()["offer"] == "essentiel"
    # Dès la requête suivante du compte : /auth/me et le refus d'un export Pro.
    assert other.get("/api/auth/me").json()["offer"] == "essentiel"
    refused = other.get(f"/api/projects/{pid}/exports/dxf")
    assert refused.status_code == 403 and refused.json()["reason"] == "offer_pro"
    back = admin.patch(f"/api/admin/users/{account['id']}/offer", json={"offer": "pro"})
    assert back.status_code == 200
    assert other.get(f"/api/projects/{pid}/exports/dxf").status_code != 403


def test_only_the_administrator_sets_an_offer_and_values_are_bounded(
    tmp_path: Path, migrated_url: str
) -> None:
    admin, service, mailer = accounts_client(tmp_path, migrated_url)
    sign_in(admin, mailer, ADMIN)
    user = service.create_user(OLD)
    url = f"/api/admin/users/{user.id}/offer"
    assert admin.patch(url, json={"offer": "premium"}).status_code == 422
    unknown = "/api/admin/users/00000000-0000-0000-0000-000000000000/offer"
    assert admin.patch(unknown, json={"offer": "pro"}).status_code == 404
    assert (
        admin.post("/api/admin/users/find", json={"email": "aucun@example.test"}).status_code == 404
    )
    other = _client(admin.app)
    other.post("/api/auth/email/start", json={"email": OLD})
    other.post("/api/auth/email/verify", json={"email": OLD, "code": code_of(mailer, OLD)})
    assert other.patch(url, json={"offer": "pro"}).status_code == 403
    assert other.post("/api/admin/users/find", json={"email": OLD}).status_code == 403
    assert _user(service, OLD).offer == "pro"


# ---------------------------------------------------------------- date des CGU


def test_terms_accepted_at_is_written_by_the_code_path_and_the_new_acceptance(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(tmp_path, migrated_url)
    service.create_user(OLD)
    assert _user(service, OLD).terms_accepted_at is None
    # Connexion par code, case cochée.
    client.post("/api/auth/email/start", json={"email": OLD, "cgu": True})
    client.post(
        "/api/auth/email/verify", json={"email": OLD, "code": code_of(mailer, OLD), "cgu": True}
    )
    first = _user(service, OLD)
    assert first.terms_accepted_at is not None and first.terms_version == TERMS_VERSION
    assert first.accepted_terms_at is None, "le repère des quotas ne bouge pas"
    # Nouvelle acceptation : la date suit.
    with service.session() as db, db.begin():
        db.get(User, first.id).terms_version = "2000-01"  # type: ignore[union-attr]
    assert first.terms_accepted_at is not None
    # Un jour plus tard (la session, d'un jour, reste valable).
    later = first.terms_accepted_at.replace(tzinfo=UTC) + timedelta(days=1)
    service.clock = lambda: later
    assert client.post("/api/auth/terms/accept", json={"version": TERMS_VERSION}).status_code == 204
    again = _user(service, OLD)
    assert again.terms_accepted_at is not None
    assert again.terms_accepted_at.replace(tzinfo=UTC) == later
    assert again.accepted_terms_at is None


def test_terms_accepted_at_is_written_by_the_password_signup(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openplan.api.accounts import passwords

    monkeypatch.setattr(passwords, "fetch_range", lambda _prefix: "0" * 35 + ":0")
    client, service, mailer = accounts_client(
        tmp_path, migrated_url, password_login=True, signup="open"
    )
    new = "nouvelle.kan126b@example.test"
    assert (
        client.post("/api/auth/signup/start", json={"email": new, "cgu": True}).status_code == 202
    )
    done = client.post(
        "/api/auth/signup/verify",
        json={"email": new, "code": code_of(mailer, new), "password": "cerisier en fleur 2026"},
    )
    assert done.status_code == 200, done.text
    user = _user(service, new)
    assert user.terms_accepted_at is not None and user.accepted_terms_at is not None
    assert user.offer == "pro"


# ---------------------------------------------------------------- owner.changed (KAN-145)


def _types(client: TestClient) -> list[str]:
    response = client.get("/api/notifications", params={"tab": "all"})
    assert response.status_code == 200, response.text
    return [line["type"] for line in response.json()["items"]]


def test_the_heir_is_told_when_made_owner(tmp_path: Path, migrated_url: str) -> None:
    owner, m, pid, _service = _team(tmp_path, migrated_url)
    members = owner.get(f"/api/projects/{pid}/members").json()
    editor_id = next(x["user_id"] for x in members if x["role"] == "editor")
    made = owner.patch(f"/api/projects/{pid}/members/{editor_id}", json={"role": "owner"})
    assert made.status_code == 200, made.text
    # La cloche de l'héritier, et de lui seul.
    bell = m["editor"].get("/api/notifications", params={"tab": "all"}).json()["items"]
    line = next(x for x in bell if x["type"] == "owner.changed")
    assert line["mine"] is True and line["project_name"] == "Salon"
    assert "owner.changed" not in _types(owner)
    assert "owner.changed" not in _types(m["viewer"])
    # Le journal d'activité le montre aux membres, filtre « membres » compris.
    activity = m["viewer"].get(f"/api/projects/{pid}/activity", params={"kind": "members"})
    assert any(x["type"] == "owner.changed" for x in activity.json()["items"])
    # Déjà propriétaire : rien de plus.
    again = owner.patch(f"/api/projects/{pid}/members/{editor_id}", json={"role": "owner"})
    assert again.status_code == 200
    bell = m["editor"].get("/api/notifications", params={"tab": "all"}).json()["items"]
    assert [x["type"] for x in bell].count("owner.changed") == 1
