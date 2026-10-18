"""T7d1 : GET /api/jobs/running dit si l'utilisateur a une tâche lourde en cours (sa sorte et
depuis quand, rien d'autre) ; sert à isoler les e2e lourds."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel

from openplan.api.app import create_app
from openplan.api.settings import Settings


class _Done(BaseModel):
    ok: bool = True


def test_running_job_is_reported_then_cleared(tmp_path: Path) -> None:
    app = create_app(settings=Settings(data_dir=tmp_path / "data"))
    client = TestClient(app)
    assert client.get("/api/jobs/running").json() == {"running": False}
    release = threading.Event()

    def work() -> _Done:
        release.wait(10)
        return _Done()

    app.state.jobs.submit("autopilot", work, device="cpu", expected_s=5, message="test")
    body = client.get("/api/jobs/running").json()
    assert body["running"] is True and body["kind"] == "autopilot"
    assert set(body) == {"running", "kind", "elapsed_s"}
    # File d'attente : la tâche suivante attend ; `queued` le dit tant qu'elle n'a pas démarré.
    app.state.jobs.submit("render", work, device="cpu", expected_s=5, message="test")
    assert client.get("/api/jobs/running").json()["queued"] == 1
    release.set()
    for _ in range(50):
        if client.get("/api/jobs/running").json() == {"running": False}:
            break
        threading.Event().wait(0.05)
    assert client.get("/api/jobs/running").json() == {"running": False}
