"""Tâches de fond et choix du périphérique de calcul."""

from __future__ import annotations

import sys
import threading
import time
import types
from uuid import uuid4

import pytest

from openplan.adapters.registry import resolve_device
from openplan.api.jobs import JobNotFoundError, JobRegistry
from openplan.schemas.common import StrictModel


class Outcome(StrictModel):
    value: int


def test_job_runs_in_background_and_exposes_its_result() -> None:
    registry = JobRegistry()
    gate = threading.Event()

    def work() -> Outcome:
        gate.wait(timeout=5)
        return Outcome(value=42)

    job = registry.submit("test", work, device="cpu", expected_s=1.0, message="en cours")
    assert job.status == "running" and job.result is None and job.message == "en cours"
    assert registry.get(job.id).status == "running", "la requête n'attend pas la tâche"
    gate.set()
    for _ in range(100):
        status = registry.get(job.id)
        if status.status != "running":
            break
        time.sleep(0.02)
    assert status.status == "done" and status.result == {"value": 42}
    assert status.elapsed_s >= 0 and status.device == "cpu" and status.expected_s == 1.0
    registry.shutdown()


def test_failed_job_reports_the_error_and_unknown_job_is_404() -> None:
    registry = JobRegistry()

    def boom() -> Outcome:
        msg = "poids absents : openplan models pull …"
        raise RuntimeError(msg)

    job = registry.submit("test", boom, device="cuda", expected_s=None, message="")
    for _ in range(100):
        status = registry.get(job.id)
        if status.status != "running":
            break
        time.sleep(0.02)
    assert status.status == "failed" and status.error is not None
    assert "models pull" in status.error and status.result is None
    with pytest.raises(JobNotFoundError):
        registry.get(uuid4())
    registry.shutdown()


def _fake_torch(cuda: bool, name: str = "GPU de test") -> types.ModuleType:
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(  # type: ignore[attr-defined]
        is_available=lambda: cuda, get_device_name=lambda _i: name
    )
    return torch


def test_resolve_device_auto_prefers_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=True))
    assert resolve_device("auto") == ("cuda", "cuda (GPU de test, choisi automatiquement)")
    assert resolve_device(None)[0] == "cuda" and resolve_device("")[0] == "cuda"
    assert resolve_device("cuda") == ("cuda", "cuda (GPU de test, demandé)")
    device, label = resolve_device("cpu")
    assert device == "cpu" and "ignoré" in label


def test_resolve_device_without_gpu_or_torch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda=False))
    assert resolve_device("auto")[0] == "cpu"
    device, label = resolve_device("cuda")
    assert device == "cpu" and "aucun GPU" in label
    monkeypatch.setitem(sys.modules, "torch", None)  # import échoue
    device, label = resolve_device("auto")
    assert device == "cpu" and "torch absent" in label


def test_staged_job_exposes_its_current_stage() -> None:
    from collections.abc import Callable

    registry = JobRegistry()
    reached, gate = threading.Event(), threading.Event()

    def work(progress: Callable[[str], None]) -> Outcome:
        progress("Recherche des murs…")
        reached.set()
        gate.wait(timeout=5)
        progress("Plan prêt")
        return Outcome(value=1)

    job = registry.submit_staged("autoplan", work, device="cpu", expected_s=20.0, message="m")
    assert reached.wait(timeout=5)
    assert registry.get(job.id).stage == "Recherche des murs…"
    gate.set()
    for _ in range(100):
        status = registry.get(job.id)
        if status.status != "running":
            break
        time.sleep(0.02)
    assert status.status == "done" and status.stage == "Plan prêt"
    plain = registry.submit(
        "test", lambda: Outcome(value=2), device="cpu", expected_s=None, message=""
    )
    assert plain.stage == ""
    registry.shutdown()
