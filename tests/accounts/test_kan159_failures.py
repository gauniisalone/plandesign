"""KAN-159 côté serveur : la cause exacte d'un arrêt du garde-fou (valeur relevée et seuil au
journal des rendus, phrase écrite par le serveur), un ancien poste qui envoie « vram » toujours
lu, et un échec dû au poste qui ne coûte plus de quota."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


def test_an_expired_request_costs_no_quota(tmp_path: Path, migrated_url: str) -> None:
    """Décision du PO (7 nov.) : une demande que personne n'a prise à temps (poste éteint ou
    occupé) n'est pas la faute de la personne ; le délai avant réouverture l'ignore aussi."""
    world = World(tmp_path, migrated_url, daily_quota=1, expire_h=1)
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    start = datetime.now(UTC)
    world.ask()
    assert _used(world) == 1
    # Deux heures plus tard, le poste ne l'a jamais prise : elle expire.
    queue.clock = lambda: start + timedelta(hours=2)
    queue.sweep()
    rows = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"]
    assert [r["state"] for r in rows] == ["expired"]
    assert _used(world) == 0
    # Le quota de 1 se rouvre aussitôt : une nouvelle demande passe…
    world.ask()
    assert _used(world) == 1
    # … et le refus suivant compte 24 h depuis CELLE-CI, pas depuis l'expirée (22 h).
    refused = world.client.post(
        f"/api/projects/{world.project_id}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id), "draw": 1},
    )
    assert refused.status_code == 429
    assert int(refused.headers["Retry-After"]) > 23 * 3600
    world.close()


def test_the_expired_text_says_it_costs_no_quota() -> None:
    """Le texte d'une demande expirée est écrit par le front (RealisticRenders.tsx)."""
    root = Path(__file__).resolve().parents[2]
    source = (root / "apps/web/src/board/RealisticRenders.tsx").read_text(encoding="utf-8")
    expected = (
        "Expiré : non calculé à temps, relancez « Rendre ». Ce rendu ne compte pas dans votre "
        "quota du jour."
    )
    assert expected in source
