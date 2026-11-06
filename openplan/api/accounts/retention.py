"""KAN-142 : la purge quotidienne qui tient les durées promises par la page de confidentialité
(apps/site/src/pages/confidentialite.astro, « Durées de conservation »).

Elle efface EXACTEMENT ce que la page promet, aux délais promis, et rien d'autre : chaque délai
porte en commentaire la phrase de la page qui le promet. Ce que la page ne promet pas (demandes
de rendu, liens publics, lu / non lu de la cloche…) n'est pas touché ici.

Les autres durées de la page sont déjà tenues ailleurs : audience du site (48 heures, 25 mois :
`audience.py`), événements d'usage (13 mois : `events.py`), compte supprimé (`forget.py`) ; ou
hors de l'application : journaux (rotation par l'Infra), rapports d'erreur (GlitchTip),
sauvegardes (VPS).

Elle tourne dans l'application (au démarrage, puis une fois par jour, dans un fil à part, comme
la purge des événements) : aucun changement sur le VPS. Une ligne de journal par purge, un
compte par table, aucune donnée personnelle."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, delete
from sqlalchemy.orm import sessionmaker

from openplan.api.accounts.models import AuthSession, Invitation, LoginToken
from openplan.api.accounts.service import TOKEN_PURGE_AFTER

log = logging.getLogger("openplan.retention")

CODES_KEEP = TOKEN_PURGE_AFTER
"""« Codes de connexion : 15 minutes (effacés après 24 heures) » (confidentialite.astro:113).
Les codes pour changer de mot de passe (30 minutes) et de confirmation (15 minutes) sont dans la
même table et partent au même délai : la page ne leur promet qu'une durée de validité."""
SESSIONS_KEEP = timedelta(days=30)
"""« sessions : 30 jours après la dernière visite » (confidentialite.astro:113) : une session,
révoquée ou non, part 30 jours après son dernier usage (`last_used_at`)."""
INVITATIONS_KEEP = timedelta(days=30)
"""« liens d'invitation : 30 jours au plus » (confidentialite.astro:113) : une invitation (lien
ou nominative), utilisée, refusée, révoquée ou expirée, part 30 jours après sa création ; elles
expirent toutes avant (1, 7, 14 ou 30 jours)."""
EVERY = timedelta(days=1)


@dataclass(frozen=True)
class Purged:
    """Ce qu'une purge a effacé, table par table (des nombres, jamais une donnée)."""

    login_tokens: int
    sessions: int
    invitations: int


class Retention:
    def __init__(
        self, engine: Engine, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.sessions = sessionmaker(engine, expire_on_commit=False)
        self.clock = clock
        self._stop: threading.Event | None = None

    def purge(self) -> Purged:
        """Une purge, en une transaction ; une ligne de journal avec les nombres."""
        now = self.clock()
        with self.sessions() as db, db.begin():
            codes = db.execute(delete(LoginToken).where(LoginToken.created_at < now - CODES_KEEP))
            sessions = db.execute(
                delete(AuthSession).where(AuthSession.last_used_at < now - SESSIONS_KEEP)
            )
            invitations = db.execute(
                delete(Invitation).where(Invitation.created_at < now - INVITATIONS_KEEP)
            )
        done = Purged(
            login_tokens=int(getattr(codes, "rowcount", 0) or 0),
            sessions=int(getattr(sessions, "rowcount", 0) or 0),
            invitations=int(getattr(invitations, "rowcount", 0) or 0),
        )
        log.info(
            "purge des durées de conservation : codes %d, sessions %d, invitations %d",
            done.login_tokens,
            done.sessions,
            done.invitations,
        )
        return done

    def start(self) -> None:
        """Purge au démarrage, puis une fois par jour, dans un fil à part."""
        stop = threading.Event()
        self._stop = stop

        def loop() -> None:
            while not stop.is_set():
                try:
                    self.purge()
                except Exception:
                    log.exception("purge des durées de conservation en échec")
                stop.wait(EVERY.total_seconds())

        threading.Thread(target=loop, name="retention-purge", daemon=True).start()

    def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
