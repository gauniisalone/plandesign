"""Ouverture des comptes au démarrage de l'API (OPENPLAN_DATABASE_URL défini)."""

from __future__ import annotations

from openplan.api.accounts.db import check_schema, make_engine
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.settings import Settings


def open_accounts(settings: Settings) -> AccountsRepo:
    """Connexion à la base, refus clair si elle n'est pas migrée (jamais de migration
    implicite : `openplan accounts migrate`, après `pg_dump`)."""
    if not settings.database_url:  # pragma: no cover - appelé seulement avec l'URL
        msg = "OPENPLAN_DATABASE_URL absent"
        raise RuntimeError(msg)
    engine = make_engine(settings.database_url)
    check_schema(engine)
    return AccountsRepo(engine)
