"""Limitation de débit en mémoire, par clé (adresse IP), sur une fenêtre glissante.

Un seul processus d'API sur le VPS : la mémoire suffit. Un redémarrage remet les compteurs à
zéro, ce qui est acceptable (fail2ban côté infra sur les 401/403 répétés, note 17 § 3).
"""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable
from threading import Lock

MAX_KEYS = 10_000
"""Au-delà, les clés les plus anciennes sont oubliées (la mémoire reste bornée)."""


class SlidingWindowLimiter:
    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self.clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Compte la demande et dit si elle passe (une demande refusée ne compte pas)."""
        now = self.clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = deque()
                self._hits[key] = hits
            self._hits.move_to_end(key)
            while hits and now - hits[0] >= self.window_s:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            while len(self._hits) > MAX_KEYS:
                self._hits.popitem(last=False)
            return True
