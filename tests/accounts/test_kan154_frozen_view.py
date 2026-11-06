"""KAN-154 (étape 1) : la vue d'une demande de rendu réaliste est FIGÉE au clic. Le poste reçoit
le bake et la caméra du moment de la demande, même si le projet change avant le rendu ; une
demande d'avant (sans fichiers) est servie par recalcul ; les fichiers partent avec le projet."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.api.accounts.render_view import FROZEN, frozen_path, frozen_view
from openplan.pipeline.scene3d import bake_level
from openplan.scene.store import ProjectStore
from tests.accounts.test_kan89_realistic import WORKER, World


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def _store(world: World) -> ProjectStore:
    store: ProjectStore = world.client.app.state.store  # type: ignore[attr-defined]
    return store


def _taller(world: World) -> None:
    """Le projet change après le clic : niveau plus haut (le bake n'est plus le même)."""
    store = _store(world)
    project = store.load(world.project_id)
    level = project.levels[world.level_id].model_copy(update={"height": 3.4})
    store.save(project.model_copy(update={"levels": {world.level_id: level}}))


def _files(world: World) -> tuple[str, dict[str, str]]:
    job = world.worker.post("/api/worker/claim", headers=WORKER).json()
    return f"/api/worker/jobs/{job['request_id']}/files", {**WORKER, "X-Render-Lease": job["lease"]}


def test_the_request_keeps_its_view_even_if_the_project_changes(world: World) -> None:
    rid = world.ask()["id"]
    store = _store(world)
    for name in FROZEN:
        assert frozen_path(store, world.project_id, rid, name).is_file()  # type: ignore[arg-type]
    frozen = frozen_view(store, world.project_id, rid)  # type: ignore[arg-type]
    assert frozen is not None
    _taller(world)
    now = bake_level(store.load(world.project_id), world.level_id, "realistic")
    assert now.model_dump_json() != frozen[0].model_dump_json()  # le projet a bien changé
    base, leased = _files(world)
    served = world.worker.get(f"{base}/bake", headers=leased)
    assert served.status_code == 200
    assert served.text == frozen[0].model_dump_json()
    camera = world.worker.get(f"{base}/camera", headers=leased)
    assert camera.text == frozen[1].model_dump_json()


def test_a_request_from_before_is_still_served_by_recomputing(world: World) -> None:
    rid = world.ask()["id"]
    store = _store(world)
    for name in FROZEN:
        frozen_path(store, world.project_id, rid, name).unlink()  # type: ignore[arg-type]
    _taller(world)
    base, leased = _files(world)
    served = world.worker.get(f"{base}/bake", headers=leased)
    assert served.status_code == 200
    now = bake_level(store.load(world.project_id), world.level_id, "realistic")
    assert served.text == now.model_dump_json()


def test_the_frozen_files_go_with_the_project(world: World) -> None:
    rid = world.ask()["id"]
    store = _store(world)
    paths = [frozen_path(store, world.project_id, rid, n) for n in FROZEN]  # type: ignore[arg-type]
    assert all(p.is_file() for p in paths)
    gone = world.client.delete(f"/api/projects/{world.project_id}")
    assert gone.status_code in (200, 204), gone.text
    assert not any(p.exists() for p in paths)
