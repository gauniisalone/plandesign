"""Correctifs de l'intégration des comptes (PO, 18 oct.) : (a) la liste des projets ne dit pas
le chemin du dossier sur le serveur quand l'API est exposée ; (b) l'état et l'annulation d'une
tâche ne répondent que pour une tâche de ce projet. (c, la garde de l'annulation, est vérifiée
par le parcours des routes de tests/accounts.)"""

from __future__ import annotations

import threading
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from pydantic import BaseModel

from openplan.api.app import create_app
from openplan.api.settings import Settings

TOKEN = "t" * 24


class _Done(BaseModel):
    ok: bool = True


def test_project_list_hides_the_server_path_when_exposed(tmp_path: Path) -> None:
    local = create_app(settings=Settings(data_dir=tmp_path / "local"))
    with TestClient(local) as c:
        c.post("/api/projects", json={"name": "ici"})
        assert c.get("/api/projects").json()[0]["path"]
    exposed = create_app(
        settings=Settings(data_dir=tmp_path / "vps", host="0.0.0.0", api_tokens={"augus": TOKEN})
    )
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(exposed) as c:
        assert c.post("/api/projects", json={"name": "là-bas"}, headers=auth).status_code == 201
        listed = c.get("/api/projects", headers=auth).json()
        assert listed and all(s["path"] is None for s in listed)


def test_a_job_answers_only_for_its_own_project(tmp_path: Path) -> None:
    app = create_app(settings=Settings(data_dir=tmp_path / "data", device="cpu"))
    gate = threading.Event()
    with TestClient(app) as c:
        mine = c.post("/api/projects", json={"name": "a"}).json()["id"]
        other = c.post("/api/projects", json={"name": "b"}).json()["id"]

        def block() -> _Done:
            assert gate.wait(30)
            return _Done()

        jobs, pid = app.state.jobs, UUID(mine)
        common = {"device": "cpu", "expected_s": 5, "project_id": pid}
        running = jobs.submit("render", block, message="x", **common)
        queued = jobs.submit("render", block, message="y", **common)
        base, elsewhere = f"/api/projects/{mine}/jobs", f"/api/projects/{other}/jobs"
        try:
            assert c.get(f"{base}/{running.id}").status_code == 200
            # Même identifiant, autre projet : inconnu (404), et rien n'est annulé.
            assert c.get(f"{elsewhere}/{running.id}").status_code == 404
            assert c.delete(f"{elsewhere}/{queued.id}").status_code == 404
            assert c.get(f"{base}/{queued.id}").json()["status"] == "queued"
            assert c.delete(f"{base}/{queued.id}").json()["status"] == "cancelled"
        finally:
            gate.set()
