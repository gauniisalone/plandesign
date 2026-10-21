"""KAN-55 : inviter par adresse e-mail, invitation nominative dans la cloche.

Adresses de test sur le domaine réservé `example.test` (le propriétaire reste l'adresse
administrateur des tests). Aucun e-mail réel : la boîte `FakeMailer` des tests."""

from __future__ import annotations

import logging
import statistics
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.mail import FakeMailer, MailMessage
from openplan.api.accounts.service import AccountService
from openplan.api.accounts.sharing import (
    EMAIL_INVITE_DAILY_LIMIT,
    EMAIL_INVITE_REFUSED,
    INVITE_QUOTA,
    INVITE_SENT,
)
from openplan.api.app import create_app
from openplan.api.settings import Settings
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

GILLES = "gilles.kan55@example.test"
NEW = "nouvelle.kan55@example.test"


def _client(app: object) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})  # type: ignore[arg-type]


def _owner(tmp_path: Path, url: str) -> tuple[TestClient, AccountService, FakeMailer, str]:
    owner, service, mailer = accounts_client(tmp_path, url)
    sign_in(owner, mailer, ADMIN)
    assert owner.patch("/api/auth/me", json={"name": "Fanny"}).status_code == 200
    made = owner.post("/api/projects", json={"name": "Grenier"})
    assert made.status_code == 201, made.text
    return owner, service, mailer, str(made.json()["id"])


def _invite(owner: TestClient, pid: str, email: str, role: str = "editor") -> Response:
    done: Response = owner.post(
        f"/api/projects/{pid}/invitations/email", json={"email": email, "role": role}
    )
    return done


def test_mh1_same_answer_with_or_without_an_account(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer, pid = _owner(tmp_path, migrated_url)
    service.create_user(GILLES)
    known = _invite(owner, pid, GILLES)
    unknown = _invite(owner, pid, NEW)
    for answer in (known, unknown):
        assert answer.status_code == 202
        assert answer.json() == {"detail": INVITE_SENT}
    assert known.content == unknown.content
    assert set(known.headers) == set(unknown.headers)
    # Le compte existant : sa cloche, pas d'e-mail. Sans compte : l'e-mail, sans code ni jeton.
    assert mailer.last_to(GILLES) is None
    sent = mailer.last_to(NEW)
    assert sent is not None and not sent.code
    assert "Fanny vous invite sur « Grenier »" in sent.text and "comme éditeur" in sent.text
    assert ORIGIN in sent.text and "#/" not in sent.text and "invitation/" not in sent.text
    # Le temps : médianes de 6 essais alternés, sans écart notable (l'avis part après).
    timings: dict[str, list[float]] = {"known": [], "unknown": []}
    for n in range(6):
        for kind, email in (("known", GILLES), ("unknown", f"autre{n}.kan55@example.test")):
            start = time.perf_counter()
            done = _invite(owner, pid, email)
            timings[kind].append(time.perf_counter() - start)
            assert done.status_code == 202
    gap = abs(statistics.median(timings["known"]) - statistics.median(timings["unknown"]))
    assert gap < 0.05, timings
    # Adresse mal formée : 422, avant toute recherche de compte.
    assert _invite(owner, pid, "pas-une-adresse").status_code == 422


def test_mh2_bell_shows_the_invitation_accept_and_decline(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, service, mailer, pid = _owner(tmp_path, migrated_url)
    other = owner.post("/api/projects", json={"name": "Salon"}).json()["id"]
    service.create_user(GILLES)
    gilles = _client(owner.app)
    sign_in(gilles, mailer, GILLES)
    assert gilles.get("/api/invitations").json() == []
    _invite(owner, pid, GILLES, "editor")
    _invite(owner, other, GILLES, "viewer")
    mine = gilles.get("/api/invitations")
    assert mine.status_code == 200
    lines = {i["project_name"]: i for i in mine.json()}
    assert set(lines) == {"Grenier", "Salon"}
    grenier = lines["Grenier"]
    assert (grenier["inviter_name"], grenier["role"]) == ("Fanny", "editor")
    assert "email" not in grenier and ADMIN not in mine.text and GILLES not in mine.text
    # Un autre compte ne peut ni accepter ni refuser l'invitation de Gilles.
    service.create_user("paul.kan55@example.test")
    paul = _client(owner.app)
    sign_in(paul, mailer, "paul.kan55@example.test")
    for action in ("accept", "decline"):
        stolen = paul.post(f"/api/invitations/{grenier['id']}/{action}")
        assert stolen.status_code == 410 and stolen.json()["detail"] == EMAIL_INVITE_REFUSED
    # Accepter : membre éditeur ; le projet est à lui (le front l'ouvre).
    accepted = gilles.post(f"/api/invitations/{grenier['id']}/accept")
    assert accepted.status_code == 200
    assert accepted.json() == {"project_id": pid, "role": "editor"}
    assert gilles.get(f"/api/projects/{pid}/role").json() == {"role": "editor"}
    assert gilles.post(f"/api/invitations/{grenier['id']}/accept").status_code == 410
    # Les autres membres l'apprennent : « Gilles a rejoint le projet » dans la cloche de Fanny.
    bell = owner.get("/api/notifications").json()["items"]
    assert any(line["type"] == "member.joined" for line in bell)
    # Refuser : l'invitation disparaît de sa cloche et des invitations en attente.
    salon = lines["Salon"]["id"]
    assert gilles.post(f"/api/invitations/{salon}/decline").status_code == 204
    assert gilles.get("/api/invitations").json() == []
    assert owner.get(f"/api/projects/{other}/invitations").json() == []
    assert gilles.get(f"/api/projects/{other}/role").status_code == 404


def test_mh3_without_an_account_the_mail_then_signup_makes_a_member(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, service, mailer, pid = _owner(tmp_path, migrated_url)
    assert service.settings.signup == "invite"  # bêta sur invitation : fermée sinon
    _invite(owner, pid, NEW, "commenter")
    assert mailer.last_to(NEW) is not None
    guest = _client(owner.app)
    asked = guest.post("/api/auth/email/start", json={"email": NEW})
    assert asked.status_code == 202
    verified = guest.post(
        "/api/auth/email/verify",
        json={"email": NEW, "code": code_of(mailer, NEW)},
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["first_login"] is True
    assert guest.get(f"/api/projects/{pid}/role").json() == {"role": "commenter"}
    assert owner.get(f"/api/projects/{pid}/invitations").json() == []
    assert guest.get("/api/invitations").json() == []
    # Témoin : une adresse non invitée ne reçoit rien (inscription fermée).
    nobody = "personne.kan55@example.test"
    assert guest.post("/api/auth/email/start", json={"email": nobody}).status_code == 202
    assert mailer.last_to(nobody) is None


def test_mh4_pending_list_cancel_and_expiry(tmp_path: Path, migrated_url: str) -> None:
    owner, service, mailer, pid = _owner(tmp_path, migrated_url)
    service.create_user(GILLES)
    gilles = _client(owner.app)
    sign_in(gilles, mailer, GILLES)
    _invite(owner, pid, GILLES, "viewer")
    _invite(owner, pid, NEW, "editor")
    pending = owner.get(f"/api/projects/{pid}/invitations").json()
    assert sorted((p["email"], p["role"]) for p in pending) == [
        (GILLES, "viewer"),
        (NEW, "editor"),
    ]
    for p in pending:
        lasted = datetime.fromisoformat(p["expires_at"]) - datetime.fromisoformat(p["created_at"])
        assert lasted == timedelta(days=14)
    # Une nouvelle invitation de la même adresse remplace l'ancienne (une seule en attente).
    _invite(owner, pid, GILLES, "commenter")
    pending = owner.get(f"/api/projects/{pid}/invitations").json()
    assert sorted((p["email"], p["role"]) for p in pending) == [
        (GILLES, "commenter"),
        (NEW, "editor"),
    ]
    # Annuler : elle quitte la liste et la cloche de l'invité.
    first = next(p for p in pending if p["email"] == GILLES)
    assert owner.delete(f"/api/projects/{pid}/invitations/{first['id']}").status_code == 204
    assert gilles.get("/api/invitations").json() == []
    assert gilles.post(f"/api/invitations/{first['id']}/accept").status_code == 410
    # Expiration au bout de 14 jours.
    _invite(owner, pid, GILLES, "editor")
    live = gilles.get("/api/invitations").json()
    assert len(live) == 1
    later = utcnow() + timedelta(days=14, minutes=1)
    service.clock = lambda: later
    assert owner.get(f"/api/projects/{pid}/invitations").json() == []
    assert gilles.get("/api/invitations").json() == []
    assert gilles.post(f"/api/invitations/{live[0]['id']}/accept").status_code == 410
    assert not service.has_email_invitation(NEW)


def test_mh5_twenty_invitations_per_owner_per_day(tmp_path: Path, migrated_url: str) -> None:
    owner, service, _, pid = _owner(tmp_path, migrated_url)
    for n in range(EMAIL_INVITE_DAILY_LIMIT):
        assert _invite(owner, pid, f"q{n}.kan55@example.test").status_code == 202
    refused = _invite(owner, pid, "q-de-trop.kan55@example.test")
    assert refused.status_code == 429
    assert refused.json()["detail"] == INVITE_QUOTA
    assert "20 invitations par jour" in INVITE_QUOTA
    tomorrow = utcnow() + timedelta(days=1, minutes=1)
    service.clock = lambda: tomorrow
    assert _invite(owner, pid, "q-demain.kan55@example.test").status_code == 202


class _BrokenMailer:
    def send(self, message: MailMessage) -> None:
        msg = f"refusé pour {message.to}"  # même une exception bavarde ne doit pas fuiter
        raise RuntimeError(msg)


def test_mh6_addresses_never_logged_nor_shown_to_other_members(
    tmp_path: Path, migrated_url: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    owner, service, mailer, pid = _owner(tmp_path, migrated_url)
    service.create_user(GILLES)
    editor = _client(owner.app)
    sign_in(editor, mailer, GILLES)
    _invite(owner, pid, GILLES, "editor")
    invitation = editor.get("/api/invitations").json()[0]["id"]
    editor.post(f"/api/invitations/{invitation}/accept")
    _invite(owner, pid, NEW, "viewer")
    service.mailer = _BrokenMailer()
    failed = _invite(owner, pid, "panne.kan55@example.test")
    assert failed.status_code == 202
    assert "envoi d'une invitation impossible" in caplog.text
    for address in (NEW, "panne.kan55@example.test", "panne.kan55", "nouvelle.kan55"):
        assert address not in caplog.text
    # Un éditeur (non propriétaire) ne voit pas les invitations en attente…
    assert editor.get(f"/api/projects/{pid}/invitations").status_code == 403
    assert _invite(editor, pid, "x.kan55@example.test").status_code == 403
    # … ni l'adresse dans sa cloche ou le journal d'activité.
    for path in ("/api/notifications?tab=all", f"/api/projects/{pid}/activity"):
        seen = editor.get(path)
        assert seen.status_code == 200 and NEW not in seen.text and "nouvelle" not in seen.text


def test_mh7_local_mode_has_no_email_invitation(tmp_path: Path) -> None:
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local")))
    made = local.post("/api/projects", json={"name": "Grenier"})
    assert made.status_code == 201
    pid = made.json()["id"]
    assert (
        local.post(
            f"/api/projects/{pid}/invitations/email", json={"email": NEW, "role": "viewer"}
        ).status_code
        == 404
    )
    assert local.get("/api/invitations").status_code == 404
