"""Contrôle de sécurité D2 : en mode exposé, un calcul indisponible ne dit pas de chemin du
serveur (409 d'une route, erreur d'une tâche de fond) ; sur la boucle locale, le message avec
la commande à lancer reste."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from openplan.adapters.protocols import AdapterUnavailableError
from openplan.api.app import create_app
from openplan.api.jobs import JobRegistry
from openplan.api.public_errors import PATH_MASK, UNAVAILABLE, public_detail
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore
from tests.factories import rect_room_project

SECRET_PATH = "/app/models/weights/depth_anything_v2_metric_indoor_small_1.0"
TOKEN = "marie-token-0123456789abcdef"


def test_public_detail_hides_server_paths_only_when_exposed() -> None:
    exc = AdapterUnavailableError(f"poids absents pour depth ({SECRET_PATH}). Téléchargement…")
    assert SECRET_PATH in public_detail(exc, exposed=False)
    assert public_detail(exc, exposed=True) == UNAVAILABLE
    other = ValueError(f"fichier illisible : {SECRET_PATH}/x.npy, le 14/10/2026")
    shown = public_detail(other, exposed=True)
    assert SECRET_PATH not in shown and PATH_MASK in shown and "14/10/2026" in shown
    windows = ValueError(r"absent : C:\data\projets\x.png")
    assert "projets" not in public_detail(windows, exposed=True)


def test_job_error_is_masked_when_exposed() -> None:
    def boom() -> object:
        raise AdapterUnavailableError(f"poids absents ({SECRET_PATH})")

    for exposed, expected in ((True, UNAVAILABLE), (False, SECRET_PATH)):
        registry = JobRegistry(exposed=exposed)
        job = registry.submit("x", boom, device="cpu", expected_s=1, message="")  # type: ignore[arg-type]
        for _ in range(200):
            status = registry.get(job.id)
            if status.status != "running":
                break
            time.sleep(0.01)
        assert status.status == "failed" and expected in (status.error or "")
        registry.shutdown()


def test_route_409_has_no_server_path_when_exposed(tmp_path: Path) -> None:
    """La profondeur sans poids : 409 ; exposé, la phrase fixe ; local, la commande."""
    project = rect_room_project(4.0, 3.0)
    store = ProjectStore(tmp_path / "data")
    store.save(project)

    def depth_status(settings: Settings, headers: dict[str, str]) -> tuple[int, str]:
        with TestClient(create_app(store=store, settings=settings)) as c:
            up = c.post(
                f"/api/projects/{project.id}/images",
                files={"file": ("p.png", _png(), "image/png")},
                headers=headers,
            )
            image = up.json()["image"]["id"]
            r = c.post(f"/api/projects/{project.id}/images/{image}/depth", headers=headers)
            return r.status_code, r.text

    weights = tmp_path / "weights_absents"
    exposed = Settings(
        data_dir=tmp_path / "data",
        host="0.0.0.0",
        api_tokens={"marie": TOKEN},
        models_dir=weights,
    )
    code, text = depth_status(exposed, {"Authorization": f"Bearer {TOKEN}"})
    assert code == 409 and UNAVAILABLE in text and str(weights) not in text
    local = Settings(data_dir=tmp_path / "data", models_dir=weights)
    code, text = depth_status(local, {})
    assert code == 409 and "openplan models pull" in text


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()
