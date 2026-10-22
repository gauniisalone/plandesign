"""KAN-75, Must have 2 : aller-retour réel. Le code actuel écrit des projets qui portent tout ce
que le format a gagné depuis la production ; `downgrade` les redescend ; le code de production
(3da4d52, extrait par `git archive`, sans worktree) les relit : liste, plan, espace de travail,
commentaires, activité et cloche, sans aucun 409 ni 500. Sauté sans historique git (clone
superficiel de la CI)."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.events import ProjectEvents
from openplan.pipeline.reference import camera_basis, with_basis
from openplan.scene.downgrade import downgrade_tree
from openplan.schemas import events as event_schema
from openplan.schemas.events import ProjectEvent
from openplan.schemas.scene import ImageRef, ReferenceSegment

PRODUCTIONS = [("3da4d52", "0.16.0"), ("a1c630f", "0.17.0")]
"""Les versions vers lesquelles on revient : MEP 5 (21 oct. 2026, schéma 0.16.0) et MEP 13
(22 oct. 2026, schéma 0.17.0, la production d'avant les cartes reliées de KAN-91)."""
REPO = Path(__file__).parents[2]

OLD_READER = r"""
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from openplan.api.app import create_app
from openplan.api.settings import Settings
d = Path(sys.argv[1])
c = TestClient(create_app(settings=Settings(data_dir=d)), raise_server_exceptions=False)
out = {"liste": c.get("/api/projects").status_code}
for pid in json.loads((d / "ids.json").read_text()):
    for suffix in ("", "/board", "/comments", "/activity"):
        out[pid[:8] + (suffix or "/")] = c.get(f"/api/projects/{pid}{suffix}").status_code
out["cloche"] = c.get("/api/notifications").status_code
print(json.dumps(out))
"""


def _old_code(tmp: Path, production: str) -> Path:
    try:
        archive = subprocess.run(
            ["git", "-C", str(REPO), "archive", production, "openplan"],
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"historique git sans {production} (clone superficiel)")
    root = tmp / "production"
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(root, filter="data")
    return root


def _write_with_the_new_code(data: Path) -> list[str]:
    """Un projet par nouveauté du format : échelle et confiance d'une photo avec mesure
    (KAN-66), post-it coloré, événement d'un type nouveau s'il existe (réactions, KAN-78)."""
    app = create_app(settings=Settings(data_dir=data))
    store = app.state.store
    client = TestClient(app)
    ids = []
    pid = client.post("/api/projects", json={"name": "Grenier mesuré"}).json()["id"]
    project = store.load(__import__("uuid").UUID(pid))
    ref = ImageRef(
        path="images/x.jpg", sha256="0" * 64, width_px=10, height_px=10, exif_stripped=True
    )
    seg = ReferenceSegment(p1=(1, 1), p2=(5, 1), length_m=2.04, author="local")
    ref = ref.model_copy(update={"reference_segments": [seg]})
    project = project.model_copy(update={"images": {ref.id: ref}})
    level = next(iter(project.levels))
    camera = camera_basis(ref.id, 0.5, 1.45, entered=True)
    measured = camera.model_copy(
        update={"source": "reference", "segment_id": seg.id, "previous": camera}
    )
    store.save(with_basis(project, level, measured))
    ids.append(pid)
    pid2 = client.post("/api/projects", json={"name": "Espace de travail"}).json()["id"]
    client.get(f"/api/projects/{pid2}/board")
    note = {"id": str(uuid4()), "kind": "note", "x": 0, "y": 0, "w": 200, "h": 200, "text": "a"}
    assert (
        client.patch(
            f"/api/projects/{pid2}/board/items", json={"set": [{**note, "color": "pink"}]}
        ).status_code
        == 200
    )
    # KAN-91 : liens automatiques (posés à la lecture), un lien coupé, une étiquette ignorée.
    board = client.get(f"/api/projects/{pid2}/board").json()
    assert board["links"], "liens posés par la synchronisation"
    tagged = {
        "id": str(uuid4()),
        "kind": "note",
        "x": 900,
        "y": 0,
        "w": 200,
        "h": 200,
        "ignored_tags": ["m:terracotta"],
    }
    assert (
        client.patch(
            f"/api/projects/{pid2}/board/items",
            json={"set": [tagged], "links_delete": [board["links"][0]["id"]]},
        ).status_code
        == 200
    )
    ids.append(pid2)
    if "reaction" in event_schema.EventType.__args__:  # type: ignore[attr-defined]
        ProjectEvents(store.project_dir(__import__("uuid").UUID(pid2))).append(
            ProjectEvent.model_validate(
                {"type": "reaction", "project_id": pid2, "actor": "local", "label": "👍"}
            )
        )
    (data / "ids.json").write_text(json.dumps(ids))
    return ids


@pytest.mark.parametrize(("production", "target"), PRODUCTIONS)
def test_production_rereads_downgraded_projects_without_409_or_500(
    tmp_path: Path, production: str, target: str
) -> None:
    old = _old_code(tmp_path, production)
    data = tmp_path / "data"
    _write_with_the_new_code(data)
    report = downgrade_tree(data, target)
    assert not report.errors and report.changed
    seen = subprocess.run(
        [sys.executable, "-c", OLD_READER, str(data)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(old), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        cwd=tmp_path,
        timeout=180,
        check=False,
    )
    assert seen.returncode == 0, seen.stderr[-2000:]
    statuses = json.loads(seen.stdout.strip().splitlines()[-1])
    assert all(code == 200 for code in statuses.values()), statuses
