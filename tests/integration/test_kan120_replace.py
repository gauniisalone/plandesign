"""KAN-120 : « Régénérer le plan » (`replace` sur la route autoplan existante). Le plan est
refait puis remplacé en UNE étape d'annulation ; les meubles hors des nouveaux murs sont
retirés et comptés ; si le calcul échoue, le plan reste intact."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.floorplan import DetectionError
from openplan.scene import history
from openplan.scene.store import ProjectStore
from openplan.schemas.elements import Camera, Furniture

FIXTURE_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"

pytestmark = pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")


def _wait(c: TestClient, pid: str, job: dict[str, Any]) -> dict[str, Any]:
    for _ in range(600):
        job = c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    return job


def _client(tmp_path: Path) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(tmp_path / "data")
    settings = Settings(data_dir=tmp_path / "data", device="cpu", models_dir=tmp_path / "vide")
    return TestClient(create_app(store=store, settings=settings)), store


def _built(c: TestClient) -> tuple[str, str, str]:
    project = c.post("/api/projects", json={"name": "régénérer"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    image = c.post(
        f"/api/projects/{pid}/images",
        files={"file": ("p.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
    ).json()["image"]
    job = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image['id']}/autoplan").json()
    assert _wait(c, pid, job)["status"] == "done"
    return pid, lid, image["id"]


def _furnish(c: TestClient, store: ProjectStore, pid: str, lid: str) -> tuple[UUID, UUID]:
    """Un lit au milieu de la pièce (gardé), sa copie à 50 m (retirée)."""
    derived = c.get(f"/api/projects/{pid}/levels/{lid}/derived").json()["bounds"]
    x = (derived["min"]["x"] + derived["max"]["x"]) / 2
    y = (derived["min"]["y"] + derived["max"]["y"]) / 2
    r = c.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "add_furniture", "asset_id": "bed_double_160", "position": {"x": x, "y": y}},
    )
    assert r.status_code == 200, r.text
    before = store.load(UUID(pid))
    level = before.levels[UUID(lid)]
    bed = next(e for e in level.elements.values() if isinstance(e, Furniture))
    far = bed.model_copy(
        update={
            "id": uuid4(),
            "transform": bed.transform.model_copy(
                update={"position": bed.transform.position.model_copy(update={"x": x + 50})}
            ),
        }
    )
    elements = {**level.elements, far.id: far}
    after = before.model_copy(
        update={
            "levels": {**before.levels, level.id: level.model_copy(update={"elements": elements})}
        }
    )
    store.save(history.commit(before, after, actor=history.USER_ACTOR, label="Lit éloigné"))
    return bed.id, far.id


def test_replace_is_one_undo_step_and_keeps_furniture_inside(tmp_path: Path) -> None:
    client, store = _client(tmp_path)
    with client as c:
        pid, lid, image_id = _built(c)
        bed, far = _furnish(c, store, pid, lid)
        before = store.load(UUID(pid))
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image_id}/autoplan?replace=true")
        assert r.status_code == 202, r.text
        job = _wait(c, pid, r.json())
        assert job["status"] == "done", job
        assert job["stage"] == "Plan prêt" and job["result"]["report"]["valid"] is True
        notices = job["result"]["run"]["notices"]
        assert "1 meuble hors des nouveaux murs retiré." in notices
        after = store.load(UUID(pid))
        assert len(after.history) == before.history_cursor + 1
        assert after.history_cursor == before.history_cursor + 1
        entry = after.history[-1]
        assert entry.label == "Plan régénéré à partir de 1 photo" and entry.actor == "user"
        level = after.levels[UUID(lid)]
        assert bed in level.elements and far not in level.elements
        assert sum(e.type == "wall" for e in level.elements.values()) >= 4
        # Ctrl+Z : l'ancien plan revient en entier, lit éloigné compris.
        undone = c.post(f"/api/projects/{pid}/undo")
        assert undone.status_code == 200, undone.text
        assert history.content(store.load(UUID(pid))) == history.content(before)


def _photo_cameras(store: ProjectStore, pid: str, lid: str) -> set[UUID]:
    level = store.load(UUID(pid)).levels[UUID(lid)]
    return {e.image_id for e in level.elements.values() if isinstance(e, Camera) and e.image_id}


def test_every_photo_of_the_node_is_posed_on_the_plan(tmp_path: Path) -> None:
    """P34 : la réunion de pièces KAN-95 étant désactivée, le plan vient d'une photo ; les
    autres photos du node sont confrontées au plan et y reçoivent leur caméra, à la génération
    comme à la régénération (sinon elles restaient « pas encore utilisées »)."""
    client, store = _client(tmp_path)
    with client as c:
        project = c.post("/api/projects", json={"name": "trois photos"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        ids = [
            c.post(
                f"/api/projects/{pid}/images",
                files={"file": (f"p{i}.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
            ).json()["image"]["id"]
            for i in range(3)
        ]
        route = f"/api/projects/{pid}/levels/{lid}/images/{ids[0]}/autoplan"
        job = _wait(c, pid, c.post(route).json())
        assert job["status"] == "done", job
        assert _photo_cameras(store, pid, lid) == {UUID(i) for i in ids}
        before = store.load(UUID(pid))
        job = _wait(c, pid, c.post(f"{route}?replace=true").json())
        assert job["status"] == "done", job
        after = store.load(UUID(pid))
        assert _photo_cameras(store, pid, lid) == {UUID(i) for i in ids}
        assert after.history_cursor == before.history_cursor + 1  # toujours une seule étape
        assert after.history[-1].label == "Plan régénéré à partir de 3 photos"


def _drawn_plan_png() -> bytes:
    """Un plan dessiné : fond blanc, traits noirs épais (ce que classify_image appelle plan)."""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((100, 100, 700, 500), outline="black", width=12)
    draw.line((400, 100, 400, 500), fill="black", width=12)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_upload_answer_says_photo_or_plan(tmp_path: Path) -> None:
    """KAN-120 : contrat de POST /projects/{id}/images, figé ici. `kind` dit la lecture
    devinée : une image de plan construit le plan dès l'import, une photo attend le bouton."""
    client, _ = _client(tmp_path)
    with client as c:
        pid = c.post("/api/projects", json={"name": "contrat"}).json()["id"]
        photo = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("p.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
        )
        assert photo.status_code == 201, photo.text
        assert set(photo.json()) == {"image", "warnings", "project", "kind"}
        assert photo.json()["kind"] == "photo"
        plan = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("plan.png", _drawn_plan_png(), "image/png")},
        )
        assert plan.status_code == 201, plan.text
        assert plan.json()["kind"] == "plan"


def test_replace_that_fails_leaves_the_plan_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = _client(tmp_path)
    with client as c:
        pid, lid, image_id = _built(c)
        before = store.load(UUID(pid))

        def broken(*_a: object, **_k: object) -> None:
            msg = "coupure simulée"
            raise DetectionError(msg)

        monkeypatch.setattr("openplan.api.routes.autoplan.run_autoplan", broken)
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image_id}/autoplan?replace=true")
        assert r.status_code == 202, r.text
        assert _wait(c, pid, r.json())["status"] == "failed"
        after = store.load(UUID(pid))
        assert history.content(after) == history.content(before)
        assert after.history_cursor == before.history_cursor
