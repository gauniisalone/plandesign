"""KAN-114 : `openplan accounts forget`, le plan seulement (`--dry-run`). Tout est compté et
listé ; RIEN n'est écrit : chaque table de la base et chaque fichier du dossier des données sont
identiques avant et après, et la clé des pseudonymes n'est jamais créée. Refus : projets partagés
sans choix, membre non éditeur pour --transfer-to, dernier administrateur, compte désigné par
OPENPLAN_ADMIN_EMAIL. Aucune autre adresse que celles tapées n'est affichée."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select

from openplan.api.accounts.db import make_engine, utcnow
from openplan.api.accounts.events import SECRET_FILE, EventLog, load_secret
from openplan.api.accounts.forget import ForgetRefusedError, describe, plan_forget
from openplan.api.accounts.models import (
    AuthSession,
    Base,
    Invitation,
    LoginToken,
    RenderRequest,
    ShareLink,
)
from openplan.api.accounts.repo import AccountsRepo
from openplan.cli.main import main

LEA = "lea@example.org"
BRUNO = "bruno@example.org"
CHLOE = "chloe@example.org"
ADMIN = "admin@example.org"
OTHERS = (BRUNO, CHLOE, ADMIN)


def _token(n: int) -> str:
    return hashlib.sha256(f"{uuid4()}{n}".encode()).hexdigest()


def _project(repo: AccountsRepo, projects: Path, owner: UUID, name: str, size: int) -> UUID:
    pid = uuid4()
    repo.add_project(pid, owner, name)
    folder = projects / str(pid)
    (folder / "images").mkdir(parents=True)
    (folder / "project.json").write_text("{}")
    (folder / "images" / "a.jpg").write_bytes(b"x" * size)
    return pid


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> dict[str, object]:
    """Léa : deux sessions, deux codes, trois événements, une invitation créée, une reçue ; un
    projet à elle seule, un co-possédé avec Chloé, un partagé avec Bruno éditeur et Chloé
    lectrice ; lectrice dans le projet de Bruno. Un administrateur."""
    engine = make_engine(migrated_url)
    repo = AccountsRepo(engine)
    data = tmp_path / "data"
    projects = data / "projects"
    users = {e: repo.create_user(e, is_admin=e == ADMIN).id for e in (LEA, *OTHERS)}
    lea, bruno, chloe = users[LEA], users[BRUNO], users[CHLOE]
    solo = _project(repo, projects, lea, "Grenier", 2_000_000)
    co = _project(repo, projects, lea, "Salon", 10)
    repo.set_member(co, chloe, "owner", added_by=lea)
    shared = _project(repo, projects, lea, "Cuisine", 10)
    repo.set_member(shared, bruno, "editor", added_by=lea)
    repo.set_member(shared, chloe, "viewer", added_by=lea)
    theirs = _project(repo, projects, bruno, "Atelier", 10)
    repo.set_member(theirs, lea, "viewer", added_by=bruno)
    now = utcnow()
    with repo.session() as db, db.begin():
        for n in range(2):
            db.add(
                AuthSession(
                    user_id=lea,
                    token_hash=_token(n),
                    created_at=now,
                    last_used_at=now,
                    expires_at=now + timedelta(days=30),
                )
            )
            db.add(LoginToken(email=LEA, token_hash=_token(10 + n), created_at=now, expires_at=now))
        db.add(LoginToken(email=BRUNO, token_hash=_token(20), created_at=now, expires_at=now))
        db.add(
            Invitation(
                project_id=shared,
                role="viewer",
                token_hash=_token(30),
                created_by=lea,
                created_at=now,
                expires_at=now,
            )
        )
        db.add(
            Invitation(
                project_id=theirs,
                role="editor",
                token_hash=_token(31),
                created_by=bruno,
                created_at=now,
                expires_at=now,
                email=LEA,
            )
        )
    log = EventLog(engine, load_secret(data))
    for name in ("active", "project_created", "plan_built"):
        log.emit(name, lea)
    log.emit("active", bruno)
    return {
        "engine": engine,
        "repo": repo,
        "data": data,
        "projects": projects,
        "users": users,
        "solo": solo,
        "co": co,
        "shared": shared,
        "theirs": theirs,
        "url": migrated_url,
    }


def _snapshot(engine: Engine, data: Path) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Chaque ligne de chaque table, et l'empreinte de chaque fichier des données."""
    rows: dict[str, list[str]] = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            rows[table.name] = sorted(str(tuple(r)) for r in conn.execute(select(table)))
    files = {
        str(p.relative_to(data)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(data.rglob("*"))
        if p.is_file()
    }
    return rows, files


def _plan(world: dict[str, object], email: str = LEA, **kw: object):  # type: ignore[no-untyped-def]
    return plan_forget(
        world["repo"],  # type: ignore[arg-type]
        email,
        projects_dir=world["projects"],  # type: ignore[arg-type]
        data_dir=world["data"],  # type: ignore[arg-type]
        **kw,  # type: ignore[arg-type]
    )


def test_the_plan_counts_everything_and_writes_nothing(world: dict[str, object]) -> None:
    before = _snapshot(world["engine"], world["data"])  # type: ignore[arg-type]
    plan = _plan(world)
    assert plan.user_id == world["users"][LEA]  # type: ignore[index]
    assert (plan.sessions, plan.login_codes, plan.events) == (2, 2, 3)
    assert (plan.invitations_created, plan.invitations_to_address) == (1, 1)
    assert [m[0] for m in plan.memberships] == [world["theirs"]]
    assert [p.id for p in plan.solo] == [world["solo"]] and plan.solo[0].size >= 2_000_000
    assert [p.id for p in plan.co_owned] == [world["co"]]
    assert plan.co_owned[0].co_owner == world["users"][CHLOE]  # type: ignore[index]
    assert [p.id for p in plan.shared] == [world["shared"]] and plan.shared[0].others == 2
    assert not plan.possible and "--transfer-to" in plan.refusals[0]
    assert _snapshot(world["engine"], world["data"]) == before  # type: ignore[arg-type]


def test_the_choice_for_shared_projects(world: dict[str, object]) -> None:
    before = _snapshot(world["engine"], world["data"])  # type: ignore[arg-type]
    to_bruno = _plan(world, transfer_to=BRUNO)
    assert to_bruno.possible and to_bruno.shared_action == "transfer"
    assert to_bruno.transfer_to == world["users"][BRUNO]  # type: ignore[index]
    to_chloe = _plan(world, transfer_to=CHLOE)  # lectrice : refusée
    assert not to_chloe.possible and "pas membre éditeur" in to_chloe.refusals[0]
    nobody = _plan(world, transfer_to="personne@example.org")
    assert not nobody.possible and "aucun autre compte" in nobody.refusals[0]
    deleted = _plan(world, delete_shared=True)
    assert deleted.possible and deleted.shared_action == "delete"
    with pytest.raises(ForgetRefusedError, match="s'excluent"):
        _plan(world, transfer_to=BRUNO, delete_shared=True)
    assert _snapshot(world["engine"], world["data"]) == before  # type: ignore[arg-type]


def test_admins_are_protected(world: dict[str, object]) -> None:
    last = _plan(world, ADMIN)
    assert not last.possible and any("dernier administrateur" in r for r in last.refusals)
    named = _plan(world, BRUNO, admin_email=" Bruno@Example.org ")
    assert not named.possible and any("OPENPLAN_ADMIN_EMAIL" in r for r in named.refusals)


def test_an_address_without_account_still_has_its_codes_counted(
    world: dict[str, object],
) -> None:
    repo: AccountsRepo = world["repo"]  # type: ignore[assignment]
    with repo.session() as db, db.begin():
        now = utcnow()
        db.add(
            LoginToken(
                email="zoe@example.org", token_hash=_token(40), created_at=now, expires_at=now
            )
        )
    plan = _plan(world, " ZOE@example.org ")
    assert plan.user_id is None and plan.login_codes == 1 and plan.possible
    with pytest.raises(ForgetRefusedError, match="vide"):
        _plan(world, "   ")


def test_the_plan_counts_the_public_links_of_the_account(world: dict[str, object]) -> None:
    """KAN-101 : les liens publics de rendus créés par le compte (actifs ou révoqués) sont
    comptés et nommés ; ceux des autres ne le sont pas ; rien n'est écrit."""
    repo: AccountsRepo = world["repo"]  # type: ignore[assignment]
    users: dict[str, UUID] = world["users"]  # type: ignore[assignment]
    now = utcnow()
    with repo.session() as db, db.begin():
        for who, project in ((LEA, world["solo"]), (BRUNO, world["theirs"])):
            render = RenderRequest(
                user_id=users[who],
                project_id=project,
                level_id=uuid4(),
                camera_id=uuid4(),
                state="done",
                created_at=now,
                expires_at=now,
            )
            db.add(render)
            db.flush()
            for n, revoked in enumerate((None, now)):
                db.add(
                    ShareLink(
                        token_hash=_token(50 + n + (10 if who == BRUNO else 0)),
                        project_id=project,
                        request_id=render.id,
                        created_by=users[who],
                        created_at=now,
                        expires_at=now + timedelta(days=30),
                        revoked_at=revoked,
                        show_project_name=True,
                    )
                )
    before = _snapshot(world["engine"], world["data"])  # type: ignore[arg-type]
    plan = _plan(world, transfer_to=BRUNO)
    assert plan.share_links == 2
    lines: list[str] = []
    describe(plan, lines.append)
    assert "  liens publics de rendus supprimés : 2" in lines
    assert _plan(world, BRUNO).share_links == 2
    assert _snapshot(world["engine"], world["data"]) == before  # type: ignore[arg-type]


def test_the_key_of_pseudonyms_is_never_created(tmp_path: Path, migrated_url: str) -> None:
    repo = AccountsRepo(make_engine(migrated_url))
    repo.create_user(LEA)
    data = tmp_path / "vide"
    plan = plan_forget(repo, LEA, projects_dir=data / "projects", data_dir=data)
    assert plan.events is None and plan.possible
    assert not (data / SECRET_FILE).exists() and not data.exists()


def test_the_description_names_no_other_address(world: dict[str, object]) -> None:
    lines: list[str] = []
    describe(_plan(world, transfer_to=BRUNO), lines.append)
    text = "\n".join(lines)
    assert "simulation" in text and "rien n'est écrit" in text
    # Masquée comme en --apply (petit défaut du 4 nov.) : jamais l'adresse en clair.
    assert "Effacement de l***@example.org : simulation (--dry-run)" in text
    assert LEA not in text
    assert "Ancien membre" in text and "Possible tel quel." in text
    assert not any(other in text for other in OTHERS)
    assert "« Grenier »" in text and "« Cuisine »" in text and "Atelier" in text


def test_the_command(
    world: dict[str, object], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENPLAN_DOTENV", "")
    monkeypatch.setenv("OPENPLAN_DATABASE_URL", str(world["url"]))
    monkeypatch.setenv("OPENPLAN_DATA_DIR", str(world["data"]))
    monkeypatch.delenv("OPENPLAN_ADMIN_EMAIL", raising=False)
    before = _snapshot(world["engine"], world["data"])  # type: ignore[arg-type]
    assert main(["accounts", "forget", LEA]) == 1  # projets partagés sans choix
    assert main(["accounts", "forget", LEA, "--dry-run", "--delete-shared"]) == 0
    assert main(["accounts", "forget", LEA, "--transfer-to", BRUNO]) == 0
    monkeypatch.setattr("builtins.input", lambda _prompt: BRUNO)  # mauvaise adresse
    assert main(["accounts", "forget", LEA, "--apply", "--transfer-to", BRUNO]) == 1
    out = capsys.readouterr().out
    assert "Confirmation manquée : rien n'est écrit." in out
    assert "sqlite" not in out and "postgres" not in out  # jamais l'URL de la base
    assert CHLOE not in out and ADMIN not in out
    assert LEA not in out and "l***@example.org" in out  # masquée, dry-run compris
    with pytest.raises(SystemExit):
        main(["accounts", "forget", LEA, "--transfer-to", BRUNO, "--delete-shared"])
    with pytest.raises(SystemExit):
        main(["accounts", "forget", LEA, "--dry-run", "--apply"])
    assert _snapshot(world["engine"], world["data"]) == before  # type: ignore[arg-type]
