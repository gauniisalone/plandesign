"""KAN-143 : le plafond de projets des comptes inscrits compte le RÔLE propriétaire (celui de la
liste des membres), et non `projects.owner_id`, que « Changer le rôle » ne met pas à jour. Un
projet confié dans l'app ne compte plus pour l'ancien propriétaire et compte pour le nouveau."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from openplan.api import security
from openplan.api.accounts import passwords
from openplan.api.accounts.mail import FakeMailer
from tests.accounts.helpers import ORIGIN, accounts_client, code_of

ZOE = "zoe@example.org"
LEA = "lea@example.org"
GOOD = "cerisier en fleur 2026"


@pytest.fixture(autouse=True)
def simulated_hibp(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_a: object, **_k: object) -> None:
        pytest.fail("appel réseau réel dans un test")

    monkeypatch.setattr(passwords, "fetch_range", lambda _prefix: "0" * 35 + ":0")
    monkeypatch.setattr("httpx.get", no_network)
    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 1000)


def _signup(client: TestClient, mailer: FakeMailer, email: str) -> None:
    assert (
        client.post("/api/auth/signup/start", json={"email": email, "cgu": True}).status_code == 202
    )
    done = client.post(
        "/api/auth/signup/verify",
        json={"email": email, "code": code_of(mailer, email), "password": GOOD},
    )
    assert done.status_code == 200, done.text


def test_a_project_handed_over_in_the_app_moves_to_the_new_owners_cap(
    tmp_path: Path, migrated_url: str
) -> None:
    client, service, mailer = accounts_client(
        tmp_path, migrated_url, password_login=True, signup="open", signup_max_projects=2
    )
    _signup(client, mailer, ZOE)
    made = [client.post("/api/projects", json={}) for _ in range(2)]
    assert all(m.status_code == 201 for m in made)
    capped = client.post("/api/projects", json={})
    assert capped.status_code == 403 and capped.json()["reason"] == "project_cap"

    other = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    _signup(other, mailer, LEA)
    zoe = service.user_by_email(ZOE)
    lea = service.user_by_email(LEA)
    assert zoe is not None and lea is not None
    handed = UUID(made[0].json()["id"])
    # Zoé confie le projet à Léa : Léa propriétaire, puis Zoé éditrice (« Changer le rôle »).
    service.set_member(handed, lea.id, "editor", added_by=zoe.id)
    assert service.change_role(handed, lea.id, "owner")
    assert service.change_role(handed, zoe.id, "editor")

    assert service.owned_project_count(zoe.id) == 1
    assert service.owned_project_count(lea.id) == 1
    # Zoé retrouve une place sous son plafond ; le projet confié compte pour Léa.
    assert client.post("/api/projects", json={}).status_code == 201
    assert other.post("/api/projects", json={}).status_code == 201
    capped_lea = other.post("/api/projects", json={})
    assert capped_lea.status_code == 403 and capped_lea.json()["reason"] == "project_cap"


def test_a_co_owned_project_counts_for_each_owner(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(
        tmp_path, migrated_url, password_login=True, signup="open", signup_max_projects=2
    )
    _signup(client, mailer, ZOE)
    project = UUID(client.post("/api/projects", json={}).json()["id"])
    other = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    _signup(other, mailer, LEA)
    zoe, lea = service.user_by_email(ZOE), service.user_by_email(LEA)
    assert zoe is not None and lea is not None
    service.set_member(project, lea.id, "owner", added_by=zoe.id)
    assert service.owned_project_count(zoe.id) == 1
    assert service.owned_project_count(lea.id) == 1
