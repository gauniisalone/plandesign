"""Durées mesurées des étapes longues, pour annoncer un temps restant juste (V1, point 6).

Moyenne glissante des dernières durées réelles par sorte d'étape (rendu, finition,
aménagement), rangée dans les réglages locaux de cette machine (`<données>/timings.json`),
jamais dans un projet : elles dépendent du processeur graphique, pas de la pièce. Sans mesure,
la valeur par défaut de l'appelant sert. Le temps restant d'une étape en cours n'est jamais nul :
une étape qui dépasse son estimation est supposée finir dans un délai proportionnel à ce
qu'elle a déjà pris.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

WINDOW = 5
"""Nombre de durées gardées par sorte d'étape."""
MIN_LEFT_S = 3.0
OVERRUN_SHARE = 0.25
"""Étape en dépassement : encore un quart de ce qu'elle a déjà pris (3 s au moins)."""
TIMINGS_FILE = "timings.json"


class Timings:
    """Durées mesurées, lues et écrites dans un petit fichier JSON local."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, list[float]] = {}
        if path is not None and path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self._data = {
                    str(k): [float(x) for x in v if isinstance(x, int | float) and x > 0][-WINDOW:]
                    for k, v in raw.items()
                    if isinstance(v, list)
                }
            except (OSError, ValueError):
                self._data = {}

    def estimate(self, kind: str, default: float) -> float:
        """Moyenne des dernières durées réelles, sinon `default`."""
        values = self._data.get(kind)
        return sum(values) / len(values) if values else default

    def record(self, kind: str, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            kept = max(0.01, round(seconds, 2))  # une étape très courte compte quand même
            self._data[kind] = [*self._data.get(kind, []), kept][-WINDOW:]
            if self.path is None:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._data, indent=2) + "\n", encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                pass  # une mesure perdue ne gêne pas : l'estimation garde l'ancienne valeur


class Countdown:
    """Temps restant d'une suite d'étapes estimées, lu en direct pendant le calcul."""

    def __init__(self, estimates: list[float]) -> None:
        self.estimates = list(estimates)
        self.index = 0
        self.started = time.perf_counter()

    def start(self, index: int) -> None:
        self.index = index
        self.started = time.perf_counter()

    def remaining(self) -> float:
        """Étape en cours (jamais moins de 3 s, plus en cas de dépassement) et suivantes."""
        if self.index >= len(self.estimates):
            return MIN_LEFT_S
        spent = time.perf_counter() - self.started
        left = self.estimates[self.index] - spent
        if left < MIN_LEFT_S:
            left = max(MIN_LEFT_S, OVERRUN_SHARE * spent)
        return left + sum(self.estimates[self.index + 1 :])
