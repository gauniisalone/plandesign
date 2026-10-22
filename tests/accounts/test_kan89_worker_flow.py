"""KAN-89 : le vrai poste (client, boucle, calcul isolé) contre l'application, moteur factice."""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi.testclient import TestClient

from openplan.cli.render_worker.client import WorkerClient
from openplan.cli.render_worker.loop import Worker
from openplan.cli.render_worker.sandbox import Sandbox
from tests.accounts.helpers import ORIGIN
from tests.accounts.test_kan89_realistic import TOKEN, World


def _worker(world: World, tmp_path: Path, token: str = TOKEN) -> tuple[Worker, list[str]]:
    said: list[str] = []
    http = TestClient(world.client.app, base_url=ORIGIN)
    worker = Worker(
        client=WorkerClient(ORIGIN, token, http=http),
        engine="fake",
        pause_flag=tmp_path / "worker.pause",
        stop_flag=tmp_path / "worker.stop",
        sandbox=Sandbox(mode="socket"),
        skip=lambda: None,
        say=said.append,
        sleep=lambda _s: None,
    )
    return worker, said


def test_worker_renders_a_request_end_to_end(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    asked = world.ask()
    worker, said = _worker(world, tmp_path)
    delay = worker.cycle()
    assert 10.0 <= delay <= 15.0 and worker.rendered == 1, said
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    row = listing["requests"][0]
    assert row["id"] == asked["id"] and row["state"] == "done" and row["image"] is True
    image = world.client.get(f"/api/projects/{world.project_id}/realistic/{row['id']}/image")
    assert image.content.startswith(b"\x89PNG")
    # File vide ensuite : un simple battement.
    assert 10.0 <= worker.cycle() <= 15.0
    assert world.client.get("/api/realistic/status").json()["worker"] == "online"
    assert not any(TOKEN in line for line in said)
    world.close()


def test_worker_with_a_wrong_token_backs_off(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    world.ask()
    worker, said = _worker(world, tmp_path, token=secrets.token_urlsafe(32))
    assert [worker.cycle() for _ in range(3)] == [60.0, 300.0, 300.0]
    assert "Jeton refusé" in said[0]
    row = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"][0]
    assert row["state"] == "pending"
    world.close()


def test_worker_reports_a_view_that_cannot_be_read(tmp_path: Path, migrated_url: str) -> None:
    """La caméra de la vue a disparu entre la demande et la prise : le serveur répond 410, le
    poste signale l'échec, l'interface le dit en français."""
    world = World(tmp_path, migrated_url)
    world.ask()
    store = world.client.app.state.store  # type: ignore[attr-defined]
    project = store.load(world.project_id)
    level = project.levels[world.level_id]
    elements = {k: v for k, v in level.elements.items() if k != world.camera_id}
    project.levels[world.level_id] = level.model_copy(update={"elements": elements})
    store.save(project)
    worker, said = _worker(world, tmp_path)
    worker.cycle()
    assert worker.rendered == 0
    assert any("invalid_input" in line for line in said), said
    row = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"][0]
    assert row["state"] == "failed"
    assert row["error"] == "La vue n'a pas pu être lue par le poste de rendu."
    world.close()
