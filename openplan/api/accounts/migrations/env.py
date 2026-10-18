"""Environnement Alembic : la connexion est fournie par `openplan.api.accounts.db`."""

from __future__ import annotations

from alembic import context
from sqlalchemy.engine import Connection

from openplan.api.accounts.models import Base

connection: Connection | None = context.config.attributes.get("connection")
if connection is None:  # pragma: no cover - toujours appelé par accounts.db
    msg = "lancer les migrations par `openplan accounts migrate`"
    raise RuntimeError(msg)

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
