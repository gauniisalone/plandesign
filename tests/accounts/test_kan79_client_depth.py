"""KAN-79 : la profondeur calculée par le navigateur. Écriture d'éditeur (garde E), bail du
plan (KAN-11), place disque ; carte validée (taille, valeurs finies, plage) ou 422 avec la
raison ; rangée comme celle du serveur, puis l'estimation du plan la relit sans modèle. Le
modèle du navigateur est servi par l'API (session requise, ETag, 304)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openplan.adapters.neural.web_depth import ONNX_NAME, SHA_SUFFIX, WEB_MODEL_ID
from openplan.adapters.neural.weights import weights_dir
from openplan.api.accounts.lease import LEASED_ROUTES
from openplan.api.accounts.quota import UPLOADS
from openplan.api.guards import PROJECT_ROUTES
from openplan.pipeline.client_depth import grid_shape
from openplan.pipeline.reference import depth_paths, load_depth
from tests.accounts.helpers import ADMIN, ORIGIN, accounts_client, sign_in
from tests.accounts.test_b2a_live import _client
from tests.accounts.test_kan51_comments import _team
from tests.synthetic_depth import SyntheticCamera, box_faces, render_depth

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"
ROUTE = "/api/projects/{project_id}/levels/{level_id}/images/{image_id}/depth"


@pytest.fixture(autouse=True)
def _many_sign_ins(monkeypatch: pytest.MonkeyPatch) -> None:
    from openplan.api import security

    monkeypatch.setattr(security, "AUTH_RATE_LIMIT", 100)


def box_grid(width: int = 1280, height: int = 960) -> np.ndarray[Any, np.dtype[np.float16]]:
    """Ce que le navigateur enverrait pour la photo de la fixture : profondeur exacte de la
    pièce 4 × 3, un pixel sur deux, en float16."""
    camera = SyntheticCamera(
        position=(0.7, 0.6, 1.4),
        yaw=0.8727,
        pitch=-0.2094,
        focal_px=900.0,
        width_px=width,
        height_px=height,
    )
    full = render_depth(box_faces(4.0 - 0.15, 3.0 - 0.15, 2.5), camera)
    return np.ascontiguousarray(full[::2, ::2].astype("<f2"))


def _put(
    client: TestClient, pid: str, lid: str, iid: str, raw: bytes, w: int = 1280, h: int = 960
) -> Any:
    return client.put(
        f"/api/projects/{pid}/levels/{lid}/images/{iid}/depth",
        params={"image_width": w, "image_height": h},
        content=raw,
        headers={"Content-Type": "application/octet-stream", "Origin": ORIGIN},
    )


def _photo(client: TestClient, pid: str) -> str:
    sent = client.post(
        f"/api/projects/{pid}/images",
        files={"file": ("piece.png", FIXTURE.read_bytes(), "image/png")},
    )
    assert sent.status_code == 201, sent.text
    image_id: str = sent.json()["image"]["id"]
    return image_id


def test_the_route_is_an_editor_write_under_the_plan_lease(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, m, pid, _ = _team(tmp_path, migrated_url)
    lid = next(iter(owner.get(f"/api/projects/{pid}").json()["levels"]))
    unknown = "00000000-0000-4000-8000-000000000000"
    for role in ("viewer", "commenter"):
        assert _put(m[role], pid, lid, unknown, b"\0\0").status_code == 403
    # L'éditeur passe la garde : la photo est inconnue (404), rien d'autre.
    assert _put(m["editor"], pid, lid, unknown, b"\0\0").status_code == 404
    assert PROJECT_ROUTES[("PUT", ROUTE)] >= 3
    assert ("PUT", ROUTE) in LEASED_ROUTES and ("PUT", ROUTE) in UPLOADS


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_checked_stored_then_the_plan_is_built_without_a_model(
    tmp_path: Path, migrated_url: str
) -> None:
    owner, _service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "Pièce"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    iid = _photo(owner, pid)
    grid = box_grid()
    assert grid.shape == grid_shape(1280, 960) == (480, 640)

    # Refus, chacun avec sa raison.
    def refused(raw: bytes, w: int = 1280, h: int = 960) -> str:
        r = _put(owner, pid, lid, iid, raw, w, h)
        assert r.status_code == 422, r.text
        detail: str = r.json()["detail"]
        return detail

    assert "taille de photo différente" in refused(grid.tobytes(), 960, 1280)
    assert "valeurs float16 attendues" in refused(grid[:-1].tobytes())
    bad = grid.copy()
    bad[10, 10] = np.nan
    assert "non finies" in refused(bad.tobytes())
    bad = grid.copy()
    bad[10, 10] = 35.0
    assert "plage plausible" in refused(bad.tobytes())
    bad = grid.copy()
    bad[10, 10] = -1.0
    assert "plage plausible" in refused(bad.tobytes())
    assert "uniforme" in refused(np.full_like(grid, 2.5).tobytes())
    store = owner.app.state.store  # type: ignore[attr-defined]
    project_dir = store.project_dir(UUID(pid))
    assert not depth_paths(project_dir, UUID(iid))[0].exists()

    # Acceptée : rangée comme celle du serveur (KAN-66), relue par la mesure de référence.
    done = _put(owner, pid, lid, iid, grid.tobytes())
    assert done.status_code == 200, done.text
    assert done.json() == {"image_id": iid, "depth_size": [640, 480], "model_id": WEB_MODEL_ID}
    stored = load_depth(project_dir, UUID(iid))
    assert stored is not None and stored.depth.shape == (480, 640)
    assert stored.scale == (0.5, 0.5)
    np.testing.assert_array_equal(stored.depth, grid.astype(np.float32))
    npy = depth_paths(project_dir, UUID(iid))[0]
    before = npy.read_bytes()

    # L'estimation repart sans modèle (aucun poids installé ici) : le plan est construit.
    job = owner.post(f"/api/projects/{pid}/levels/{lid}/images/{iid}/autoplan")
    assert job.status_code == 202, job.text
    status = _wait(owner, pid, job.json()["id"])
    assert status["status"] == "done", status
    run = status["result"]["run"]
    assert run["method"] in ("depth", "attic"), run
    stages = {s["key"]: s["status"] for s in run["stages"]}
    assert stages.get("depth") == "done", run["stages"]
    walls = [
        e for e in run["project"]["levels"][lid]["elements"].values() if e.get("type") == "wall"
    ]
    assert len(walls) >= 3
    assert not any("Analyse plus fine non installée" in n for n in run["notices"])
    # La profondeur du navigateur n'est pas réécrite par l'estimation.
    assert npy.read_bytes() == before


def _wait(client: TestClient, pid: str, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 120
    while True:
        status: dict[str, Any] = client.get(f"/api/projects/{pid}/jobs/{job_id}").json()
        if status["status"] not in ("queued", "running") or time.monotonic() > deadline:
            return status
        time.sleep(0.2)


def test_the_upload_respects_the_plan_lease(tmp_path: Path, migrated_url: str) -> None:
    owner, _service, mailer = accounts_client(tmp_path, migrated_url, signup="open")
    sign_in(owner, mailer, ADMIN)
    project = owner.post("/api/projects", json={"name": "À deux"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    token = owner.post(f"/api/projects/{pid}/invitations", json={"role": "editor"}).json()
    other = _client(owner.app)
    sign_in(other, mailer, "lea.kan79@example.org")
    assert (
        other.post("/api/auth/invitation/accept", json={"token": token["token"]}).status_code == 200
    )
    assert other.post(f"/api/projects/{pid}/levels/{lid}/lease").status_code == 200
    unknown = "00000000-0000-4000-8000-000000000000"
    refused = _put(owner, pid, lid, unknown, b"\0\0")
    assert refused.status_code == 409 and refused.json()["detail"].endswith("modifie ce plan")


def test_the_browser_model_is_served_with_etag(tmp_path: Path, migrated_url: str) -> None:
    models = tmp_path / "weights"
    owner, _service, mailer = accounts_client(
        tmp_path, migrated_url, signup="open", models_dir=models
    )
    anonymous = _client(owner.app)
    assert anonymous.get("/api/web-models/depth").status_code == 401
    sign_in(owner, mailer, ADMIN)
    assert owner.get("/api/web-models/depth").status_code == 404
    assert WEB_MODEL_ID not in owner.get("/api/health").json()["weights"]
    folder = weights_dir(WEB_MODEL_ID, models)
    folder.mkdir(parents=True)
    payload = b"onnx" * 1000
    (folder / ONNX_NAME).write_bytes(payload)
    digest = "a" * 64
    (folder / (ONNX_NAME + SHA_SUFFIX)).write_text(f"{digest}  {ONNX_NAME}\n", encoding="utf-8")
    assert WEB_MODEL_ID in owner.get("/api/health").json()["weights"]
    got = owner.get("/api/web-models/depth")
    assert got.status_code == 200 and got.content == payload
    assert got.headers["etag"] == f'"{digest}"'
    assert "immutable" in got.headers["cache-control"]
    assert got.headers["content-length"] == str(len(payload))
    same = owner.get("/api/web-models/depth", headers={"If-None-Match": f'"{digest}"'})
    assert same.status_code == 304 and same.content == b""
    stale = owner.get("/api/web-models/depth", headers={"If-None-Match": '"autre"'})
    assert stale.status_code == 200 and stale.content == payload
