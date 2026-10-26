"""KAN-96 : bail d'une prise du poste. Une prise sans nouvelles repasse en attente une fois,
puis échoue (« perdu la connexion ») ; 5 min sans battement pour un poste qui bat, 30 min pour
un ancien poste ; un `claim` libre reprend ce que le poste n'a plus en main ; un dépôt en retard
ou répété ne donne jamais deux résultats, ni un échec posé sur un rendu fait."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect

from openplan.api.accounts import render_routes
from openplan.api.accounts.db import downgrade, make_engine, upgrade
from openplan.api.accounts.render_queue import (
    BEAT_LEASE,
    MAX_ATTEMPTS,
    TAKE_TTL,
    LeaseError,
    RenderQueue,
    public_request,
)
from openplan.cli.render_worker.client import ApiBusyError, WorkerClient
from openplan.schemas.render_queue import (
    BEAT_EVERY_S,
    CAPABILITIES_HEADER,
    FAILURE_TEXT,
    WorkerClaim,
    WorkerResult,
    capabilities,
)
from tests.accounts.test_kan89_realistic import WORKER, World, _queue, png
from tests.accounts.test_kan89_worker_flow import _worker

LOST = "Le poste de rendu a perdu la connexion, relancez le rendu."
META = WorkerResult(seconds=95.0, vram_peak_mib=0, width=64, height=48, engine="fake")
BEATS = {"state": "idle", "version": "heartbeat"}


def test_migration_kan96_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine)
    assert "beat_at" in {c["name"] for c in inspect(engine).get_columns("render_requests")}
    downgrade(engine, "kan89_0006")
    assert "beat_at" not in {c["name"] for c in inspect(engine).get_columns("render_requests")}
    upgrade(engine)
    engine.dispose()


def test_capabilities_are_tokens_in_version() -> None:
    assert MAX_ATTEMPTS == 2 and timedelta(minutes=5) == BEAT_LEASE
    assert capabilities(None) == frozenset()
    assert capabilities(WorkerClaim()) == frozenset()
    assert capabilities(WorkerClaim(version="heartbeat+style")) == {"heartbeat", "style"}
    assert FAILURE_TEXT["lost"] == LOST


# --- la file seule (horloge réglable) ------------------------------------------------------


def test_a_beating_worker_keeps_its_claim_while_it_beats(migrated_url: str, tmp_path: Path) -> None:
    """5 min sans battement : la prise repasse en attente, une fois ; la seconde perte est un
    échec « perdu la connexion »."""
    queue, clock, user, project = _queue(migrated_url, tmp_path, expire_h=100.0)
    row = queue.create(user, project, uuid4(), uuid4())
    job = queue.claim(beats=True)
    assert job is not None
    for _ in range(3):  # 12 min de calcul et d'envoi, un battement toutes les 4 min
        clock.now += timedelta(minutes=4)
        queue.sweep()
        assert queue.get(row.id).state == "taken"  # type: ignore[union-attr]
        queue.renew(row.id, job.lease)
    clock.now += BEAT_LEASE + timedelta(seconds=1)
    queue.sweep()
    assert queue.get(row.id).state == "pending"  # type: ignore[union-attr]
    with pytest.raises(LeaseError):
        queue.renew(row.id, job.lease)
    second = queue.claim(beats=True)
    assert second is not None and second.request_id == row.id
    clock.now += BEAT_LEASE + timedelta(seconds=1)
    queue.sweep()
    final = queue.get(row.id)
    assert final is not None and final.state == "failed" and final.error == "lost"
    assert public_request(final, False).error == LOST
    assert queue.claim(beats=True) is None


def test_an_old_worker_keeps_the_30_minutes(migrated_url: str, tmp_path: Path) -> None:
    """Un poste qui n'a pas annoncé savoir battre n'est jamais repris au bout de 5 min."""
    queue, clock, user, project = _queue(migrated_url, tmp_path, expire_h=100.0)
    row = queue.create(user, project, uuid4(), uuid4())
    assert queue.claim() is not None
    assert queue.get(row.id).beat_at is None  # type: ignore[union-attr]
    clock.now += timedelta(minutes=29)
    queue.sweep()
    assert queue.get(row.id).state == "taken"  # type: ignore[union-attr]
    clock.now += timedelta(minutes=1, seconds=1)
    queue.sweep()
    assert queue.get(row.id).state == "pending"  # type: ignore[union-attr]
    # Le cas c914167f : une prise d'avant la MEP (sans battement) que le poste n'a plus ; le
    # premier `claim` libre la reprend, sans geste en base.
    assert queue.claim() is not None
    queue.release_held()
    final = queue.get(row.id)
    assert final is not None and (final.state, final.error) == ("failed", "lost")


def test_a_free_claim_takes_back_what_the_worker_no_longer_holds(
    migrated_url: str, tmp_path: Path
) -> None:
    queue, _clock, user, project = _queue(migrated_url, tmp_path, expire_h=100.0)
    row = queue.create(user, project, uuid4(), uuid4())
    first = queue.claim(beats=True)
    queue.release_held()
    again = queue.claim(beats=True)
    assert first is not None and again is not None and again.request_id == row.id
    assert again.lease != first.lease
    queue.release_held()
    final = queue.get(row.id)
    assert final is not None and final.state == "failed" and final.error == "lost"
    # Rien d'autre n'est touché : une demande faite reste faite.
    done = queue.create(user, project, uuid4(), uuid4())
    job = queue.claim()
    assert job is not None and job.request_id == done.id
    queue.complete(done.id, job.lease, META)
    queue.release_held()
    queue.sweep()
    assert queue.get(done.id).state == "done"  # type: ignore[union-attr]


# --- par les routes -------------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


class Later:
    """Décale l'horloge de la file de l'application."""

    def __init__(self, world: World) -> None:
        self.queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
        self.offset = timedelta()
        self.queue.clock = lambda: datetime.now(UTC) + self.offset

    def __call__(self, delta: timedelta) -> None:
        self.offset += delta


def _claim(world: World) -> dict[str, str]:
    taken = world.worker.post("/api/worker/claim", json=BEATS, headers=WORKER)
    assert taken.status_code == 200, taken.text
    assert taken.headers[CAPABILITIES_HEADER] == "heartbeat"
    job: dict[str, str] = taken.json()
    return job


def _deposit(world: World, job: dict[str, str]) -> int:
    done = world.worker.post(
        f"/api/worker/jobs/{job['request_id']}/result",
        headers={**WORKER, "X-Render-Lease": job["lease"]},
        data={"meta": META.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    return int(done.status_code)


def _rows(world: World) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = world.client.get(
        f"/api/projects/{world.project_id}/realistic"
    ).json()["requests"]
    return rows


def _images(world: World) -> list[str]:
    store = world.client.app.state.store  # type: ignore[attr-defined]
    folder = store.project_dir(world.project_id) / "realistic"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def test_the_beat_route_and_the_announced_capability(world: World) -> None:
    world.ask()
    # Un ancien poste (version vide) : ni en-tête, ni bail court.
    old = world.worker.post("/api/worker/claim", json={"state": "waiting"}, headers=WORKER)
    assert old.status_code == 204 and CAPABILITIES_HEADER not in old.headers
    # Un jeton inconnu est ignoré ; « heartbeat » est confirmé.
    taken = world.worker.post(
        "/api/worker/claim", json={"state": "idle", "version": "heartbeat+demain"}, headers=WORKER
    )
    assert taken.headers[CAPABILITIES_HEADER] == "heartbeat"
    job = taken.json()
    beat = f"/api/worker/jobs/{job['request_id']}/beat"
    assert world.worker.post(beat, headers=WORKER, content=b"").status_code == 404
    wrong = {**WORKER, "X-Render-Lease": str(uuid4())}
    assert world.worker.post(beat, headers=wrong, content=b"").status_code == 404
    leased = {**WORKER, "X-Render-Lease": job["lease"]}
    assert world.worker.post(beat, headers=leased, content=b"").status_code == 204
    assert world.client.get("/api/realistic/status").json()["worker"] == "busy"
    assert _deposit(world, job) == 200
    assert world.worker.post(beat, headers=leased, content=b"").status_code == 404
    world.close()


def test_a_late_result_after_a_take_back_is_the_only_result(world: World) -> None:
    """Le dernier envoi arrive APRÈS la remise en attente (5 min sans battement) : il fait le
    rendu, une seule fois ; ni seconde image, ni « lost » posé ensuite."""
    later = Later(world)
    asked = world.ask()
    job = _claim(world)
    later(BEAT_LEASE + timedelta(seconds=1))
    assert [r["state"] for r in _rows(world)] == ["pending"]
    assert _deposit(world, job) == 200
    assert _deposit(world, job) == 200  # le même envoi répété : rien de plus
    rows = _rows(world)
    assert [(r["id"], r["state"], r["image"]) for r in rows] == [(asked["id"], "done", True)]
    assert _images(world) == [f"{asked['id']}.png"]
    assert world.worker.post("/api/worker/claim", json=BEATS, headers=WORKER).status_code == 204
    later(TAKE_TTL * 2)
    assert [(r["state"], r["error"]) for r in _rows(world)] == [("done", None)]
    journal = world.client.app.state.settings.data_dir / "renders.jsonl"  # type: ignore[attr-defined]
    assert journal.read_text(encoding="utf-8").count('"result": "done"') == 1
    world.close()


def test_a_late_result_after_the_claim_was_taken_again_is_refused(world: World) -> None:
    """Remise en attente puis reprise (nouveau bail) : l'envoi en retard de l'ancien bail est
    refusé, celui du nouveau fait le rendu ; puis une perte définitive (« lost ») refuse tout
    dépôt en retard, sans image."""
    later = Later(world)
    asked = world.ask()
    first = _claim(world)
    later(BEAT_LEASE + timedelta(seconds=1))
    second = _claim(world)
    assert second["request_id"] == asked["id"] and second["lease"] != first["lease"]
    assert _deposit(world, first) == 404
    assert _deposit(world, second) == 200
    assert _deposit(world, first) == 404
    assert [(r["state"], r["error"]) for r in _rows(world)] == [("done", None)]
    assert _images(world) == [f"{asked['id']}.png"]
    # Une autre vue, perdue deux fois : échec en français, et le dépôt en retard n'y change rien.
    pid, lid, cid = world.new_project(world.client)
    made = world.client.post(
        f"/api/projects/{pid}/levels/{lid}/realistic", json={"camera_id": str(cid)}
    )
    lost_id = made.json()["id"]
    a = _claim(world)
    later(BEAT_LEASE + timedelta(seconds=1))
    b = _claim(world)
    later(BEAT_LEASE + timedelta(seconds=1))
    assert a["request_id"] == b["request_id"] == lost_id
    rows = world.client.get(f"/api/projects/{pid}/realistic").json()["requests"]
    assert [(r["state"], r["error"]) for r in rows] == [("failed", LOST)]
    assert _deposit(world, b) == 404
    rows = world.client.get(f"/api/projects/{pid}/realistic").json()["requests"]
    assert [(r["state"], r["image"]) for r in rows] == [("failed", False)]
    world.close()


# --- le vrai poste contre l'application -----------------------------------------------------


def test_the_worker_retries_the_upload_without_rendering_again(
    tmp_path: Path, migrated_url: str
) -> None:
    """Deux envois coupés (réseau), puis le dépôt : un seul calcul, un seul rendu ; le poste a
    vu « heartbeat » confirmé et a battu pendant l'envoi, par la route du battement."""
    world = World(tmp_path, migrated_url)
    asked = world.ask()
    worker, said = _worker(world, tmp_path)
    worker.wait = lambda _s: None  # type: ignore[method-assign, assignment]
    worker.beat_every_s = 0.02
    client: WorkerClient = worker.client
    upload, beat, run = client.upload, client.beat, worker.sandbox.run
    calls = {"upload": 0, "beat": 0, "run": 0}

    def flaky_upload(*args: object) -> None:
        calls["upload"] += 1
        if calls["upload"] <= 2:
            deadline = calls["beat"] + 2
            for _ in range(200):  # l'envoi « lent » dure au moins deux battements
                if calls["beat"] >= deadline:
                    break
                time.sleep(0.01)
            raise ApiBusyError("WriteTimeout")
        upload(*args)  # type: ignore[arg-type]

    def counted_beat(*args: object) -> None:
        beat(*args)  # type: ignore[arg-type]
        calls["beat"] += 1

    def counted_run(*args: object) -> object:
        calls["run"] += 1
        return run(*args)  # type: ignore[arg-type]

    client.upload = flaky_upload  # type: ignore[method-assign, assignment]
    client.beat = counted_beat  # type: ignore[method-assign, assignment]
    worker.sandbox.run = counted_run  # type: ignore[method-assign, assignment]
    worker.cycle()
    assert client.beat_every_s == float(BEAT_EVERY_S)
    assert worker.rendered == 1, said
    assert calls["run"] == 1 and calls["upload"] == 3 and calls["beat"] >= 4, calls
    assert any("nouvel essai dans 15 s" in line for line in said), said
    rows = _rows(world)
    assert [(r["id"], r["state"], r["image"]) for r in rows] == [(asked["id"], "done", True)]
    world.close()


def test_a_new_worker_against_an_older_server_does_not_beat(
    tmp_path: Path, migrated_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un serveur qui ne confirme pas « heartbeat » (d'avant KAN-96) : le poste ne bat pas, et sa
    prise garde le délai de 30 min."""
    monkeypatch.setattr(render_routes, "SERVED_CAPABILITIES", frozenset())
    world = World(tmp_path, migrated_url)
    asked = world.ask()
    worker, said = _worker(world, tmp_path)
    beats: list[object] = []
    worker.client.beat = beats.append  # type: ignore[method-assign, assignment]
    worker.beat_every_s = None
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    seen: list[object] = []
    complete = queue.complete

    def spy(request_id: UUID, lease: UUID, result: WorkerResult) -> object:
        seen.append(queue.get(request_id).beat_at)  # type: ignore[union-attr]
        return complete(request_id, lease, result)

    queue.complete = spy  # type: ignore[method-assign, assignment]
    worker.cycle()
    assert worker.rendered == 1, said
    assert worker.client.beat_every_s is None and beats == [] and seen == [None]
    assert [r["id"] for r in _rows(world)] == [asked["id"]]
    world.close()
