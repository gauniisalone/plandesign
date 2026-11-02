"""KAN-117 : plafond quotidien de tous les e-mails, par classes de priorité.

`login` (codes de connexion, contact du site) va jusqu'au plafond total ; `invitation` s'arrête
avant, pour garder la place des connexions. Au-delà : 503 avec Retry-After et
`X-PlanDesign-Limit: mail-quota`, la même réponse pour toutes les adresses ; rien ne part.
Aucun e-mail réel : la boîte `FakeMailer` des tests."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts.mail import FakeMailer, login_message
from openplan.api.accounts.mail_budget import QUOTA_TEXT, MailBudget, MailQuotaError
from openplan.api.accounts.service import AccountService
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in

KNOWN = "gilles.kan117@example.test"
NEW = "nouvelle.kan117@example.test"
SITE = "https://site.example.org"
CONTACT = {"email": "visiteur@example.org", "message": "Bonjour.", "consent": True}
NOON = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)

Made = tuple[TestClient, AccountService, FakeMailer]


def _quota(response: httpx.Response) -> bool:
    return (
        response.status_code == 503
        and response.headers["X-PlanDesign-Limit"] == "mail-quota"
        and int(response.headers["Retry-After"]) > 0
    )


def _owner(tmp_path: Path, url: str, **overrides: object) -> tuple[Made, str]:
    made = accounts_client(tmp_path, url, **overrides)
    owner, service, mailer = made
    sign_in(owner, mailer, ADMIN)  # 1 e-mail : le code de l'administrateur
    service.create_user(KNOWN)
    project = owner.post("/api/projects", json={"name": "Grenier"})
    assert project.status_code == 201, project.text
    return made, str(project.json()["id"])


# --- réglages ------------------------------------------------------------------------------


def test_defaults_under_the_brevo_free_plan() -> None:
    assert (Settings().mail_daily_total, Settings().mail_daily_invites) == (280, 250)
    env = Settings.from_env(
        {"OPENPLAN_MAIL_DAILY_TOTAL": "1000", "OPENPLAN_MAIL_DAILY_INVITES": "0"}, dotenv=None
    )
    assert (env.mail_daily_total, env.mail_daily_invites) == (1000, 0)
    with pytest.raises(ValueError, match="entier"):
        Settings.from_env({"OPENPLAN_MAIL_DAILY_TOTAL": "beaucoup"}, dotenv=None)


# --- le compteur ---------------------------------------------------------------------------


def test_classes_close_in_priority_order_and_reopen_at_midnight() -> None:
    now = [NOON]
    budget = MailBudget(5, {"invitation": 3}, lambda: now[0])
    assert [budget.take("invitation") for _ in range(4)] == [True, True, True, False]
    with pytest.raises(MailQuotaError) as closed:
        budget.check("invitation")
    assert closed.value.retry_after_s == 12 * 3600  # jusqu'à minuit UTC
    assert closed.value.headers["X-PlanDesign-Limit"] == "mail-quota"
    budget.check("login")  # les connexions passent encore
    assert [budget.take("login") for _ in range(3)] == [True, True, False]
    with pytest.raises(MailQuotaError):
        budget.check("login")
    now[0] = NOON + timedelta(days=1)
    budget.check("login")
    assert budget.take("invitation")


def test_zero_means_no_cap_and_a_class_never_exceeds_the_total() -> None:
    free = MailBudget(0, {"invitation": 0}, lambda: NOON)
    assert all(free.take("invitation") for _ in range(500))
    assert MailBudget(3, {"invitation": 10}, lambda: NOON).limit("invitation") == 3
    assert MailBudget(0, {"invitation": 4}, lambda: NOON).limit("invitation") == 4


def test_alerts_once_without_any_address(caplog: pytest.LogCaptureFixture) -> None:
    budget = MailBudget(10, {"invitation": 9}, lambda: NOON)
    with caplog.at_level(logging.WARNING, logger="openplan.accounts"):
        for _ in range(12):
            budget.take("login")
    alerts = [r.getMessage() for r in caplog.records]
    assert sum("80 %" in a for a in alerts) == 1
    assert sum("« invitation » fermés" in a for a in alerts) == 1
    assert sum("« login » fermés" in a for a in alerts) == 1
    assert "@" not in caplog.text


# --- les routes ----------------------------------------------------------------------------


def test_login_codes_get_a_503_alike_for_every_address(tmp_path: Path, migrated_url: str) -> None:
    (owner, service, mailer), _ = _owner(tmp_path, migrated_url, mail_daily_total=2)
    anyone = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    assert anyone.post("/api/auth/email/start", json={"email": KNOWN}).status_code == 202
    sent = len(mailer.outbox)
    answers = [anyone.post("/api/auth/email/start", json={"email": e}) for e in (KNOWN, NEW, ADMIN)]
    assert all(_quota(a) for a in answers)
    assert {a.json()["detail"] for a in answers} == {QUOTA_TEXT}
    page = anyone.post("/api/auth/login/code", data={"email": KNOWN})
    assert _quota(page) and "réessayez demain" in page.text
    assert len(mailer.outbox) == sent  # rien n'est parti
    assert service.send_mail(login_message(KNOWN, "123456", 15)) is False  # plafond dur


def test_invitations_close_first_and_login_codes_still_pass(
    tmp_path: Path, migrated_url: str
) -> None:
    (owner, _, mailer), pid = _owner(
        tmp_path, migrated_url, mail_daily_total=3, mail_daily_invites=1
    )
    for email in (KNOWN, NEW):  # avec ou sans compte : la même réponse
        refused = owner.post(
            f"/api/projects/{pid}/invitations/email", json={"email": email, "role": "viewer"}
        )
        assert _quota(refused), refused.text
    assert mailer.last_to(NEW) is None
    other = TestClient(owner.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    assert other.post("/api/auth/email/start", json={"email": KNOWN}).status_code == 202
    assert mailer.last_code_to(KNOWN) is not None


def test_invitation_mail_counts(tmp_path: Path, migrated_url: str) -> None:
    (owner, service, mailer), pid = _owner(tmp_path, migrated_url, mail_daily_invites=2)
    done = owner.post(
        f"/api/projects/{pid}/invitations/email", json={"email": NEW, "role": "viewer"}
    )
    assert done.status_code == 202 and mailer.last_to(NEW) is not None
    with pytest.raises(MailQuotaError):  # code de l'administrateur + l'invitation = 2
        service.mail_budget.check("invitation")


def test_site_contact_gets_a_503_when_nothing_can_leave(tmp_path: Path, migrated_url: str) -> None:
    client, service, mailer = accounts_client(
        tmp_path, migrated_url, site_origin=SITE, mail_daily_total=1
    )
    site = TestClient(client.app, base_url=ORIGIN, headers={"Origin": SITE})
    assert site.post("/api/contact", json=CONTACT).status_code == 202
    assert len(mailer.outbox) == 1
    full = site.post("/api/contact", json=CONTACT)
    assert _quota(full) and full.headers["access-control-allow-origin"] == SITE
    assert len(mailer.outbox) == 1
    assert service.mail_budget.limit("login") == 1
