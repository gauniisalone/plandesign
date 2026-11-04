"""KAN-117 : plafond quotidien de TOUS les e-mails, par classes de priorité.

Le quota de Brevo (offre gratuite : 300 par jour) est partagé par tous les envois ; quand on
s'en approche, les codes de connexion passent avant le reste :

- `login` (P1) : codes de connexion, formulaire de contact du site et avis « mot de passe
  changé » (KAN-104), jusqu'au plafond total (OPENPLAN_MAIL_DAILY_TOTAL, 280 par défaut) ;
- `invitation` (P2) : invitations par e-mail, jusqu'à OPENPLAN_MAIL_DAILY_INVITES (250) ;
- `signup` (P3, KAN-104) : inscription, « déjà un compte », oubli et preuve récente, jusqu'à
  OPENPLAN_MAIL_DAILY_CAP (200) : l'oubli peut attendre, le code de connexion reste ouvert.

Une classe fermée répond 503 avec Retry-After (jusqu'à minuit UTC) et
`X-PlanDesign-Limit: mail-quota`. Le contrôle (`check`) passe AVANT toute lecture de l'adresse :
la réponse est la même pour toutes. 503 et non 429 : la prison fail2ban compte les 429 de
`/api/auth/*` (revue Infra). Le compte se fait à l'envoi (`take`), qui tient le plafond même
entre deux requêtes simultanées. Alertes dans le journal, une fois par jour et par seuil, sans
aucune adresse : 80 % du total, puis la fermeture de chaque classe. 0 = sans plafond.

Le compte du jour est aussi écrit sur le disque (`data/mail-budget.json`, jour, nombre et
alertes déjà données), de façon atomique à chaque envoi, et relu au démarrage : une MEP ne
remet plus le compte à zéro (KAN-104, trou (a) du plafond). Un fichier illisible repart de
zéro, avec un avertissement dans le journal.
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4

log = logging.getLogger("openplan.accounts")

MailKind = Literal["login", "invitation", "signup"]
KINDS: tuple[MailKind, ...] = ("login", "invitation", "signup")
ALERT_SHARE = 0.8
QUOTA_TEXT = "Trop d'e-mails envoyés aujourd'hui : réessayez demain."
LIMIT_HEADER = "X-PlanDesign-Limit"
STATE_FILE = "mail-budget.json"
"""Sous OPENPLAN_DATA_DIR : le compte du jour, qui survit à un redémarrage."""


class MailQuotaError(RuntimeError):
    """Cette classe d'e-mails est fermée jusqu'à minuit UTC : rien n'a été envoyé."""

    def __init__(self, retry_after_s: int) -> None:
        super().__init__("plafond d'e-mails du jour atteint")
        self.retry_after_s = retry_after_s

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(self.retry_after_s), LIMIT_HEADER: "mail-quota"}


class MailBudget:
    """Envois du jour (UTC), tous e-mails confondus ; chaque classe s'arrête à son plafond."""

    def __init__(
        self,
        total: int,
        ceilings: Mapping[str, int],
        clock: Callable[[], datetime],
        path: Path | None = None,
    ) -> None:
        self.total = total
        self.ceilings = dict(ceilings)
        self.clock = clock
        self.path = path
        """Fichier du compte du jour ; None = en mémoire seulement (tests du compteur)."""
        self._day: date | None = None
        self._sent = 0
        self._alerted: set[str] = set()
        self._lock = Lock()
        self._load()

    def limit(self, kind: MailKind) -> int:
        """Plafond de la classe, jamais au-dessus du total ; 0 = sans plafond."""
        caps = [c for c in (self.total, self.ceilings.get(kind, 0)) if c > 0]
        return min(caps) if caps else 0

    def check(self, kind: MailKind) -> None:
        """MailQuotaError si la classe est fermée aujourd'hui ; ne compte rien."""
        with self._lock:
            self._roll()
            if self._closed(kind):
                raise MailQuotaError(self._retry_after_s())

    def take(self, kind: MailKind) -> bool:
        """Un envoi de plus, s'il reste de la place aujourd'hui pour cette classe."""
        with self._lock:
            self._roll()
            if self._closed(kind):
                return False
            self._sent += 1
            self._alert()
            self._save()
            return True

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
            day, sent = date.fromisoformat(state["day"]), int(state["sent"])
            alerted = {str(a) for a in state.get("alerted", [])}
        except (OSError, ValueError, TypeError, KeyError):
            log.warning("compte d'e-mails du jour illisible : il repart de zéro")
            return
        self._day, self._sent, self._alerted = day, max(0, sent), alerted

    def _save(self) -> None:
        """Écriture atomique : un fichier à côté, puis `os.replace`."""
        if self.path is None:
            return
        state = {"day": str(self._day), "sent": self._sent, "alerted": sorted(self._alerted)}
        partial = self.path.with_suffix(f".{uuid4().hex}.part")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(state), encoding="utf-8")
            os.replace(partial, self.path)
        except OSError:
            # Disque plein ou en lecture seule : le compte en mémoire tient encore le plafond.
            partial.unlink(missing_ok=True)
            log.warning("compte d'e-mails du jour non écrit sur le disque")

    def _roll(self) -> None:
        today = self.clock().date()
        if today != self._day:
            self._day, self._sent, self._alerted = today, 0, set()

    def _closed(self, kind: MailKind) -> bool:
        limit = self.limit(kind)
        return limit > 0 and self._sent >= limit

    def _alert(self) -> None:
        if self.total > 0 and self._sent >= self.total * ALERT_SHARE and "80" not in self._alerted:
            self._alerted.add("80")
            log.warning(
                "plafond d'e-mails du jour atteint à 80 %% (%d sur %d) : voir l'offre Brevo",
                self._sent,
                self.total,
            )
        for kind in KINDS:
            if self._closed(kind) and kind not in self._alerted:
                self._alerted.add(kind)
                log.warning(
                    "e-mails « %s » fermés jusqu'à minuit UTC (%d envoyés aujourd'hui)",
                    kind,
                    self._sent,
                )

    def _retry_after_s(self) -> int:
        now = self.clock()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return max(1, math.ceil((midnight - now).total_seconds()))
