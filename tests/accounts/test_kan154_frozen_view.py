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


def test_the_fidelity_verdict_is_kept_with_the_render_and_goes_with_the_project(
    world: World,
) -> None:
    from openplan.api.accounts.render_view import fidelity_of, fidelity_path
    from openplan.schemas.fidelity import FidelityVerdict, MeasureResult
    from openplan.schemas.render_queue import WorkerResult
    from tests.accounts.test_kan89_realistic import png

    rid = world.ask()["id"]
    base, leased = _files(world)
    verdict = FidelityVerdict(
        ok=False,
        failed=["edge_scale"],
        measures=[MeasureResult(name="edge_scale", value=0.05, threshold=0.025, ok=False)],
        attempts=3,
    )
    meta = WorkerResult(
        seconds=12.0, vram_peak_mib=4000, width=64, height=48, engine="fake", fidelity=verdict
    )
    done = world.worker.post(
        base.replace("/files", "/result"),
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    assert done.status_code == 200, done.text
    store = _store(world)
    assert fidelity_of(store, world.project_id, rid) == verdict  # type: ignore[arg-type]
    path = fidelity_path(store, world.project_id, rid)  # type: ignore[arg-type]
    world.client.delete(f"/api/projects/{world.project_id}")
    assert not path.exists()


def _rendered(world: World) -> str:
    """Une demande rendue, avec ses fichiers figés et son verdict de fidélité."""
    from openplan.schemas.fidelity import FidelityVerdict
    from openplan.schemas.render_queue import WorkerResult
    from tests.accounts.test_kan89_realistic import png

    rid = str(world.ask()["id"])
    base, leased = _files(world)
    meta = WorkerResult(
        seconds=1.0,
        vram_peak_mib=0,
        width=64,
        height=48,
        engine="fake",
        fidelity=FidelityVerdict(ok=True, attempts=1),
    )
    done = world.worker.post(
        base.replace("/files", "/result"),
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    assert done.status_code == 200, done.text
    return rid


def _sidecars(world: World, rid: str) -> list[Path]:
    from uuid import UUID

    from openplan.api.accounts.render_view import fidelity_path

    store, request_id = _store(world), UUID(rid)
    files = [frozen_path(store, world.project_id, request_id, n) for n in FROZEN]
    return [*files, fidelity_path(store, world.project_id, request_id)]


def _forget(world: World, **kw: object) -> None:
    from openplan.api.accounts.forget import apply_forget, plan_forget
    from openplan.api.accounts.repo import AccountsRepo
    from tests.accounts.test_kan89_realistic import AUGUS

    repo = AccountsRepo(world.service.engine)
    projects = _store(world).project_dir(world.project_id).parent
    data = world.client.app.state.settings.data_dir  # type: ignore[attr-defined]
    plan = plan_forget(repo, AUGUS, projects_dir=projects, data_dir=data, **kw)  # type: ignore[arg-type]
    assert apply_forget(repo, plan, projects_dir=projects, data_dir=data).done


def test_forgetting_the_only_member_erases_the_frozen_files(world: World) -> None:
    """KAN-132 (forget, suppression du compte) : projet dont il est le seul membre, supprimé
    avec son dossier ; bake, caméra et verdict figés partent avec lui."""
    paths = _sidecars(world, _rendered(world))
    assert all(p.is_file() for p in paths)
    _forget(world)
    assert not any(p.exists() for p in paths)


def test_a_project_that_stays_keeps_its_render_and_its_frozen_files(world: World) -> None:
    """Projet partagé repris par un héritier (KAN-132) : le rendu reste, signé « Ancien
    membre » ; ses fichiers figés restent avec lui, comme ses images de style (KAN-97)."""
    rid = _rendered(world)
    paths = _sidecars(world, rid)
    token = world.client.post(
        f"/api/projects/{world.project_id}/invitations", json={"role": "editor"}
    ).json()["token"]
    other = world.person("heritier.kan154@example.org")
    assert other.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    _forget(world, heirs={})
    image = _store(world).project_dir(world.project_id) / "realistic" / f"{rid}.png"
    assert image.is_file()
    assert all(p.is_file() for p in paths)
