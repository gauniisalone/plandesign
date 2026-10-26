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
from typing import Any
from uuid import UUID, uuid4

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

PRODUCTIONS = [
    ("3da4d52", "0.16.0"),
    ("a1c630f", "0.17.0"),
    ("14e8870", "0.18.0"),
    ("f6137ff", "0.19.0"),
    ("af6bf71", "0.19.0"),
]
"""Les versions vers lesquelles on revient : MEP 5 (21 oct. 2026, schéma 0.16.0), MEP 13
(22 oct. 2026, schéma 0.17.0, la production d'avant les cartes reliées de KAN-91), MEP 15
(25 oct. 2026, schéma 0.18.0, la production d'avant les zones de sol de KAN-92), MEP 16
(f6137ff) et le passage 16 (af6bf71), schéma 0.19.0, d'avant les poteaux et fermes de KAN-94."""
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
cards = {}
for pid in json.loads((d / "ids.json").read_text()):
    items = c.get(f"/api/projects/{pid}/board").json()["items"]
    cards[pid] = sum(1 for i in items if i["kind"] == "frame" and i.get("role") == "photos")
print(json.dumps({"statuts": out, "cartes_photos": cards}))
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
    # KAN-91 : un deuxième niveau, donc deux cartes Photos (celle de l'étage porte son id).
    storey = {"name": "Étage", "kind": "storey"}
    assert client.post(f"/api/projects/{pid2}/levels", json=storey).status_code in (200, 201)
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
    # Un fichier joint posé par l'espace de travail (`placed`, 25 oct.) : l'index des fichiers
    # est relu par l'ancienne application avec un modèle strict.
    sent = client.post(
        f"/api/projects/{pid2}/media",
        params={"placed": "true"},
        files={"file": ("note.txt", b"bonjour", "text/plain")},
    )
    assert sent.status_code == 201, sent.text
    ids.append(pid2)
    if "reaction" in event_schema.EventType.__args__:  # type: ignore[attr-defined]
        ProjectEvents(store.project_dir(__import__("uuid").UUID(pid2))).append(
            ProjectEvent.model_validate(
                {"type": "reaction", "project_id": pid2, "actor": "local", "label": "👍"}
            )
        )
    ids.append(str(_with_floor_zone(store)))
    ids.append(str(_with_posts_and_truss(store)))
    (data / "ids.json").write_text(json.dumps(ids))
    return ids


def _with_posts_and_truss(store: Any) -> UUID:
    """KAN-94 : des combles avec une ferme intermédiaire (déplacée, donc un historique qui la
    connaît) et un poteau à hauteur donnée."""
    from openplan.scene.ops import AddPost, AddTruss, UpdateElement, apply_op
    from openplan.schemas.common import Point2
    from openplan.schemas.elements import Truss
    from tests.factories import attic_project

    project = attic_project(user_defined=True, camera=False)
    level = next(iter(project.levels.values()))
    project = apply_op(project, level.id, AddTruss(distance=1.5))
    truss = next(e for e in project.levels[level.id].elements.values() if isinstance(e, Truss))
    project = apply_op(
        project, level.id, UpdateElement(element_id=truss.id, fields={"spacing": 1.2})
    )
    near = Point2(x=truss.position.x + 0.4, y=truss.position.y + 0.4)
    project = apply_op(project, level.id, AddPost(position=near, top=2.1))
    store.save(project)
    return project.id


def _with_floor_zone(store: Any) -> UUID:
    """KAN-92 : une pièce avec une zone de sol élevée de 20 cm (par l'API, comme Pousser/Tirer)
    et un mur épaissi à 30 cm."""
    from openplan.scene.ops import AddFloorZone, UpdateElement, apply_op
    from openplan.schemas.common import Point2
    from openplan.schemas.elements import FloorZone, Wall
    from tests.factories import rect_room_project

    project = rect_room_project(name="Estrade")
    level = next(iter(project.levels.values()))
    wall = next(e for e in level.elements.values() if isinstance(e, Wall))
    corners = [(1.0, 1.0), (2.0, 1.0), (2.0, 2.0), (1.0, 2.0)]
    project = apply_op(
        project, level.id, AddFloorZone(polygon=[Point2(x=x, y=y) for x, y in corners])
    )
    zone = next(e for e in project.levels[level.id].elements.values() if isinstance(e, FloorZone))
    for eid, fields in ((zone.id, {"elevation": 0.2}), (wall.id, {"thickness": 0.3})):
        project = apply_op(project, level.id, UpdateElement(element_id=eid, fields=fields))
    store.save(project)
    return project.id


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
    seen_out = json.loads(seen.stdout.strip().splitlines()[-1])
    statuses = seen_out["statuts"]
    assert all(code == 200 for code in statuses.values()), statuses
    # Pour l'ancien code, les deux cartes Photos du projet à deux niveaux restent des cartes
    # Photos (rôle « photos »), relues sans erreur.
    assert sorted(seen_out["cartes_photos"].values()) == [1, 1, 1, 2], seen_out
