"""KAN-117 : plafond quotidien de TOUS les e-mails, par classes de priorité.

Le quota de Brevo (offre gratuite : 300 par jour) est partagé par tous les envois ; quand on
s'en approche, les codes de connexion passent avant le reste :

- `login` (P1) : codes de connexion et formulaire de contact du site, jusqu'au plafond total
  (OPENPLAN_MAIL_DAILY_TOTAL, 280 par défaut) ;
- `invitation` (P2) : invitations par e-mail, jusqu'à OPENPLAN_MAIL_DAILY_INVITES (250).

Une classe fermée répond 503 avec Retry-After (jusqu'à minuit UTC) et
`X-PlanDesign-Limit: mail-quota`. Le contrôle (`check`) passe AVANT toute lecture de l'adresse :
la réponse est la même pour toutes. 503 et non 429 : la prison fail2ban compte les 429 de
`/api/auth/*` (revue Infra). Le compte se fait à l'envoi (`take`), qui tient le plafond même
entre deux requêtes simultanées. Alertes dans le journal, une fois par jour et par seuil, sans
aucune adresse : 80 % du total, puis la fermeture de chaque classe. 0 = sans plafond.

En mémoire, comme les autres limites (un seul processus d'API) : un redémarrage remet le
compte à zéro.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Literal

log = logging.getLogger("openplan.accounts")

MailKind = Literal["login", "invitation"]
KINDS: tuple[MailKind, ...] = ("login", "invitation")
ALERT_SHARE = 0.8
QUOTA_TEXT = "Trop d'e-mails envoyés aujourd'hui : réessayez demain."
LIMIT_HEADER = "X-PlanDesign-Limit"


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
        self, total: int, ceilings: Mapping[str, int], clock: Callable[[], datetime]
    ) -> None:
        self.total = total
        self.ceilings = dict(ceilings)
        self.clock = clock
        self._day: date | None = None
        self._sent = 0
        self._alerted: set[str] = set()
        self._lock = Lock()

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
            return True

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
