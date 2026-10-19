"""KAN-12 : place disque par compte. Mesure côté serveur, refus 413 avant toute écriture,
invité compté chez le propriétaire, suppression qui libère la place, calcul refusé avant son
lancement, mode local et 0 sans limite."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from openplan.api.accounts.quota import LAUNCHES, MB, UPLOADS
from openplan.api.app import create_app
from openplan.api.guards import PROJECT_ROUTES
from openplan.api.settings import Settings, parse_quota
from tests.accounts.helpers import ADMIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client


def _png(size_px: int) -> bytes:
    """Une image PNG peu compressible d'environ size_px² × 3 octets."""
    import random

    rng = random.Random(size_px)
    image = Image.frombytes(
        "RGB", (size_px, size_px), bytes(rng.getrandbits(8) for _ in range(size_px * size_px * 3))
    )
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _setup(tmp_path: Path, url: str, quota_mb: int) -> tuple[TestClient, TestClient, str]:
    owner, _service, mailer = accounts_client(tmp_path, url, signup="open", quota_mb=quota_mb)
    sign_in(owner, mailer, ADMIN)
    pid = owner.post("/api/projects", json={"name": "Grenier"}).json()["id"]
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()["token"]
    guest = _client(owner.app)
    sign_in(guest, mailer, "lea@example.org")
    assert guest.post("/api/auth/invitation/accept", json={"token": token}).status_code == 200
    return owner, guest, pid


def _upload(client: TestClient, pid: str, data: bytes) -> object:
    return client.post(
        f"/api/projects/{pid}/media", files={"file": ("photo.png", data, "image/png")}
    )


def test_usage_is_measured_and_freed(tmp_path: Path, migrated_url: str) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=5)
    before = owner.get("/api/auth/usage").json()
    assert before["quota_bytes"] == 5 * MB
    added = _upload(owner, pid, _png(400))
    assert added.status_code == 201  # type: ignore[attr-defined]
    after = owner.get("/api/auth/usage").json()["used_bytes"]
    assert after > before["used_bytes"]
    # L'invitée ne paie rien : le projet est compté chez le propriétaire.
    assert guest.get("/api/auth/usage").json()["used_bytes"] == 0
    media_id = added.json()["id"]  # type: ignore[attr-defined]
    assert owner.delete(f"/api/projects/{pid}/media/{media_id}").status_code in (200, 204)
    assert owner.get("/api/auth/usage").json()["used_bytes"] < after


def test_an_upload_over_the_limit_is_refused_before_any_write(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, guest, pid = _setup(tmp_path, migrated_url, quota_mb=1)
    big = _png(700)  # plus d'un Mo
    assert len(big) > MB
    used = owner.get("/api/auth/usage").json()["used_bytes"]
    refused = _upload(owner, pid, big)
    assert refused.status_code == 413  # type: ignore[attr-defined]
    assert "Votre espace est plein" in refused.json()["detail"]  # type: ignore[attr-defined]
    assert "sur 1 Mo" in refused.json()["detail"]  # type: ignore[attr-defined]
    assert owner.get("/api/auth/usage").json()["used_bytes"] == used  # rien d'écrit
    assert owner.get(f"/api/projects/{pid}/media").json() == []
    # L'invitée : refusée aussi, sans voir la place du propriétaire.
    hers = _upload(guest, pid, big)
    assert hers.status_code == 413  # type: ignore[attr-defined]
    detail = hers.json()["detail"]  # type: ignore[attr-defined]
    assert "propriétaire" in detail and "Mo" not in detail
    # Un petit envoi passe.
    assert _upload(owner, pid, _png(40)).status_code == 201  # type: ignore[attr-defined]


def test_a_computation_is_refused_before_launch_when_full(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=1)
    project = owner.get(f"/api/projects/{pid}").json()
    level = next(iter(project["levels"]))
    # Remplir au-delà de la limite (écriture directe : ce qu'un calcul aurait laissé).
    store = owner.app.state.store  # type: ignore[attr-defined]
    (store.project_dir(project["id"]) / "runs").mkdir(exist_ok=True)
    (store.project_dir(project["id"]) / "runs" / "plein.bin").write_bytes(b"x" * (MB + 1))
    refused = owner.post(f"/api/projects/{pid}/levels/{level}/render")
    assert refused.status_code == 413
    assert "Votre espace est plein" in refused.json()["detail"]


def test_a_computation_keeps_a_reserve_before_launch(tmp_path: Path, migrated_url: str) -> None:
    """Décision (b) : un rendu ne dépasse jamais en cours de route. Avec moins que la réserve
    de place restante (LAUNCH_RESERVE), il est refusé avant son lancement."""
    from openplan.api.accounts.quota import LAUNCH_RESERVE

    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=30)
    project = owner.get(f"/api/projects/{pid}").json()
    level = next(iter(project["levels"]))
    store = owner.app.state.store  # type: ignore[attr-defined]
    runs = store.project_dir(project["id"]) / "runs"
    runs.mkdir(exist_ok=True)
    # Il reste moins que la réserve : refusé, alors que la limite n'est pas encore atteinte.
    (runs / "presque.bin").write_bytes(b"x" * (30 * MB - LAUNCH_RESERVE + MB))
    assert owner.get("/api/auth/usage").json()["used_bytes"] < 30 * MB
    refused = owner.post(f"/api/projects/{pid}/levels/{level}/render")
    assert refused.status_code == 413


def test_unlimited_and_local_mode(tmp_path: Path, migrated_url: str) -> None:
    owner, _guest, pid = _setup(tmp_path, migrated_url, quota_mb=0)
    assert owner.get("/api/auth/usage").json()["quota_bytes"] is None
    assert _upload(owner, pid, _png(700)).status_code == 201  # type: ignore[attr-defined]
    local = TestClient(create_app(settings=Settings(data_dir=tmp_path / "local", quota_mb=1)))
    lpid = local.post("/api/projects", json={"name": "Seul"}).json()["id"]
    assert _upload(local, lpid, _png(700)).status_code == 201  # type: ignore[attr-defined]


def test_reading_my_space_never_uses_up_the_sign_in_attempts(
    tmp_path: Path, migrated_url: str
) -> None:
    """L'atelier lit /api/auth/usage à chaque changement du projet : ces lectures ne comptent
    pas dans la limite des tentatives de connexion (10 par minute et par adresse)."""
    owner, _guest, _pid = _setup(tmp_path, migrated_url, quota_mb=5)
    for _ in range(15):
        assert owner.get("/api/auth/usage").status_code == 200
    again = owner.post("/api/auth/email/start", json={"email": "autre@example.org"})
    assert again.status_code == 202
    # Sans session : 401, comme toute route des comptes.
    assert _client(owner.app).get("/api/auth/usage").status_code == 401


def test_setting_and_route_tables() -> None:
    assert parse_quota(None) == 500 and parse_quota("") == 500
    assert parse_quota("0") == 0 and parse_quota(" 250 ") == 250
    for bad in ("-1", "1.5", "beaucoup"):
        with pytest.raises(ValueError, match="OPENPLAN_QUOTA_MB"):
            parse_quota(bad)
    env = {"OPENPLAN_QUOTA_MB": "42"}
    assert Settings.from_env(env, dotenv=None).quota_mb == 42
    assert set(PROJECT_ROUTES) >= UPLOADS | LAUNCHES
