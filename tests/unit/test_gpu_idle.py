"""Libération de la carte graphique après inactivité : le modèle est relâché au bout du délai
sans calcul, jamais pendant un calcul, rechargé sans rien demander au calcul suivant ; le délai
se règle par OPENPLAN_GPU_IDLE_S (0 : jamais)."""

from __future__ import annotations

import threading

import pytest

from openplan.adapters.neural import idle
from openplan.adapters.neural.idle import IDLE_ENV, IdleRelease, idle_seconds


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeModel:
    """Un adaptateur réduit à sa forme : chargé à la demande, relâché par la garde."""

    def __init__(self, clock: _Clock, timeout: float | None = 600.0) -> None:
        self.model: object | None = None
        self.loads = 0
        self.idle = IdleRelease(self._unload, timeout_s=timeout, from_env=False, clock=clock)

    def _unload(self) -> None:
        self.model = None

    def compute(self) -> str:
        with self.idle.use():
            if self.model is None:
                self.loads += 1
                self.model = object()
            return "ok"


@pytest.fixture(autouse=True)
def _no_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pas de vrai minuteur dans les tests : `check()` est appelé à la main."""
    monkeypatch.setattr(IdleRelease, "_arm", lambda self: None)


def test_released_after_the_delay_and_reloaded_transparently() -> None:
    clock = _Clock()
    model = _FakeModel(clock)
    assert model.compute() == "ok" and model.loads == 1
    clock.now += 599.0
    assert not model.idle.check(), "avant le délai, rien ne bouge"
    assert model.model is not None
    clock.now += 2.0
    assert model.idle.check()
    assert model.model is None and model.idle.releases == 1
    # Le calcul suivant recharge, sans erreur ni question.
    assert model.compute() == "ok" and model.loads == 2


def test_each_compute_restarts_the_delay() -> None:
    clock = _Clock()
    model = _FakeModel(clock)
    model.compute()
    clock.now += 500.0
    model.compute()
    clock.now += 500.0
    assert not model.idle.check(), "500 s seulement depuis le dernier calcul"
    clock.now += 101.0
    assert model.idle.check()


def test_never_released_during_a_compute() -> None:
    clock = _Clock()
    model = _FakeModel(clock)
    started, release = threading.Event(), threading.Event()

    def long_compute() -> None:
        with model.idle.use():
            model.model = object()
            started.set()
            release.wait(5)

    worker = threading.Thread(target=long_compute)
    worker.start()
    started.wait(5)
    clock.now += 10_000.0
    assert not model.idle.check(), "un calcul en cours garde le modèle"
    assert model.model is not None
    release.set()
    worker.join(5)
    clock.now += 601.0
    assert model.idle.check()


def test_zero_means_never(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(IDLE_ENV, "0")
    assert idle_seconds() is None
    clock = _Clock()
    guard = IdleRelease(lambda: None, clock=clock)
    clock.now += 1e9
    assert not guard.check()


def test_delay_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(IDLE_ENV, raising=False)
    assert idle_seconds() == 600.0
    monkeypatch.setenv(IDLE_ENV, "45")
    assert idle_seconds() == 45.0
    monkeypatch.setenv(IDLE_ENV, "pas un nombre")
    assert idle_seconds() == 600.0


def test_cache_is_emptied_only_if_torch_is_already_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class _Cuda:
        @staticmethod
        def is_available() -> bool:
            return True

        @staticmethod
        def empty_cache() -> None:
            calls.append("empty")

        @staticmethod
        def ipc_collect() -> None:
            calls.append("ipc")

    class _Torch:
        cuda = _Cuda

    monkeypatch.setitem(idle.sys.modules, "torch", _Torch)
    idle.free_gpu_cache()
    assert calls == ["empty", "ipc"]
    monkeypatch.delitem(idle.sys.modules, "torch")
    idle.free_gpu_cache()
    assert calls == ["empty", "ipc"], "sans torch chargé, rien n'est importé"


def test_the_four_gpu_adapters_carry_a_guard() -> None:
    """Chaque adaptateur qui charge un modèle sur la carte passe ses calculs par la garde."""
    import inspect

    from openplan.adapters.neural import depth_anything, finish, inspiration, openings

    for module, cls, methods in (
        (finish, finish.ControlledRefiner, ("finish", "inspire")),
        (inspiration, inspiration.DepthGuidedDiffusion, ("generate",)),
        (depth_anything, depth_anything.DepthAnythingMetric, ("predict",)),
        (openings, openings.OwlV2OpeningDetector, ("_detect",)),
    ):
        assert "IdleRelease(self._unload)" in inspect.getsource(cls.__init__), module.__name__
        for name in methods:
            assert "self.idle.use()" in inspect.getsource(getattr(cls, name)), name
