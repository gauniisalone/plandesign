"""File d'attente par l'API : une photo déposée pendant un calcul d'un autre projet donne quand
même son plan, sans autre geste ; deux demandes du même chemin pendant l'attente n'en font
qu'une ; une tâche en attente s'annule. Le calcul qui occupe le fil est simulé."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore

FIXTURE_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


class _Done(BaseModel):
    ok: bool = True


def _block(app: object, gate: threading.Event) -> None:
    """Un calcul lourd d'un autre projet occupe le fil jusqu'à `gate`."""

    def work() -> _Done:
        assert gate.wait(30)
        return _Done()

    app.state.jobs.submit("render", work, device="cpu", expected_s=60, message="occupé")  # type: ignore[attr-defined]


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")
def test_photo_dropped_during_a_job_still_builds_the_plan(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", device="cpu", models_dir=tmp_path / "vide")
    app = create_app(store=ProjectStore(tmp_path / "data"), settings=settings)
    gate = threading.Event()
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "file"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _block(app, gate)
        image = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("p.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
        ).json()["image"]
        url = f"/api/projects/{pid}/levels/{lid}/images/{image['id']}/autoplan"
        first = c.post(url)
        assert first.status_code == 202, first.text
        job = first.json()
        assert job["status"] == "queued" and job["queue_position"] == 1
        # Même dépôt pendant l'attente : la même tâche, pas une seconde.
        again = c.post(url).json()
        assert again["id"] == job["id"] and again["queue_position"] == 1
        assert c.get("/api/jobs/running").json()["queued"] == 1
        gate.set()  # le calcul en cours finit : la construction du plan démarre seule
        for _ in range(600):
            job = c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
            if job["status"] in {"done", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        saved = c.get(f"/api/projects/{pid}").json()
        kinds = [e["type"] for e in saved["levels"][lid]["elements"].values()]
        assert kinds.count("wall") >= 4
        assert c.get("/api/jobs/running").json() == {"running": False}


def test_autopilot_twice_while_waiting_is_one_job_and_can_be_cancelled(tmp_path: Path) -> None:
    app = create_app(settings=Settings(data_dir=tmp_path / "data", device="cpu"))
    gate = threading.Event()
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "pilote"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _block(app, gate)
        url = f"/api/projects/{pid}/levels/{lid}/autopilot?gpu=false"
        a = c.post(url, json={"instruction": "salon"})
        assert a.status_code == 202, a.text
        b = c.post(url, json={"instruction": "salon scandinave"}).json()
        assert b["id"] == a.json()["id"] and b["status"] == "queued"
        assert c.get("/api/jobs/running").json()["queued"] == 1
        r = c.delete(f"/api/projects/{pid}/jobs/{b['id']}")
        assert r.status_code == 200 and r.json()["status"] == "cancelled"
        assert c.delete(f"/api/projects/{pid}/jobs/{b['id']}").status_code == 409
        assert "queued" not in c.get("/api/jobs/running").json()
        gate.set()
