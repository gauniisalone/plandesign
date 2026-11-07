"""KAN-162 : le serveur dit au poste combien de rendus attendent en file (`X-Render-Pending`)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from openplan.api.accounts.render_queue import TAKE_TTL
from openplan.api.realistic_config import RealisticConfig
from openplan.schemas.render_queue import CAPABILITIES_HEADER, PENDING_HEADER
from tests.accounts.test_kan89_realistic import WORKER, World, _queue

CLAIM = "/api/worker/claim"


def _render_headers(response: object) -> set[str]:
    headers = response.headers  # type: ignore[attr-defined]
    return {k.lower() for k in headers if k.lower().startswith("x-render-")}


def test_every_claim_says_how_many_wait_and_nothing_else(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url)
    # File vide : 0, même pour un poste libre.
    empty = world.worker.post(CLAIM, json={"state": "waiting"}, headers=WORKER)
    assert empty.status_code == 204 and empty.headers[PENDING_HEADER] == "0"
    world.ask()
    for state in ("waiting", "paused"):
        answer = world.worker.post(CLAIM, json={"state": state}, headers=WORKER)
        assert answer.status_code == 204
        # Un entier seul ; aucun corps ; pas d'autre en-tête du poste (aucune capacité annoncée).
        assert answer.headers[PENDING_HEADER] == "1"
        assert answer.content == b""
        assert _render_headers(answer) == {PENDING_HEADER.lower()}
    # Avec capacités : l'en-tête des capacités, et le nombre, rien de plus.
    announced = {"state": "waiting", "version": "heartbeat+style+style-ignore+why"}
    answer = world.worker.post(CLAIM, json=announced, headers=WORKER)
    assert answer.headers[PENDING_HEADER] == "1"
    assert _render_headers(answer) == {PENDING_HEADER.lower(), CAPABILITIES_HEADER.lower()}
    # La prise : le travail, et ce qui reste après elle (0) ; le corps reste celui d'avant.
    job = world.worker.post(CLAIM, json={"state": "idle"}, headers=WORKER)
    assert job.status_code == 200 and job.headers[PENDING_HEADER] == "0"
    assert set(job.json()) == {"request_id", "lease", "created_at"}
    # Pendant le calcul, la demande prise ne compte plus.
    waiting = world.worker.post(CLAIM, json={"state": "waiting"}, headers=WORKER)
    assert waiting.headers[PENDING_HEADER] == "0"
    world.close()


def test_the_count_uses_the_filters_of_the_claim(migrated_url: str, tmp_path: Path) -> None:
    queue, clock, user, project = _queue(migrated_url, tmp_path, expire_h=2.0)
    assert queue.pending_count() == 0
    old = queue.create(user, project, uuid4(), uuid4())
    clock.now += timedelta(hours=1)
    queue.create(user, project, uuid4(), uuid4())
    queue.create(user, project, uuid4(), uuid4())
    assert queue.pending_count() == 3
    # Une demande prise sort du compte ; reprise par la file (poste tombé), elle y revient.
    job = queue.claim()
    assert job is not None and job.request_id == old.id
    assert queue.pending_count() == 2
    # Expirée (non prise à temps) : plus comptée, même avant le balayage.
    clock.now += timedelta(hours=1, seconds=1)
    assert queue.pending_count() == 2  # `old` est prise, les deux autres ont 1 h de moins
    clock.now += timedelta(hours=1)
    assert queue.pending_count() == 0
    clock.now += TAKE_TTL
    # Un compte retiré de la liste blanche, ou plus aucun compte : rien à prendre, 0.
    queue.create(user, project, uuid4(), uuid4())
    assert queue.pending_count() == 1
    queue.config = RealisticConfig(accounts=frozenset({uuid4()}))
    assert queue.pending_count() == 0
    queue.config = RealisticConfig(accounts=frozenset())
    assert queue.pending_count() == 0
