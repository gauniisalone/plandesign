"""KAN-89 : file des rendus réalistes calculés par le poste d'augus, API du poste, droits."""

from __future__ import annotations

import io
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import inspect

from openplan.api.accounts.db import downgrade, make_engine, upgrade
from openplan.api.accounts.mail import FakeMailer
from openplan.api.accounts.render_queue import (
    MAX_ATTEMPTS,
    TAKE_TTL,
    LeaseError,
    QuotaExceededError,
    RenderQueue,
)
from openplan.api.accounts.repo import AccountsRepo
from openplan.api.accounts.service import AccountService
from openplan.api.guards import GLOBAL_ROUTES, PROJECT_ROUTES
from openplan.api.realistic_config import RealisticConfig, parse_accounts, parse_hashes
from openplan.api.worker_auth import token_hash
from openplan.scene.ops import AddCamera, apply_op
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import Point2
from openplan.schemas.elements import Camera
from openplan.schemas.render_queue import WorkerResult
from openplan.schemas.scene import Project
from tests.accounts.helpers import ORIGIN, accounts_client, sign_in
from tests.factories import only_level, rect_room_project

AUGUS = "rendus@example.org"
TOKEN = secrets.token_urlsafe(32)
WORKER = {"Authorization": f"Bearer {TOKEN}"}
P = "/api/projects/{project_id}"
L = P + "/levels/{level_id}"
USER_ROUTES = {("GET", P + "/realistic"), ("GET", P + "/realistic/{request_id}/image")}
WORKER_ROUTES = {
    ("POST", "/api/worker/heartbeat"),
    ("POST", "/api/worker/claim"),
    ("GET", "/api/worker/jobs/{request_id}/files/{name}"),
    ("POST", "/api/worker/jobs/{request_id}/result"),
    ("POST", "/api/worker/jobs/{request_id}/fail"),
}


def png(width: int = 64, height: int = 48) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 180, 150)).save(buffer, format="PNG")
    return buffer.getvalue()


class World:
    def __init__(self, tmp_path: Path, url: str, **realistic: object) -> None:
        repo = AccountsRepo(make_engine(url))
        self.augus_id = repo.create_user(AUGUS).id
        repo.engine.dispose()
        config = RealisticConfig(
            accounts=frozenset({self.augus_id}),
            worker_token_sha256=(token_hash(TOKEN),),
            **realistic,  # type: ignore[arg-type]
        )
        client, service, mailer = accounts_client(tmp_path, url, signup="open", realistic=config)
        self.client: TestClient = client
        self.service: AccountService = service
        self.mailer: FakeMailer = mailer
        sign_in(client, mailer, AUGUS)
        self.project_id, self.level_id, self.camera_id = self.new_project(client)
        # Le poste : ni cookie, ni origine.
        self.worker = TestClient(client.app, base_url=ORIGIN)

    def new_project(self, client: TestClient) -> tuple[UUID, UUID, UUID]:
        created = client.post("/api/projects", json={"name": "Chambre"})
        assert created.status_code == 201, created.text
        pid = UUID(created.json()["id"])
        base = rect_room_project().model_copy(update={"id": pid})
        level = only_level(base)
        project = apply_op(base, level.id, AddCamera(position=Point2(x=1.0, y=1.0)))
        store = client.app.state.store  # type: ignore[attr-defined]
        store.save(project)
        camera = next(e for e in only_level(project).elements.values() if isinstance(e, Camera))
        return pid, level.id, camera.id

    def person(self, email: str) -> TestClient:
        other = TestClient(self.client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        sign_in(other, self.mailer, email)
        return other

    def ask(self, client: TestClient | None = None) -> dict[str, object]:
        done = (client or self.client).post(
            f"/api/projects/{self.project_id}/levels/{self.level_id}/realistic",
            json={"camera_id": str(self.camera_id)},
        )
        assert done.status_code in (200, 201), done.text
        body: dict[str, object] = done.json()
        return body

    def close(self) -> None:
        self.service.engine.dispose()


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def test_migration_kan89_up_and_down(db_url: str) -> None:
    engine = make_engine(db_url)
    upgrade(engine)
    names = set(inspect(engine).get_table_names())
    assert {"render_requests", "render_workers"} <= names
    columns = {c["name"] for c in inspect(engine).get_columns("render_requests")}
    assert {"state", "expires_at", "lease_id", "attempts", "seconds", "error"} <= columns
    downgrade(engine, "kan55_0005")
    names = set(inspect(engine).get_table_names())
    assert not names & {"render_requests", "render_workers"}
    assert "invitations" in names
    upgrade(engine)
    assert "render_requests" in inspect(engine).get_table_names()
    engine.dispose()


def test_request_worker_and_image(world: World) -> None:  # noqa: PLR0915 - un parcours
    """Demande, prise par le poste, fichiers, dépôt, image servie ; journal sans identifiant."""
    first = world.ask()
    assert first["state"] == "pending" and first["image"] is False
    again = world.ask()  # double clic : la même demande
    assert again["id"] == first["id"]
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    assert [r["state"] for r in listing["requests"]] == ["pending"]
    assert listing["status"]["worker"] == "offline"
    # Battement : le poste est en ligne.
    beat = world.worker.post("/api/worker/heartbeat", json={"state": "idle"}, headers=WORKER)
    assert beat.status_code == 200 and beat.json()["poll_s"] >= 5
    status = world.client.get("/api/realistic/status").json()
    assert status == {"allowed": True, "worker": "online", "quota": 20, "used_today": 1}
    # Prise : la demande, puis rien.
    job = world.worker.post("/api/worker/claim", headers=WORKER)
    assert job.status_code == 200, job.text
    assert set(job.json()) == {"request_id", "lease", "created_at"}
    rid, lease = job.json()["request_id"], job.json()["lease"]
    assert rid == first["id"]
    assert world.worker.post("/api/worker/claim", headers=WORKER).status_code == 204
    leased = {**WORKER, "X-Render-Lease": lease}
    files = f"/api/worker/jobs/{rid}/files"
    project = world.worker.get(f"{files}/project", headers=leased)
    assert Project.model_validate_json(project.content).id == world.project_id
    assert Bake.model_validate_json(world.worker.get(f"{files}/bake", headers=leased).content)
    camera = RenderCamera.model_validate_json(
        world.worker.get(f"{files}/camera", headers=leased).content
    )
    assert camera.width_px > 0
    assert world.worker.get(f"{files}/autre", headers=leased).status_code == 422
    wrong = {**WORKER, "X-Render-Lease": str(uuid4())}
    assert world.worker.get(f"{files}/bake", headers=wrong).status_code == 404
    assert world.worker.get(f"{files}/bake", headers=WORKER).status_code == 404
    # En cours, côté interface.
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    assert listing["requests"][0]["state"] == "taken"
    # Dépôt : un PNG à la taille annoncée seulement.
    meta = WorkerResult(seconds=42.5, vram_peak_mib=4800, width=64, height=48, engine="fake")
    result = f"/api/worker/jobs/{rid}/result"
    bad = world.worker.post(
        result,
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", b"pas une image", "image/png")},
    )
    assert bad.status_code == 422
    wrong_size = world.worker.post(
        result,
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(32, 32), "image/png")},
    )
    assert wrong_size.status_code == 422
    done = world.worker.post(
        result,
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    assert done.status_code == 200, done.text
    assert done.json()["state"] == "done" and done.json()["image"] is True
    # Le bail est consommé : plus de second dépôt.
    twice = world.worker.post(
        result,
        headers=leased,
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    assert twice.status_code == 404
    listing = world.client.get(f"/api/projects/{world.project_id}/realistic").json()
    row = listing["requests"][0]
    assert row["state"] == "done" and row["image"] is True and row["seconds"] == 42.5
    image = world.client.get(f"/api/projects/{world.project_id}/realistic/{rid}/image")
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    # Journal des rendus : durée et résultat, aucune donnée personnelle ni identifiant.
    journal = world.client.app.state.settings.data_dir / "renders.jsonl"  # type: ignore[attr-defined]
    text = journal.read_text(encoding="utf-8")
    entry = json.loads(text.splitlines()[-1])
    assert entry["result"] == "done" and entry["seconds"] == 42.5
    for secret in (rid, str(world.project_id), str(world.augus_id), AUGUS, TOKEN):
        assert secret not in text
    world.close()


def test_failure_is_reported_in_french(world: World) -> None:
    world.ask()
    job = world.worker.post("/api/worker/claim", headers=WORKER).json()
    leased = {**WORKER, "X-Render-Lease": job["lease"]}
    url = f"/api/worker/jobs/{job['request_id']}/fail"
    assert world.worker.post(url, headers=leased, json={"reason": "rm -rf"}).status_code == 422
    assert world.worker.post(url, headers=WORKER, json={"reason": "vram"}).status_code == 404
    assert world.worker.post(url, headers=leased, json={"reason": "vram"}).status_code == 204
    row = world.client.get(f"/api/projects/{world.project_id}/realistic").json()["requests"][0]
    assert row["state"] == "failed"
    assert row["error"] == "Le poste de rendu manquait de mémoire : réessayez plus tard."
    world.close()


def test_other_accounts_can_neither_create_nor_see(world: World) -> None:
    """Un autre compte, même éditeur du projet d'augus : ni création, ni liste, ni image."""
    world.ask()
    job = world.worker.post("/api/worker/claim", headers=WORKER).json()
    meta = WorkerResult(seconds=1.0, vram_peak_mib=0, width=64, height=48, engine="fake")
    world.worker.post(
        f"/api/worker/jobs/{job['request_id']}/result",
        headers={**WORKER, "X-Render-Lease": job["lease"]},
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(), "image/png")},
    )
    marie = world.person("marie@example.org")
    user = world.service.user_by_email("marie@example.org")
    assert user is not None
    world.service.set_member(world.project_id, user.id, "editor", added_by=world.augus_id)
    assert marie.get(f"/api/projects/{world.project_id}").status_code == 200
    base = f"/api/projects/{world.project_id}"
    create = marie.post(
        f"{base}/levels/{world.level_id}/realistic", json={"camera_id": str(world.camera_id)}
    )
    assert create.status_code == 403
    assert create.json()["detail"] == "Rendus réalistes : bientôt en ligne"
    assert marie.get(f"{base}/realistic").status_code == 403
    assert marie.get(f"{base}/realistic/{job['request_id']}/image").status_code == 403
    assert marie.get("/api/realistic/status").json()["allowed"] is False
    # Son propre projet ne change rien.
    pid, lid, cid = world.new_project(marie)
    mine = marie.post(f"/api/projects/{pid}/levels/{lid}/realistic", json={"camera_id": str(cid)})
    assert mine.status_code == 403
    # Rien n'a été créé pour elle.
    assert world.service.engine is not None
    queue: RenderQueue = world.client.app.state.render_queue  # type: ignore[attr-defined]
    assert queue.used_today(user.id) == 0
    world.close()


def test_new_routes_are_guarded_and_refuse_without_session_or_token(world: World) -> None:
    assert set(PROJECT_ROUTES) >= USER_ROUTES | {("POST", L + "/realistic")}
    assert {k: GLOBAL_ROUTES[k] for k in WORKER_ROUTES} == dict.fromkeys(WORKER_ROUTES, "worker")
    assert GLOBAL_ROUTES[("GET", "/api/realistic/status")] == "user"
    anonymous = TestClient(world.client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    base = f"/api/projects/{world.project_id}"
    rid = uuid4()
    for method, url in (
        ("GET", "/api/realistic/status"),
        ("GET", f"{base}/realistic"),
        ("GET", f"{base}/realistic/{rid}/image"),
        ("POST", f"{base}/levels/{world.level_id}/realistic"),
    ):
        assert anonymous.request(method, url).status_code == 401, url
        # Le jeton du poste n'ouvre pas les routes des personnes.
        assert anonymous.request(method, url, headers=WORKER).status_code == 401, url
    for method, url in (
        ("POST", "/api/worker/heartbeat"),
        ("POST", "/api/worker/claim"),
        ("GET", f"/api/worker/jobs/{rid}/files/bake"),
        ("POST", f"/api/worker/jobs/{rid}/result"),
        ("POST", f"/api/worker/jobs/{rid}/fail"),
    ):
        assert anonymous.request(method, url).status_code == 401, url
        # Une session (même celle d'augus) n'ouvre pas l'API du poste.
        assert world.client.request(method, url).status_code == 401, url
        bad = {"Authorization": "Bearer " + secrets.token_urlsafe(32)}
        assert anonymous.request(method, url, headers=bad).status_code == 401, url
    # Le jeton du poste n'ouvre rien d'autre.
    assert anonymous.get("/api/projects", headers=WORKER).status_code == 401
    assert anonymous.get("/api/auth/me", headers=WORKER).status_code == 401
    # Envoi sans longueur annoncée, ou trop gros : refusé avant la lecture du corps.
    huge = {**WORKER, "Content-Length": str(64 * 1024 * 1024)}
    assert world.worker.post("/api/worker/claim", headers=huge).status_code == 413
    world.close()


def test_worker_api_closed_without_configured_hash(tmp_path: Path, migrated_url: str) -> None:
    client, service, _ = accounts_client(tmp_path, migrated_url)
    worker = TestClient(client.app, base_url=ORIGIN)
    assert worker.post("/api/worker/claim", headers=WORKER).status_code == 401
    service.engine.dispose()


def test_daily_quota(tmp_path: Path, migrated_url: str) -> None:
    world = World(tmp_path, migrated_url, daily_quota=1)
    world.ask()
    world.worker.post("/api/worker/claim", headers=WORKER)  # la première n'est plus en attente
    pid, lid, cid = world.new_project(world.client)
    over = world.client.post(
        f"/api/projects/{pid}/levels/{lid}/realistic", json={"camera_id": str(cid)}
    )
    assert over.status_code == 429
    assert "du jour" in over.json()["detail"]
    unknown = world.client.post(
        f"/api/projects/{pid}/levels/{lid}/realistic", json={"camera_id": str(uuid4())}
    )
    assert unknown.status_code == 404
    world.close()


# --- la file seule (horloge réglable) ------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _queue(url: str, tmp_path: Path, **config: object) -> tuple[RenderQueue, Clock, UUID, UUID]:
    repo = AccountsRepo(make_engine(url))
    user = repo.create_user("a@example.org")
    project_id = uuid4()
    repo.add_project(project_id, user.id, "P")
    clock = Clock()
    queue = RenderQueue(
        repo.engine,
        RealisticConfig(accounts=frozenset({user.id}), **config),  # type: ignore[arg-type]
        tmp_path,
        clock=clock,
    )
    return queue, clock, user.id, project_id


def test_expiry_and_abandoned_claims(migrated_url: str, tmp_path: Path) -> None:
    queue, clock, user, project = _queue(migrated_url, tmp_path, expire_h=2.0)
    old = queue.create(user, project, uuid4(), uuid4())
    clock.now += timedelta(hours=1)
    fresh = queue.create(user, project, uuid4(), uuid4())
    clock.now += timedelta(hours=1, seconds=1)
    queue.sweep()
    assert queue.get(old.id).state == "expired"  # type: ignore[union-attr]
    assert queue.get(fresh.id).state == "pending"  # type: ignore[union-attr]
    # Une prise sans nouvelles repasse en attente (avant son expiration)…
    job = queue.claim()
    assert job is not None and job.request_id == fresh.id
    assert queue.claim() is None
    clock.now += TAKE_TTL + timedelta(seconds=1)
    queue.sweep()
    again = queue.get(fresh.id)
    assert again is not None and again.state == "pending" and again.lease_id is None
    # … l'ancien bail ne vaut plus rien, et une attente trop longue expire.
    with pytest.raises(LeaseError):
        queue.leased(fresh.id, job.lease)
    clock.now += timedelta(hours=1)
    assert queue.claim() is None
    assert queue.get(fresh.id).state == "expired"  # type: ignore[union-attr]
    lines = (tmp_path / "renders.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(x)["result"] for x in lines] == ["expired", "expired"]


def test_abandoned_claim_goes_back_then_fails(migrated_url: str, tmp_path: Path) -> None:
    queue, clock, user, project = _queue(migrated_url, tmp_path, expire_h=100.0)
    row = queue.create(user, project, uuid4(), uuid4())
    for _ in range(MAX_ATTEMPTS):
        job = queue.claim()
        assert job is not None and job.request_id == row.id
        clock.now += TAKE_TTL + timedelta(seconds=1)
        queue.sweep()
    final = queue.get(row.id)
    assert final is not None and final.state == "failed" and final.error == "abandoned"
    assert queue.claim() is None
    lines = (tmp_path / "renders.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["result"] == "failed"


def test_claim_is_atomic(migrated_url: str, tmp_path: Path) -> None:
    queue, _clock, user, project = _queue(migrated_url, tmp_path)
    ids = {queue.create(user, project, uuid4(), uuid4()).id for _ in range(6)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: queue.claim(), range(12)))
    taken = [j.request_id for j in jobs if j is not None]
    assert len(taken) == len(set(taken)) == 6
    assert set(taken) == ids


def test_quota_and_allowlist_in_queue(migrated_url: str, tmp_path: Path) -> None:
    queue, clock, user, project = _queue(migrated_url, tmp_path, daily_quota=2)
    queue.create(user, project, uuid4(), uuid4())
    queue.create(user, project, uuid4(), uuid4())
    with pytest.raises(QuotaExceededError):
        queue.create(user, project, uuid4(), uuid4())
    clock.now += timedelta(hours=24, seconds=1)
    queue.create(user, project, uuid4(), uuid4())
    # Un compte retiré de la liste blanche : ses demandes ne sont plus prises.
    queue.config = RealisticConfig(accounts=frozenset({uuid4()}))
    assert queue.claim() is None


def test_worker_status_follows_heartbeat(migrated_url: str, tmp_path: Path) -> None:
    queue, clock, _user, _project = _queue(migrated_url, tmp_path)
    assert queue.worker_status() == "offline"
    queue.beat("idle")
    assert queue.worker_status() == "online"
    queue.beat("paused")
    assert queue.worker_status() == "paused"
    queue.beat("busy")
    assert queue.worker_status() == "busy"
    clock.now += timedelta(minutes=2)
    assert queue.worker_status() == "offline"


def test_config_parsing_never_echoes_secrets() -> None:
    uid = uuid4()
    assert parse_accounts(f" {uid}, ") == frozenset({uid})
    with pytest.raises(ValueError, match="uuid"):
        parse_accounts("augus@example.org")
    token = secrets.token_urlsafe(32)
    with pytest.raises(ValueError) as caught:
        parse_hashes(token)
    assert token not in str(caught.value)
    digest = token_hash(token)
    assert parse_hashes(digest.upper()) == (digest,)
    config = RealisticConfig.from_env(
        {
            "OPENPLAN_RENDER_ACCOUNTS": str(uid),
            "OPENPLAN_WORKER_TOKEN_SHA256": digest,
            "OPENPLAN_RENDER_EXPIRE_H": "12,5",
            "OPENPLAN_RENDER_DAILY_QUOTA": "5",
        }
    )
    assert config.allows(uid) and config.expire_h == 12.5 and config.daily_quota == 5
    assert digest not in repr(config)
