"""Ouverture des comptes au démarrage de l'API (OPENPLAN_DATABASE_URL défini)."""

from __future__ import annotations

import logging

from openplan.api.accounts.db import check_schema, make_engine
from openplan.api.accounts.mail import Mailer, build_mailer
from openplan.api.accounts.service import AccountService
from openplan.api.settings import Settings

log = logging.getLogger("openplan.accounts")


class FakeMailerExposedError(RuntimeError):
    """Le mailer `fake` (boîte en mémoire, lue par un point de test) hors de la boucle locale."""


def open_accounts(settings: Settings, mailer: Mailer | None = None) -> AccountService:
    """Connexion à la base, refus clair si elle n'est pas migrée (jamais de migration
    implicite : `openplan accounts migrate`, après `pg_dump`). `mailer` remplace celui des
    réglages (tests)."""
    if not settings.database_url:  # pragma: no cover - appelé seulement avec l'URL
        msg = "OPENPLAN_DATABASE_URL absent"
        raise RuntimeError(msg)
    if settings.mailer == "fake" and not settings.local_only:
        msg = (
            "OPENPLAN_MAILER=fake : réservé aux tests sur la boucle locale (sa boîte se lit par "
            f"un point de test), refusé sur {settings.host}"
        )
        raise FakeMailerExposedError(msg)
    mailer = mailer or build_mailer(settings.mailer, settings.mail_from, settings.brevo_api_key)
    engine = make_engine(settings.database_url)
    check_schema(engine)
    if settings.mailer == "console" and not settings.local_only:
        log.warning(
            "comptes : envoi « console » sur un serveur exposé, les codes de connexion vont "
            "dans le journal du serveur (OPENPLAN_MAILER=smtp_api avec la clé Brevo pour envoyer)"
        )
    return AccountService(engine, settings, mailer)
