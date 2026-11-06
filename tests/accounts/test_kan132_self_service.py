"""KAN-132 : supprimer son compte et exporter ses données depuis Réglages › Compte.

Un test par décision (augus et PO, 6 nov.) : drapeau éteint = 404 ; code par e-mail et adresse
retapée ; héritier choisi, sinon l'éditeur le plus ancien, sinon le membre le plus ancien ;
projet sans autre membre effacé ; administrateur refusé ; export en tâche de fond, lien de
24 heures à usage unique pour le même compte, rien d'un autre membre ; une demande à la fois ;
taille maximale ; purge des archives expirées."""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from openplan.api.accounts import account_export
from openplan.api.accounts.account_export import LINK_TTL, purge_exports
from openplan.api.accounts.models import Member, ProjectRow, User
from openplan.api.accounts.service import AccountService
from openplan.api.app import ExportsInsideDataError
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, code_of, sign_in

LEA = "lea.martin@example.org"
MARIE = "marie.durand@example.org"
PAUL = "paul.bernard@example.org"
ROUTES = (
    ("GET", "/api/account/delete"),
    ("POST", "/api/account/delete/code"),
    ("POST", "/api/account/delete"),
    ("POST", "/api/account/export"),
    ("GET", "/api/account/export/download"),
)


def _client(tmp_path: Path, url: str, **kw: Any) -> tuple[TestClient, AccountService, Any]:
    kw.setdefault("account_self_service", True)
    kw.setdefault("exports_dir", tmp_path / "exports")  # hors du dossier des données
    return accounts_client(tmp_path, url, signup="open", **kw)


def _other(client: TestClient, mailer: Any, email: str) -> TestClient:
    other = TestClient(client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    sign_in(other, mailer, email)
    return other


def _invite(owner: TestClient, member: TestClient, pid: str, role: str) -> None:
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": role}).json()["token"]
    done = member.post("/api/auth/invitation/accept", json={"token": token})
    assert done.status_code == 200, done.text


@pytest.fixture
def team(tmp_path: Path, migrated_url: str) -> dict[str, Any]:
    """Léa possède « Seule » (sans autre membre), « Atelier » (Marie lectrice puis Paul
    éditeur) et « Salon » (Marie lectrice seulement) ; Marie possède « Bureau », où Léa
    commente."""
    lea, accounts, mailer = _client(tmp_path, migrated_url)
    sign_in(lea, mailer, LEA)
    marie, paul = _other(lea, mailer, MARIE), _other(lea, mailer, PAUL)
    ids = {
        n: lea.post("/api/projects", json={"name": n}).json()["id"]
        for n in ("Seule", "Atelier", "Salon")
    }
    _invite(lea, marie, ids["Atelier"], "viewer")
    _invite(lea, paul, ids["Atelier"], "editor")
    _invite(lea, marie, ids["Salon"], "viewer")
    ids["Bureau"] = marie.post("/api/projects", json={"name": "Bureau"}).json()["id"]
    _invite(marie, lea, ids["Bureau"], "commenter")
    users = {}
    with accounts.session() as db:
        for row in db.scalars(select(User)):
            users[row.email] = row.id
    return {
        "lea": lea,
        "marie": marie,
        "paul": paul,
        "accounts": accounts,
        "mailer": mailer,
        "ids": ids,
        "users": users,
        "tmp": tmp_path,
    }


def _owner_of(accounts: AccountService, pid: str) -> UUID:
    with accounts.session() as db:
        row = db.get(ProjectRow, UUID(pid))
        assert row is not None
        return row.owner_id


def _role(accounts: AccountService, pid: str, user: UUID) -> str | None:
    with accounts.session() as db:
        return db.scalar(
            select(Member.role).where(Member.project_id == UUID(pid), Member.user_id == user)
        )


# --- drapeau ------------------------------------------------------------------------


def test_flag_off_every_route_answers_404(tmp_path: Path, migrated_url: str) -> None:
    client, _, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(client, mailer, LEA)
    for method, path in ROUTES:
        assert client.request(method, path).status_code == 404, path


# --- suppression ----------------------------------------------------------------------


def test_preview_lists_what_goes_and_the_default_heirs(team: dict[str, Any]) -> None:
    r = team["lea"].get("/api/account/delete")
    assert r.status_code == 200, r.text
    body, ids, users = r.json(), team["ids"], team["users"]
    assert [p["name"] for p in body["deleted"]] == ["Seule"]
    heirs = {p["name"]: p["heir"] for p in body["shared"]}
    # l'éditeur le plus ancien, même arrivé après une lectrice ; sinon le membre le plus ancien
    assert heirs == {"Atelier": str(users[PAUL]), "Salon": str(users[MARIE])}
    atelier = next(p for p in body["shared"] if p["id"] == ids["Atelier"])
    assert {m["role"] for m in atelier["members"]} == {"viewer", "editor"}
    assert all("@" not in m["name"] or "***" in m["name"] for m in atelier["members"])
    assert body["memberships"] == 1 and body["refusal"] is None


def test_delete_needs_the_code_and_the_retyped_address(team: dict[str, Any]) -> None:
    lea, mailer = team["lea"], team["mailer"]
    assert lea.post("/api/account/delete/code").status_code == 202
    message = mailer.last_code_to(LEA)
    assert message is not None and "supprimer votre compte" in message.text.lower()
    wrong = lea.post("/api/account/delete", json={"code": message.code, "email": MARIE})
    assert wrong.status_code == 422
    bad = lea.post("/api/account/delete", json={"code": "000000", "email": LEA})
    assert bad.status_code == 400
    assert lea.get("/api/auth/me").status_code == 200, "rien n'est effacé"


def test_delete_hands_shared_projects_over_and_erases_the_rest(team: dict[str, Any]) -> None:
    lea, accounts, ids, users = team["lea"], team["accounts"], team["ids"], team["users"]
    folder = team["tmp"] / "data" / "projects" / ids["Seule"]
    assert folder.is_dir()
    lea.post("/api/account/delete/code")
    choice = {ids["Atelier"]: str(users[MARIE])}  # Marie choisie, bien que lectrice
    done = lea.post(
        "/api/account/delete",
        json={"code": code_of(team["mailer"], LEA), "email": LEA.upper(), "heirs": choice},
    )
    assert done.status_code == 204, done.text
    assert "pd_session" in done.headers.get("set-cookie", "")
    with accounts.session() as db:
        assert db.scalar(select(User).where(User.email == LEA)) is None
        assert db.get(ProjectRow, UUID(ids["Seule"])) is None
    assert not folder.exists()
    assert _owner_of(accounts, ids["Atelier"]) == users[MARIE]
    assert _role(accounts, ids["Atelier"], users[MARIE]) == "owner"
    assert _owner_of(accounts, ids["Salon"]) == users[MARIE], "le membre le plus ancien"
    assert _role(accounts, ids["Bureau"], users[LEA]) is None
    assert team["marie"].get(f"/api/projects/{ids['Bureau']}").status_code == 200
    assert lea.get("/api/auth/me").status_code == 401


def test_an_heir_who_is_not_a_member_is_refused(team: dict[str, Any]) -> None:
    lea, ids, users = team["lea"], team["ids"], team["users"]
    lea.post("/api/account/delete/code")
    r = lea.post(
        "/api/account/delete",
        json={
            "code": code_of(team["mailer"], LEA),
            "email": LEA,
            "heirs": {ids["Salon"]: str(users[PAUL])},
        },
    )
    assert r.status_code == 409 and "plus membre" in r.json()["detail"]
    assert lea.get("/api/auth/me").status_code == 200


def test_an_administrator_cannot_delete_itself_here(tmp_path: Path, migrated_url: str) -> None:
    admin, _, mailer = _client(tmp_path, migrated_url)
    sign_in(admin, mailer, ADMIN)
    assert admin.get("/api/account/delete").json()["refusal"]
    assert admin.post("/api/account/delete/code").status_code == 403
    r = admin.post("/api/account/delete", json={"code": "123456", "email": ADMIN})
    assert r.status_code == 403


# --- export ---------------------------------------------------------------------------


def _link(mailer: Any, email: str) -> str:
    message = mailer.last_to(email)
    assert message is not None
    found = re.search(r"(/api/account/export/download\?t=\S+)", message.text)
    assert found, message.text
    return found.group(1)


def _comment(client: TestClient, pid: str, text: str) -> None:
    r = client.post(
        f"/api/projects/{pid}/comments", json={"target": {"at": {"x": 0, "y": 0}}, "text": text}
    )
    assert r.status_code in (200, 201), r.text


def test_export_holds_the_account_its_projects_and_only_its_contributions(
    team: dict[str, Any],
) -> None:
    lea, marie, ids = team["lea"], team["marie"], team["ids"]
    _comment(marie, ids["Bureau"], "Message de Marie")
    _comment(lea, ids["Bureau"], "Message de Léa")
    r = lea.post("/api/account/export")
    assert r.status_code == 202, r.text
    assert lea.post("/api/account/export").status_code == 409, "une demande à la fois"
    link = _link(team["mailer"], LEA)
    stranger = TestClient(lea.app, base_url=ORIGIN)
    assert stranger.get(link).status_code == 401, "connecté d'abord"
    assert marie.get(link).status_code == 404, "le même compte seulement"
    got = lea.get(link)
    assert got.status_code == 200 and got.headers["content-type"] == "application/zip"
    assert got.headers["referrer-policy"] == "no-referrer", "le jeton ne part pas en Referer"
    token = link.split("t=", 1)[1]
    assert token not in stranger.get(link).text, "la page de connexion ne reprend pas le jeton"
    archive = zipfile.ZipFile(io.BytesIO(got.content))
    names = archive.namelist()
    account = archive.read("compte.txt").decode()
    assert LEA in account and "Bureau" in account and MARIE not in account
    assert sum(n.startswith("projets/") for n in names) == 3
    contributions = [n for n in names if n.startswith("contributions/")]
    assert len(contributions) == 1
    text = archive.read(contributions[0]).decode()
    assert "Message de Léa" in text and "Message de Marie" not in text
    assert {m["author"] for m in json.loads(text)["comments"]} == {str(team["users"][LEA])}
    assert "evenements-d-usage.csv" in names
    root = team["accounts"].settings.exports_path
    assert not list(root.glob("*.zip")), "l'archive est effacée juste après le téléchargement"
    assert not list(root.glob("*.json")), "et sa fiche avec"
    assert lea.get(link).status_code == 404, "usage unique"


def test_an_export_too_large_fails_by_mail(
    team: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(account_export, "MAX_BYTES", 0)
    lea = team["lea"]
    (team["tmp"] / "data" / "projects" / team["ids"]["Seule"] / "lourd.bin").write_bytes(b"x")
    assert lea.post("/api/account/export").status_code == 202
    message = team["mailer"].last_to(LEA)
    assert message is not None and "pas pu être préparée" in message.text
    assert lea.post("/api/account/export").status_code == 202, "un échec ne bloque pas"


def test_exports_inside_the_backed_up_data_refuse_to_start(
    tmp_path: Path, migrated_url: str
) -> None:
    """Revue Infra : sous OPENPLAN_DATA_DIR (sauvegardé 6 mois par restic), les archives
    seraient copiées dans les sauvegardes ; drapeau posé, l'application refuse de démarrer."""
    with pytest.raises(ExportsInsideDataError):
        _client(tmp_path, migrated_url, exports_dir=None)
    with pytest.raises(ExportsInsideDataError):
        _client(tmp_path, migrated_url, exports_dir=tmp_path / "data" / "x")
    off, _, _ = accounts_client(tmp_path, migrated_url)  # drapeau éteint : rien à refuser
    assert off.get("/api/health").status_code == 200


def test_error_reports_never_carry_the_link_token() -> None:
    """Revue Infra : une erreur dans la route du lien n'envoie pas `t=` à GlitchTip."""
    from openplan.monitoring import before_send

    url = f"{ORIGIN}/api/account/export/download?t=SECRETJETON"
    event = {
        "request": {"url": url, "query_string": "t=SECRETJETON", "method": "GET"},
        "breadcrumbs": {"values": [{"type": "http", "data": {"url": url}}]},
    }
    assert "SECRETJETON" not in json.dumps(before_send(event))


def test_the_daily_purge_erases_expired_archives(team: dict[str, Any]) -> None:
    lea, accounts = team["lea"], team["accounts"]
    assert lea.post("/api/account/export").status_code == 202
    root = accounts.settings.exports_path
    assert list(root.glob("*.zip")) and list(root.glob("*.json"))
    now = datetime.now(UTC)
    assert purge_exports(root, now) == 0, "un lien encore valable reste"
    assert purge_exports(root, now + LINK_TTL + timedelta(minutes=1)) == 1
    assert not list(root.iterdir())
