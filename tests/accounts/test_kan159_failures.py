"""KAN-159 côté serveur : la cause exacte d'un arrêt du garde-fou (valeur relevée et seuil au
journal des rendus, phrase écrite par le serveur), un ancien poste qui envoie « vram » toujours
lu, et un échec dû au poste qui ne coûte plus de quota."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from openplan.api.accounts.events import cause_code
from openplan.api.accounts.render_queue import RenderQueue
from openplan.schemas.render_queue import CAPABILITIES_HEADER, FAILURE_TEXT
from tests.accounts.test_kan89_realistic import AUGUS, TOKEN, WORKER, World

DETAILED = {"state": "idle", "version": "heartbeat+style+style-ignore+why"}


def _take(world: World, claim: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    world.ask()
    got = world.worker.post("/api/worker/claim", headers=WORKER, json=claim or DETAILED)
    assert got.status_code == 200, got.text
    return got.json()["request_id"], {**WORKER, "X-Render-Lease": got.json()["lease"]}


def _row(world: World, rid: str) -> dict[str, object]:
    rows = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"]
    found: dict[str, object] = next(r for r in rows if r["id"] == rid)
    return found


def _used(world: World) -> int:
    used: int = world.client.get("/api/realistic/status").json()["used_today"]
    return used


def test_the_exact_cause_is_served_stored_and_told(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    world.ask()
    got = world.worker.post("/api/worker/claim", headers=WORKER, json=DETAILED)
    assert "why" in got.headers[CAPABILITIES_HEADER].split("+")
    rid, leased = got.json()["request_id"], {**WORKER, "X-Render-Lease": got.json()["lease"]}
    url = f"/api/worker/jobs/{rid}/fail"
    # Aucun texte : codes connus, entiers bornés.
    for bad in (
        {"reason": "carte_occupee", "measured": "812 Mio"},
        {"reason": "carte_occupee", "measured": -1},
        {"reason": "carte_occupee", "detail": "vue 3D"},
        {"reason": "la carte sert déjà"},
    ):
        assert world.worker.post(url, headers=leased, json=bad).status_code == 422, bad
    body = {"reason": "carte_occupee", "measured": 812, "threshold": 500}
    assert world.worker.post(url, headers=leased, json=body).status_code == 204
    row = _row(world, rid)
    assert row["state"] == "failed" and row["error"] == FAILURE_TEXT["carte_occupee"]
    assert "une autre application utilise la carte graphique" in str(row["error"]).lower()
    # Valeur relevée et seuil au journal des rendus, sans identifiant ni compte.
    journal = world.client.app.state.settings.data_dir / "renders.jsonl"  # type: ignore[attr-defined]
    text = journal.read_text(encoding="utf-8")
    entry = json.loads(text.splitlines()[-1])
    assert entry["result"] == "failed" and entry["error"] == "carte_occupee"
    assert (entry["measured"], entry["threshold"]) == (812, 500)
    for secret in (rid, str(world.project_id), str(world.augus_id), AUGUS, TOKEN):
        assert secret not in text
    # Back-office : la mémoire reste « vram », nvidia-smi et la durée vont à « other ».
    assert cause_code("carte_occupee") == cause_code("vram")
    assert cause_code("nvidia_smi_illisible") == cause_code("other")
    world.close()


def test_an_old_worker_sending_vram_is_still_read(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    world.ask()
    old = world.worker.post("/api/worker/claim", headers=WORKER, json={"version": "heartbeat"})
    assert old.headers[CAPABILITIES_HEADER] == "heartbeat"  # pas de « why » pour lui
    rid, leased = old.json()["request_id"], {**WORKER, "X-Render-Lease": old.json()["lease"]}
    done = world.worker.post(
        f"/api/worker/jobs/{rid}/fail", headers=leased, json={"reason": "vram"}
    )
    assert done.status_code == 204
    assert (
        _row(world, rid)["error"] == "Le poste de rendu manquait de mémoire : réessayez plus tard."
    )
    world.close()


def test_a_failure_of_the_worker_costs_no_quota(tmp_path: Path, migrated_url: str) -> None:
    """Avant KAN-159, toute demande des 24 h comptait, même échouée par la faute du poste :
    deux arrêts du garde-fou épuisaient un quota de 2."""
    world = World(tmp_path, migrated_url, daily_quota=2)
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    for body in (
        {"reason": "vram_depassee", "measured": 5230, "threshold": 5000},
        {"reason": "vram"},  # un ancien poste : c'est aussi le garde-fou
        {"reason": "nvidia_smi_illisible_depart"},
    ):
        rid, leased = _take(world)
        assert _used(world) == 1  # en attente puis pris : la demande compte
        assert (
            world.worker.post(f"/api/worker/jobs/{rid}/fail", headers=leased, json=body).status_code
            == 204
        )
        assert _used(world) == 0, body
    # Le quota n'est pas épuisé : deux demandes passent encore.
    rid, leased = _take(world)
    world.worker.post(
        f"/api/worker/jobs/{rid}/fail", headers=leased, json={"reason": "invalid_input"}
    )
    assert _used(world) == 1  # la vue elle-même peut en être la cause : elle compte
    second = world.client.post(
        f"/api/projects/{world.project_id}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id)},
    )
    assert second.status_code in (200, 201) and _used(world) == 2
    third = world.client.post(
        f"/api/projects/{world.project_id}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id), "draw": 1},
    )
    assert third.status_code == 429
    assert queue.used_today(UUID(str(world.augus_id))) == 2
    world.close()
