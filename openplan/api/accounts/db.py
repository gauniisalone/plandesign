"""Connexion à la base des comptes et migrations Alembic (note 17, § 8).

Les comptes ne s'activent que si `OPENPLAN_DATABASE_URL` est défini (conteneur de
app.plandesign.fr) ; le mode local n'importe jamais ce module. Postgres 16 en production
(pilote psycopg 3) ; SQLite marche aussi pour les tests et un essai sur une machine.

`alembic upgrade head` se lance par `openplan accounts migrate` (au démarrage du conteneur,
après `pg_dump`) ; l'application refuse de démarrer sur une base en retard.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, event

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class AccountsSchemaError(RuntimeError):
    """La base des comptes n'est pas à la dernière migration."""


def normalize_url(url: str) -> str:
    """`postgres://` et `postgresql://` → pilote psycopg 3 (le seul installé)."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def _sqlite_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(url: str) -> Engine:
    url = normalize_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(engine, "connect", _sqlite_foreign_keys)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    """SQLite rend des dates sans fuseau : elles sont en UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def head_revision() -> str:
    head = ScriptDirectory.from_config(_config()).get_current_head()
    if head is None:  # pragma: no cover - au moins une migration existe
        msg = "aucune migration des comptes"
        raise AccountsSchemaError(msg)
    return head


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def upgrade(engine: Engine, revision: str = "head") -> None:
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def downgrade(engine: Engine, revision: str) -> None:
    config = _config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, revision)


def check_schema(engine: Engine) -> None:
    """Refus clair si la base n'est pas migrée (jamais de migration implicite)."""
    current, head = current_revision(engine), head_revision()
    if current != head:
        msg = (
            f"base des comptes à la révision {current or 'vide'}, attendu {head} : lancer "
            "`openplan accounts migrate` (après une sauvegarde pg_dump)"
        )
        raise AccountsSchemaError(msg)
