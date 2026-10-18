"""Bases jetables pour les tests des comptes : SQLite toujours, Postgres en plus quand
OPENPLAN_TEST_DATABASE_URL désigne un serveur de test (conteneur local, jamais celui du VPS).
Une base neuve par test. Sans l'extra « comptes », ces tests ÉCHOUENT (décision PO du
18 oct.) : l'extra est installé par `make setup` et par chaque `uv run` du Makefile ; seuls
les tests Postgres restent conditionnés à OPENPLAN_TEST_DATABASE_URL."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

PG_ENV = "OPENPLAN_TEST_DATABASE_URL"

MISSING = [m for m in ("sqlalchemy", "alembic") if importlib.util.find_spec(m) is None]
if MISSING:
    msg = (
        f"extra « comptes » absent ({', '.join(MISSING)}) : `make setup`, ou "
        "`uv sync --inexact --extra comptes`"
    )
    raise RuntimeError(msg)


def _backends() -> list[str]:
    return ["sqlite", "postgres"] if os.environ.get(PG_ENV) else ["sqlite"]


@pytest.fixture(params=_backends())
def db_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'comptes.db'}"
        return
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from openplan.api.accounts.db import normalize_url

    admin_url = make_url(normalize_url(os.environ[PG_ENV]))
    name = f"t_{uuid4().hex[:12]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as con:
        con.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield admin_url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as con:
            con.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def migrated_url(db_url: str) -> str:
    from openplan.api.accounts.db import make_engine, upgrade

    engine = make_engine(db_url)
    upgrade(engine)
    engine.dispose()
    return db_url
