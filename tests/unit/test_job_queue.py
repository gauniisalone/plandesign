"""File d'attente des tâches lourdes : un lancement pendant un calcul attend au lieu d'être
refusé, démarre seul dans l'ordre d'arrivée, s'annule, ne se double pas ; l'échec d'une tâche
ne bloque pas la suivante. Tâches simulées (aucun modèle, aucun GPU), horloge contrôlée."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pytest

from openplan.api.jobs import JobNotCancellableError, JobNotFoundError, JobRegistry, JobStatus
from openplan.schemas.common import StrictModel


class Outcome(StrictModel):
    value: int


class Clock:
    """Horloge du registre, avancée à la main : les durées exposées sont exactes."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wait_for(registry: JobRegistry, job_id: object, states: set[str]) -> JobStatus:
    for _ in range(500):
        status = registry.get(job_id)  # type: ignore[arg-type]
        if status.status in states:
            return status
        time.sleep(0.01)
    msg = f"état attendu {states}, obtenu {status.status}"
    raise AssertionError(msg)


def gated(log: list[int], value: int, gate: threading.Event | None = None) -> Callable[[], Outcome]:
    def work() -> Outcome:
        if gate is not None:
            assert gate.wait(5)
        log.append(value)
        return Outcome(value=value)

    return work


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def registry(clock: Clock) -> Iterator[JobRegistry]:
    reg = JobRegistry(clock=clock)
    yield reg
    reg.shutdown()


def test_jobs_wait_and_start_alone_in_arrival_order(registry: JobRegistry, clock: Clock) -> None:
    log: list[int] = []
    gate = threading.Event()
    first = registry.submit(
        "autopilot", gated(log, 1, gate), device="cpu", expected_s=30, message=""
    )
    assert first.status == "running" and first.queue_position is None
    others = [
        registry.submit(kind, gated(log, i), device="cpu", expected_s=10, message="")
        for i, kind in [(2, "autoplan"), (3, "render"), (4, "finish")]
    ]
    assert [o.status for o in others] == ["queued"] * 3
    assert [o.queue_position for o in others] == [1, 2, 3]
    assert all(o.elapsed_s == 0 for o in others), "le temps d'attente n'est pas du calcul"
    clock.now += 12.5
    assert registry.get(first.id).elapsed_s == 12.5
    assert registry.get(others[2].id).queue_position == 3
    gate.set()  # le calcul en cours finit : les suivants démarrent seuls, sans autre geste
    for o in others:
        assert wait_for(registry, o.id, {"done", "failed"}).status == "done"
    assert log == [1, 2, 3, 4]


def test_queued_job_can_be_cancelled_and_never_runs(registry: JobRegistry) -> None:
    log: list[int] = []
    gate = threading.Event()
    first = registry.submit("render", gated(log, 1, gate), device="cpu", expected_s=5, message="")
    second = registry.submit("render", gated(log, 2), device="cpu", expected_s=5, message="")
    third = registry.submit("render", gated(log, 3), device="cpu", expected_s=5, message="")
    with pytest.raises(JobNotFoundError):
        registry.cancel(second.id, "user:quelqu-un-d-autre")
    cancelled = registry.cancel(second.id, "user")
    assert cancelled.status == "cancelled" and cancelled.queue_position is None
    assert registry.get(third.id).queue_position == 1, "la file se resserre"
    with pytest.raises(JobNotCancellableError):
        registry.cancel(first.id, "user")  # déjà lancée
    gate.set()
    assert wait_for(registry, third.id, {"done"}).result == {"value": 3}
    assert registry.get(second.id).status == "cancelled"
    assert log == [1, 3]
    with pytest.raises(JobNotCancellableError):
        registry.cancel(third.id, "user")  # finie


def test_same_request_while_waiting_replaces_the_queued_one(registry: JobRegistry) -> None:
    """Deux dépôts sur le même plan pendant l'attente : un seul chemin automatique, celui de la
    dernière demande, sous l'identifiant et à la place du premier."""
    log: list[int] = []
    gate = threading.Event()
    registry.submit("render", gated(log, 0, gate), device="cpu", expected_s=5, message="")
    key = ("projet", "plan")
    a = registry.submit_staged(
        "autopilot", lambda _s: gated(log, 1)(), device="cpu", expected_s=9, message="a", dedup=key
    )
    other = registry.submit_staged(
        "autopilot",
        lambda _s: gated(log, 5)(),
        device="cpu",
        expected_s=9,
        message="o",
        dedup=("autre projet", "plan"),
    )
    b = registry.submit_staged(
        "autopilot", lambda _s: gated(log, 2)(), device="cpu", expected_s=7, message="b", dedup=key
    )
    assert b.id == a.id and b.status == "queued" and b.queue_position == 1
    assert b.message == "b" and b.expected_s == 7
    # Même clé mais autre utilisateur, ou autre sorte : pas un doublon.
    c = registry.submit(
        "autopilot",
        gated(log, 3),
        device="cpu",
        expected_s=1,
        message="",
        owner="user:b",
        dedup=key,
    )
    d = registry.submit(
        "autoplan", gated(log, 4), device="cpu", expected_s=1, message="", dedup=key
    )
    assert len({a.id, other.id, c.id, d.id}) == 4
    gate.set()
    for job in (a, other, c, d):
        wait_for(registry, job.id, {"done"})
    assert log == [0, 2, 5, 3, 4], "la première demande n'a jamais calculé"


def test_running_job_is_not_replaced_by_a_new_request(registry: JobRegistry) -> None:
    """Le doublon ne vise que l'attente : une demande arrivée pendant que le même calcul tourne
    est mise en file (le plan a pu changer depuis son démarrage)."""
    log: list[int] = []
    gate = threading.Event()
    key = ("projet", "plan")
    a = registry.submit(
        "autopilot", gated(log, 1, gate), device="cpu", expected_s=5, message="", dedup=key
    )
    b = registry.submit(
        "autopilot", gated(log, 2), device="cpu", expected_s=5, message="", dedup=key
    )
    assert a.status == "running" and b.status == "queued" and a.id != b.id
    gate.set()
    wait_for(registry, b.id, {"done"})
    assert log == [1, 2]


def test_a_failing_job_does_not_block_the_next_one(registry: JobRegistry) -> None:
    gate = threading.Event()

    def boom() -> Outcome:
        assert gate.wait(5)
        msg = "Blender a planté"
        raise RuntimeError(msg)

    failing = registry.submit("render", boom, device="cpu", expected_s=5, message="")
    log: list[int] = []
    after = registry.submit("autoplan", gated(log, 7), device="cpu", expected_s=5, message="")
    assert after.status == "queued"
    gate.set()
    assert wait_for(registry, failing.id, {"failed", "done"}).status == "failed"
    assert wait_for(registry, after.id, {"done", "failed"}).result == {"value": 7}


def test_queue_state_is_exposed_per_owner(registry: JobRegistry, clock: Clock) -> None:
    gate = threading.Event()
    log: list[int] = []
    first = registry.submit(
        "render", gated(log, 1, gate), device="cpu", expected_s=5, message="", owner="user:a"
    )
    q1 = registry.submit(
        "render", gated(log, 2), device="cpu", expected_s=5, message="", owner="user:b"
    )
    q2 = registry.submit(
        "render", gated(log, 3), device="cpu", expected_s=5, message="", owner="user:a"
    )
    running = registry.running_for("user:a")
    assert running is not None and running.kind == "render"
    assert registry.running_for("user:b") is None, "en attente n'est pas en cours"
    assert registry.queued_for("user:a") == [q2.id] and registry.queued_for("user:b") == [q1.id]
    # La file est partagée (un seul fil) : la place compte toutes les tâches qui attendent.
    assert registry.get(q2.id).queue_position == 2
    clock.now += 3
    assert registry.elapsed(running) == 3
    gate.set()
    wait_for(registry, q2.id, {"done"})
    assert registry.get(first.id).status == "done" and log == [1, 2, 3]
    assert registry.queued_for("user:a") == []
