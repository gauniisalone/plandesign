"""B1a : migrations, tables, dépôt des comptes et activation par OPENPLAN_DATABASE_URL."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from openplan.api.accounts.db import (
    AccountsSchemaError,
    check_schema,
    current_revision,
    downgrade,
    head_revision,
    make_engine,
    normalize_url,
    upgrade,
)
from openplan.api.accounts.repo import AccountsRepo, normalize_email
from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.cli.main import main

TABLES = {"users", "sessions", "login_tokens", "projects", "members"}


def test_migrations_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    with pytest.raises(AccountsSchemaError, match="openplan accounts migrate"):
        check_schema(engine)
    upgrade(engine)
    assert current_revision(engine) == head_revision()
    check_schema(engine)
    names = set(inspect(engine).get_table_names())
    assert names >= TABLES
    columns = {c["name"] for c in inspect(engine).get_columns("login_tokens")}
    assert {"token_hash", "expires_at", "used_at", "next", "ip", "attempts"} <= columns
    assert "token" not in columns and "code" not in columns  # jamais le secret en clair
    assert head_revision() == "kan89_0006"
    invitations = {c["name"] for c in inspect(engine).get_columns("invitations")}
    assert {"token_hash", "role", "expires_at", "used_at", "revoked_at", "email"} <= invitations
    assert "token" not in invitations  # jamais le jeton en clair
    reads = {c["name"] for c in inspect(engine).get_columns("notification_read_items")}
    assert reads == {"user_id", "event_id", "project_id", "event_at"}
    # KAN-55 se défait seule (la colonne de l'adresse invitée part, la table reste).
    downgrade(engine, "kan52_0004")
    invitations = {c["name"] for c in inspect(engine).get_columns("invitations")}
    assert "email" not in invitations and "token_hash" in invitations
    indexes = {i["name"] for i in inspect(engine).get_indexes("invitations")}
    assert "ix_invitations_email" not in indexes
    # KAN-52 se défait seule (les deux tables de la cloche partent), puis B1e (la table des
    # invitations), puis B1b2 (la colonne des essais).
    downgrade(engine, "b1e_0003")
    names = set(inspect(engine).get_table_names())
    assert "invitations" in names
    assert not names & {"notification_reads", "notification_read_items"}
    downgrade(engine, "b1b2_0002")
    assert "invitations" not in inspect(engine).get_table_names()
    downgrade(engine, "b1a_0001")
    columns = {c["name"] for c in inspect(engine).get_columns("login_tokens")}
    assert "attempts" not in columns and "token_hash" in columns
    upgrade(engine)
    assert current_revision(engine) == "kan89_0006"
    downgrade(engine, "base")
    assert "invitations" not in inspect(engine).get_table_names()
    assert "notification_reads" not in inspect(engine).get_table_names()
    assert not (set(inspect(engine).get_table_names()) & TABLES)
    engine.dispose()


def test_url_uses_psycopg3() -> None:
    assert normalize_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


def test_repo_users_projects_members(migrated_url: str) -> None:
    repo = AccountsRepo(make_engine(migrated_url))
    alice = repo.create_user("  Alice@Example.ORG ")
    assert alice.email == "alice@example.org" == normalize_email(" ALICE@example.org")
    with pytest.raises(IntegrityError):
        repo.create_user("alice@example.org")
    bob = repo.create_user("bob@example.org")
    assert repo.user_by_email("ALICE@example.org") is not None
    project_id = uuid4()
    row = repo.add_project(project_id, alice.id, "Grenier")
    assert row.storage_key == f"projects/{project_id}"
    assert repo.role_name(project_id, alice.id) == "owner"
    assert repo.role_name(project_id, bob.id) is None
    repo.set_member(project_id, bob.id, "viewer", added_by=alice.id)
    assert repo.role_name(project_id, bob.id) == "viewer"
    repo.set_member(project_id, bob.id, "editor", added_by=alice.id)
    assert repo.role_name(project_id, bob.id) == "editor"
    with pytest.raises(ValueError, match="rôle inconnu"):
        repo.set_member(project_id, bob.id, "admin", added_by=alice.id)
    assert repo.project_ids_of(bob.id) == {project_id}
    repo.remove_member(project_id, bob.id)
    assert repo.project_ids_of(bob.id) == set()
    repo.remove_project(project_id)
    assert repo.role_name(project_id, alice.id) is None
    repo.engine.dispose()


def test_app_with_database_url(tmp_path: Path, migrated_url: str, db_url: str) -> None:
    settings = Settings(data_dir=tmp_path / "data", database_url=migrated_url)
    app = create_app(settings=settings)
    assert app.state.accounts is not None
    assert app.state.store.root == tmp_path / "data" / "projects"
    assert app.state.storage.root == tmp_path / "data"
    app.state.accounts.engine.dispose()


def test_app_refuses_an_unmigrated_database(tmp_path: Path, db_url: str) -> None:
    with pytest.raises(AccountsSchemaError):
        create_app(settings=Settings(data_dir=tmp_path / "data", database_url=db_url))


def test_cli_migrate_and_status(
    db_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENPLAN_DOTENV", "")
    monkeypatch.delenv("OPENPLAN_DATABASE_URL", raising=False)
    assert main(["accounts", "status"]) == 2
    monkeypatch.setenv("OPENPLAN_DATABASE_URL", db_url)
    assert main(["accounts", "status"]) == 1
    assert main(["accounts", "migrate"]) == 0
    out = capsys.readouterr().out
    assert head_revision() in out
    assert "sqlite" not in out and "postgres" not in out  # jamais l'URL de la base
    assert main(["accounts", "status"]) == 0
