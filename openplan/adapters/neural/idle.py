"""Libère la mémoire de la carte graphique après un temps sans calcul.

Les modèles locaux (Stable Diffusion et ses ControlNet, l'IP-Adapter, la profondeur, le
détecteur d'ouvertures) restent chargés entre deux calculs pour aller vite. Sans cette garde,
ils occupaient la carte jusqu'à l'arrêt du serveur et bloquaient tout autre calcul sur la
machine. Chaque adaptateur déclare ici son modèle : après `OPENPLAN_GPU_IDLE_S` secondes sans
calcul (600 par défaut, 0 pour ne jamais libérer), le modèle est relâché et le cache CUDA vidé ;
le calcul suivant le recharge, sans rien demander."""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

IDLE_ENV = "OPENPLAN_GPU_IDLE_S"
DEFAULT_IDLE_S = 600.0


def idle_seconds() -> float | None:
    """Délai d'inactivité avant libération ; None : jamais (variable à 0 ou moins)."""
    raw = os.environ.get(IDLE_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_IDLE_S
    except ValueError:
        value = DEFAULT_IDLE_S
    return None if value <= 0 else value


def free_gpu_cache() -> None:
    """Rend au pilote la mémoire que PyTorch garde en réserve (si torch est déjà chargé :
    jamais importé pour rien)."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()
        torch.cuda.ipc_collect()


class IdleRelease:
    """Garde d'un modèle chargé : `with garde.use():` autour de chaque calcul ; au bout du
    délai sans calcul, `release` est appelé (il remet le modèle à None) puis le cache vidé.
    Jamais pendant un calcul : la libération et le début d'un calcul passent par le même
    verrou."""

    def __init__(
        self,
        release: Callable[[], None],
        *,
        timeout_s: float | None = None,
        from_env: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._release = release
        self.timeout_s = idle_seconds() if from_env and timeout_s is None else timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._busy = 0
        self._last = clock()
        self._timer: threading.Timer | None = None
        self.releases = 0
        """Nombre de libérations faites (pour les tests et le bilan)."""

    @contextmanager
    def use(self) -> Iterator[None]:
        with self._lock:
            self._busy += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        try:
            yield
        finally:
            with self._lock:
                self._busy -= 1
                self._last = self._clock()
                if self._busy == 0:
                    self._arm()

    def _arm(self) -> None:
        if self.timeout_s is None:
            return
        timer = threading.Timer(self.timeout_s, self.check)
        timer.daemon = True
        timer.start()
        self._timer = timer

    def check(self) -> bool:
        """Libère si le délai est passé sans calcul ; vrai si c'est fait."""
        if self.timeout_s is None:
            return False
        with self._lock:
            if self._busy or self._clock() - self._last < self.timeout_s - 1e-6:
                return False
            self._timer = None
            self._release()
            self.releases += 1
        free_gpu_cache()
        return True
