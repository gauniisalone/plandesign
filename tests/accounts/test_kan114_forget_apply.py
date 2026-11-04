"""KAN-114 : `openplan accounts forget --apply`, l'effacement pour de bon (décisions du PO du
30 oct., confirmées par augus). Il refait le plan et ses refus, fait retaper l'adresse, puis agit
dans une transaction : sessions révoquées d'abord, demandes de rendu en attente annulées
(`cancel_pending_renders`, KAN-115) AVANT le `DELETE users`, codes et invitations effacés par
adresse, `--transfer-to` qui réécrit `owner_id` et le rôle `owner`, fichiers des projets des
autres jamais réécrits. Un second `--apply` ne trouve « rien à faire ». La sortie masque
l'adresse. Aucune donnée réelle."""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, event, func, select

from openplan.api.accounts.db import utcnow
from openplan.api.accounts.events import EventLog, load_secret
from openplan.api.accounts.forget import (
    ForgetPlan,
    ForgetRefusedError,
    apply_forget,
    plan_forget,
    run_apply,
)
from openplan.api.accounts.models import (
    AuthSession,
    Event,
    Invitation,
    LoginToken,
    Member,
    ProjectRow,
    RenderRequest,
    User,
)
from openplan.api.accounts.repo import AccountsRepo
from openplan.cli.main import main
from tests.accounts import test_kan114_forget as base
from tests.accounts.test_kan114_forget import (
    ADMIN,
    BRUNO,
    CHLOE,
    LEA,
    OTHERS,
    _project,
    _snapshot,
    _token,
)

world = base.world
"""Le monde du plan (`test_kan114_forget`) : Léa, Bruno, Chloé, un administrateur."""
ZOE = "zoe@example.org"


def _plan(w: dict[str, Any], email: str = LEA, **kw: Any) -> ForgetPlan:
    return plan_forget(w["repo"], email, projects_dir=w["projects"], data_dir=w["data"], **kw)


def _run(w: dict[str, Any], plan: ForgetPlan, typed: str | None, lines: list[str]) -> int:
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        if typed is None:
            raise EOFError
        return typed

    code = run_apply(
        w["repo"], plan, projects_dir=w["projects"], data_dir=w["data"], ask=ask, say=lines.append
    )
    lines.extend(f"[demandé] {p}" for p in asked)
    return code


def _count(engine: Engine, model: type[Any], *where: Any) -> int:
    with engine.connect() as conn:
        return int(conn.scalar(select(func.count()).select_from(model).where(*where)) or 0)


def _files_of(projects: Path, pid: UUID) -> dict[str, str]:
    folder = projects / str(pid)
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _render(db: Any, user: UUID, project: UUID, state: str) -> RenderRequest:
    now = utcnow()
    row = RenderRequest(
        user_id=user,
        project_id=project,
        level_id=uuid4(),
        camera_id=uuid4(),
        state=state,
        created_at=now,
        expires_at=now + timedelta(hours=6),
        lease_id=uuid4() if state == "taken" else None,
        taken_at=now if state == "taken" else None,
    )
    db.add(row)
    return row


def test_apply_with_transfer_erases_the_account_and_hands_over_its_projects(
    world: dict[str, Any],
) -> None:
    """Décisions 1, 2, 6 : transfert (owner_id ET rôle owner), co-possédé passé à l'autre
    propriétaire, projet seul supprimé avec son dossier, rôle retiré chez Bruno, fichiers des
    projets qui restent identiques octet pour octet, codes et invitations par adresse effacés,
    événements effacés ; les autres comptes et leurs traces restent."""
    engine, users, projects = world["engine"], world["users"], world["projects"]
    lea, bruno, chloe = users[LEA], users[BRUNO], users[CHLOE]
    kept = {
        pid: _files_of(projects, pid) for pid in (world["co"], world["shared"], world["theirs"])
    }
    lines: list[str] = []
    assert _run(world, _plan(world, transfer_to=BRUNO), " Lea@Example.org ", lines) == 0
    with engine.connect() as conn:
        assert conn.scalar(select(func.count()).select_from(User).where(User.id == lea)) == 0
        owner_of = dict(conn.execute(select(ProjectRow.id, ProjectRow.owner_id)).all())
        roles = {(r.project_id, r.user_id): r.role for r in conn.execute(select(Member)).all()}
    assert owner_of[world["shared"]] == bruno and roles[(world["shared"], bruno)] == "owner"
    assert owner_of[world["co"]] == chloe and roles[(world["co"], chloe)] == "owner"
    assert roles[(world["shared"], chloe)] == "viewer"
    assert world["solo"] not in owner_of and not (projects / str(world["solo"])).exists()
    assert not any(uid == lea for _, uid in roles)
    assert owner_of[world["theirs"]] == bruno
    for pid, files in kept.items():  # décision 2 : fichiers des projets des autres intacts
        assert _files_of(projects, pid) == files
    assert _count(engine, AuthSession, AuthSession.user_id == lea) == 0
    assert _count(engine, LoginToken, LoginToken.email == LEA) == 0
    assert _count(engine, LoginToken, LoginToken.email == BRUNO) == 1
    assert _count(engine, Invitation, Invitation.email == LEA) == 0
    assert _count(engine, Invitation, Invitation.created_by == lea) == 0
    log = EventLog(engine, load_secret(world["data"]))
    assert _count(engine, Event, Event.actor == log.pseudonym(lea)) == 0
    assert _count(engine, Event, Event.actor == log.pseudonym(bruno)) == 1
    assert any(line.startswith("Effacé : 2 session(s) révoquée(s)") for line in lines)


def test_a_second_apply_has_nothing_to_do(world: dict[str, Any]) -> None:
    lines: list[str] = []
    assert _run(world, _plan(world, delete_shared=True), LEA, lines) == 0
    before = _snapshot(world["engine"], world["data"])
    again: list[str] = []
    assert _run(world, _plan(world, delete_shared=True), LEA, again) == 0
    assert again == ["Rien à faire : aucun compte, code ni invitation pour cette adresse."]
    assert not apply_forget(
        world["repo"], _plan(world), projects_dir=world["projects"], data_dir=world["data"]
    ).done
    assert _snapshot(world["engine"], world["data"]) == before


def test_shared_projects_refused_then_deleted(world: dict[str, Any]) -> None:
    """Projet partagé : refus sans choix (rien n'est écrit, rien n'est demandé), puis
    `--delete-shared` : supprimé avec son dossier, ses membres et ses invitations."""
    before = _snapshot(world["engine"], world["data"])
    lines: list[str] = []
    assert _run(world, _plan(world), LEA, lines) == 1
    assert "Rien n'est écrit." in lines and not any("[demandé]" in x for x in lines)
    assert _snapshot(world["engine"], world["data"]) == before
    with pytest.raises(ForgetRefusedError, match="refus"):
        apply_forget(
            world["repo"], _plan(world), projects_dir=world["projects"], data_dir=world["data"]
        )
    assert _snapshot(world["engine"], world["data"]) == before
    assert _run(world, _plan(world, delete_shared=True), LEA, []) == 0
    shared = world["shared"]
    assert _count(world["engine"], ProjectRow, ProjectRow.id == shared) == 0
    assert _count(world["engine"], Member, Member.project_id == shared) == 0
    assert not (world["projects"] / str(shared)).exists()
    assert _count(world["engine"], ProjectRow, ProjectRow.id == world["co"]) == 1


@pytest.mark.parametrize("case", ["admin_email", "last_admin", "transfer_to_viewer"])
def test_apply_keeps_every_refusal_of_the_dry_run(world: dict[str, Any], case: str) -> None:
    """Décision 4 (OPENPLAN_ADMIN_EMAIL), dernier administrateur, transfert à un non-éditeur :
    --apply refait les contrôles du plan et n'écrit rien."""
    plan = {
        "admin_email": lambda: _plan(world, BRUNO, admin_email=" Bruno@Example.org "),
        "last_admin": lambda: _plan(world, ADMIN),
        "transfer_to_viewer": lambda: _plan(world, transfer_to=CHLOE),
    }[case]()
    before = _snapshot(world["engine"], world["data"])
    lines: list[str] = []
    assert _run(world, plan, plan.email, lines) == 1
    assert any("REFUS" in x for x in lines) and not any("[demandé]" in x for x in lines)
    assert _snapshot(world["engine"], world["data"]) == before


def test_the_command_refuses_the_account_named_by_admin_email(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENPLAN_DOTENV", "")
    monkeypatch.setenv("OPENPLAN_DATABASE_URL", str(world["url"]))
    monkeypatch.setenv("OPENPLAN_DATA_DIR", str(world["data"]))
    monkeypatch.setenv("OPENPLAN_ADMIN_EMAIL", BRUNO)
    monkeypatch.setattr("builtins.input", lambda _p: BRUNO)
    before = _snapshot(world["engine"], world["data"])
    assert main(["accounts", "forget", BRUNO, "--apply"]) == 1
    out = capsys.readouterr().out
    assert "OPENPLAN_ADMIN_EMAIL" in out and BRUNO not in out
    assert _snapshot(world["engine"], world["data"]) == before


def test_sessions_first_then_pending_renders_cancelled_before_delete_users(
    world: dict[str, Any],
) -> None:
    """Décision 5 et règle du 3 nov. : dans la transaction, la révocation des sessions est la
    première écriture ; l'annulation des demandes en attente vient AVANT `DELETE FROM users`.
    La demande en attente passe en `expired` sans lanceur ; celle prise par le poste garde son
    état et son bail ; le rendu fait reste au projet (« Ancien membre »)."""
    engine: Engine = world["engine"]
    lea, theirs = world["users"][LEA], world["theirs"]
    with world["repo"].session() as db, db.begin():
        pending = _render(db, lea, theirs, "pending")
        taken = _render(db, lea, theirs, "taken")
        done = _render(db, lea, theirs, "done")
        db.flush()
        ids = pending.id, taken.id, done.id, taken.lease_id
    writes: list[str] = []

    def spy(_c: Any, _cur: Any, statement: str, *_a: Any) -> None:
        head = statement.lstrip().upper()
        if head.startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(" ".join(head.split()[:3]))

    event.listen(engine, "before_cursor_execute", spy)
    try:
        assert _run(world, _plan(world, transfer_to=BRUNO), LEA, []) == 0
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert writes[0] == "DELETE FROM SESSIONS"
    cancel = writes.index("UPDATE RENDER_REQUESTS SET")
    assert 0 < cancel < writes.index("DELETE FROM USERS")
    assert writes[-1] == "DELETE FROM USERS"
    with engine.connect() as conn:
        rows = {r.id: r for r in conn.execute(select(RenderRequest)).all()}
    assert rows[ids[0]].state == "expired" and rows[ids[0]].finished_at is not None
    assert rows[ids[1]].state == "taken" and rows[ids[1]].lease_id == ids[3]
    assert rows[ids[2]].state == "done"
    assert all(rows[i].user_id is None for i in ids[:3])


def test_confirmation_by_retyping_the_address(world: dict[str, Any]) -> None:
    before = _snapshot(world["engine"], world["data"])
    for typed in (None, "", BRUNO, "lea@example.com"):
        lines: list[str] = []
        assert _run(world, _plan(world, transfer_to=BRUNO), typed, lines) == 1
        assert "Confirmation manquée : rien n'est écrit." in lines
        assert "[demandé] Pour confirmer, retapez l'adresse du compte : " in lines
    assert _snapshot(world["engine"], world["data"]) == before


def test_the_output_and_the_log_never_show_the_address(
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("OPENPLAN_DOTENV", "")
    monkeypatch.setenv("OPENPLAN_DATABASE_URL", str(world["url"]))
    monkeypatch.setenv("OPENPLAN_DATA_DIR", str(world["data"]))
    monkeypatch.delenv("OPENPLAN_ADMIN_EMAIL", raising=False)
    monkeypatch.setattr("builtins.input", lambda _p: LEA)
    caplog.set_level(logging.INFO)
    assert main(["accounts", "forget", LEA, "--apply", "--transfer-to", BRUNO]) == 0
    assert main(["accounts", "forget", LEA, "--apply", "--transfer-to", BRUNO]) == 0
    out = capsys.readouterr().out
    assert "l***@example.org (--apply)" in out and "Rien à faire" in out
    assert "Ancien membre" in out
    for address in (LEA, *OTHERS):
        assert address not in out and address not in caplog.text
    assert str(world["users"][LEA])[:8] in caplog.text  # trace par pseudonyme
    assert "sqlite" not in out


def test_codes_and_invitations_of_an_address_without_account(
    world: dict[str, Any], tmp_path: Path
) -> None:
    """Décision 6 : sans compte, les codes de connexion et les invitations nominatives de
    l'adresse sont effacés par adresse ; puis « rien à faire »."""
    engine, repo = world["engine"], world["repo"]
    now = utcnow()
    with repo.session() as db, db.begin():
        db.add(LoginToken(email=ZOE, token_hash=_token(70), created_at=now, expires_at=now))
        db.add(
            Invitation(
                project_id=world["theirs"],
                role="viewer",
                token_hash=_token(71),
                created_by=world["users"][BRUNO],
                created_at=now,
                expires_at=now,
                email=ZOE,
            )
        )
    plan = _plan(world, ZOE)
    assert plan.user_id is None and plan.possible
    assert _run(world, plan, ZOE, []) == 0
    assert _count(engine, LoginToken, LoginToken.email == ZOE) == 0
    assert _count(engine, Invitation, Invitation.email == ZOE) == 0
    assert _count(engine, LoginToken) == 3  # ceux de Léa et de Bruno restent
    again: list[str] = []
    assert _run(world, _plan(world, ZOE), ZOE, again) == 0 and "Rien à faire" in again[0]


def test_a_lone_account_is_erased_with_its_project(world: dict[str, Any]) -> None:
    """Un compte seul : son projet et son dossier partent, rien d'autre ne bouge."""
    repo: AccountsRepo = world["repo"]
    zoe = repo.create_user(ZOE).id
    pid = _project(repo, world["projects"], zoe, "Véranda", 10)
    before = {p: _files_of(world["projects"], p) for p in (world["solo"], world["theirs"])}
    assert _run(world, _plan(world, ZOE), ZOE, []) == 0
    assert _count(world["engine"], User, User.id == zoe) == 0
    assert _count(world["engine"], ProjectRow, ProjectRow.id == pid) == 0
    assert not (world["projects"] / str(pid)).exists()
    assert {p: _files_of(world["projects"], p) for p in before} == before
