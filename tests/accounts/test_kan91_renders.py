"""KAN-91 (lot L6) : galerie des rendus réalistes, « Rendre depuis la photo » (D5), variantes,
couverture du projet, réactions, durée typique, cloche ; droits et liste blanche (KAN-89)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from openplan.api.accounts.render_queue import RenderQueue, variant_seed
from openplan.api.guards import PROJECT_ROUTES, Role
from openplan.cli.render_worker import engine
from openplan.cli.render_worker.engine import png_bytes
from openplan.cli.render_worker.sandbox import BAKE, CAMERA, OUT, PROJECT, SEED, read_seed
from openplan.schemas.bake import RenderCamera
from openplan.schemas.elements import Camera
from openplan.schemas.render_queue import WorkerResult
from openplan.schemas.scene import ImageRef
from tests.accounts.helpers import ORIGIN
from tests.accounts.test_kan89_realistic import WORKER, World, png

P = "/api/projects/{project_id}"
L = P + "/levels/{level_id}"
NEW_ROUTES = {
    ("POST", L + "/realistic/from-photo"): Role.EDITOR,
    ("POST", P + "/realistic/{request_id}/variant"): Role.EDITOR,
    ("PUT", P + "/realistic/{request_id}/cover"): Role.EDITOR,
    ("DELETE", P + "/realistic/cover"): Role.EDITOR,
    ("PUT", P + "/realistic/{request_id}/reactions/{key}"): Role.COMMENTER,
    ("DELETE", P + "/realistic/{request_id}/reactions/{key}"): Role.COMMENTER,
}
PHOTO_SIZE = (1600, 1200)


@pytest.fixture
def world(tmp_path: Path, migrated_url: str) -> World:
    return World(tmp_path, migrated_url)


def base(world: World) -> str:
    return f"/api/projects/{world.project_id}"


def listing(world: World, client: TestClient | None = None) -> dict[str, Any]:
    done = (client or world.client).get(f"{base(world)}/realistic")
    assert done.status_code == 200, done.text
    body: dict[str, Any] = done.json()
    return body


def render_one(world: World, width: int = 64, height: int = 48) -> dict[str, object]:
    """Le poste simulé prend la plus ancienne demande et la dépose ; renvoie la prise."""
    claim = world.worker.post("/api/worker/claim", json={"state": "idle"}, headers=WORKER)
    assert claim.status_code == 200, claim.text
    job: dict[str, object] = claim.json()
    meta = WorkerResult(seconds=90.0, vram_peak_mib=0, width=width, height=height, engine="fake")
    done = world.worker.post(
        f"/api/worker/jobs/{job['request_id']}/result",
        headers={**WORKER, "X-Render-Lease": str(job["lease"])},
        data={"meta": meta.model_dump_json()},
        files={"image": ("r.png", png(width, height), "image/png")},
    )
    assert done.status_code == 200, done.text
    return job


def add_photo_view(world: World) -> tuple[UUID, UUID]:
    """Une photo importée et sa caméra estimée (comme KAN-66 et KAN-79 la gardent)."""
    store = world.client.app.state.store  # type: ignore[attr-defined]
    project = store.load(world.project_id)
    level = project.levels[world.level_id]
    free = level.elements[world.camera_id]
    assert isinstance(free, Camera)
    image = ImageRef(
        path="images/photo.jpg",
        sha256="0" * 64,
        width_px=PHOTO_SIZE[0],
        height_px=PHOTO_SIZE[1],
        exif_stripped=True,
    )
    estimated = free.model_copy(
        update={"id": uuid4(), "image_id": image.id, "yaw": 1.2, "focal_px": 1300.0}
    )
    level = level.model_copy(update={"elements": {**level.elements, estimated.id: estimated}})
    project = project.model_copy(
        update={
            "images": {**project.images, image.id: image},
            "levels": {**project.levels, world.level_id: level},
        }
    )
    store.save(project)
    return image.id, estimated.id


def test_new_routes_are_in_the_guard_table_and_closed_without_session(world: World) -> None:
    assert {k: PROJECT_ROUTES[k] for k in NEW_ROUTES} == NEW_ROUTES
    anonymous = TestClient(world.client.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
    rid = uuid4()
    for method, url in (
        ("POST", f"{base(world)}/levels/{world.level_id}/realistic/from-photo"),
        ("POST", f"{base(world)}/realistic/{rid}/variant"),
        ("PUT", f"{base(world)}/realistic/{rid}/cover"),
        ("DELETE", f"{base(world)}/realistic/cover"),
        ("PUT", f"{base(world)}/realistic/{rid}/reactions/up"),
        ("DELETE", f"{base(world)}/realistic/{rid}/reactions/up"),
    ):
        assert anonymous.request(method, url).status_code == 401, url
        # Le jeton du poste n'ouvre pas les routes des personnes.
        assert anonymous.request(method, url, headers=WORKER).status_code == 401, url
    world.close()


def test_gallery_listing_typical_time_and_progress(world: World) -> None:
    first = world.ask()
    body = listing(world)
    assert body["typical_seconds"] == 120.0  # journal vide : ~2 min
    assert body["cover_id"] is None and body["reactions"] == []
    row = body["requests"][0]
    assert row["taken_at"] is None and row["photo_id"] is None
    claim = world.worker.post("/api/worker/claim", headers=WORKER).json()
    assert claim["request_id"] == first["id"]
    assert "seed" not in claim  # premier rendu : message de KAN-89 inchangé
    row = listing(world)["requests"][0]
    assert row["state"] == "taken" and row["taken_at"] is not None
    world.close()


def test_typical_seconds_is_the_median_of_recent_done_renders(tmp_path: Path) -> None:
    queue = RenderQueue.__new__(RenderQueue)
    queue.journal = tmp_path / "renders.jsonl"
    lines = [{"result": "done", "seconds": s} for s in (100.0, 130.0)]
    queue.journal.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert queue.typical_seconds() == 120.0  # moins de 3 rendus : la durée par défaut
    lines += [
        {"result": "failed", "seconds": None},
        {"result": "done", "seconds": 70.0},
        {"result": "done", "seconds": 112.0},
    ]
    queue.journal.write_text(
        "pas du json\n" + "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    assert queue.typical_seconds() == 106.0  # médiane de 70, 100, 112, 130


def test_render_from_photo_uses_the_estimated_camera(world: World) -> None:
    image_id, camera_id = add_photo_view(world)
    url = f"{base(world)}/levels/{world.level_id}/realistic/from-photo"
    asked = world.client.post(url, json={"image_id": str(image_id)})
    assert asked.status_code == 201, asked.text
    assert asked.json()["camera_id"] == str(camera_id)
    assert asked.json()["photo_id"] == str(image_id)
    # Double clic : la même demande.
    again = world.client.post(url, json={"image_id": str(image_id)})
    assert again.status_code == 200 and again.json()["id"] == asked.json()["id"]
    # Le poste reçoit la caméra estimée de la photo, au format de la photo.
    job = world.worker.post("/api/worker/claim", headers=WORKER).json()
    camera = RenderCamera.model_validate_json(
        world.worker.get(
            f"/api/worker/jobs/{job['request_id']}/files/camera",
            headers={**WORKER, "X-Render-Lease": job["lease"]},
        ).content
    )
    assert camera.yaw == pytest.approx(1.2)
    assert camera.width_px / camera.height_px == pytest.approx(PHOTO_SIZE[0] / PHOTO_SIZE[1])
    # La liste dit de quelle photo vient le rendu ; une vue libre n'en a pas.
    world.ask()
    rows = {r["camera_id"]: r for r in listing(world)["requests"]}
    assert rows[str(camera_id)]["photo_id"] == str(image_id)
    assert rows[str(world.camera_id)]["photo_id"] is None
    # Photo inconnue : 404 ; photo sans caméra estimée : 409 en français.
    assert world.client.post(url, json={"image_id": str(uuid4())}).status_code == 404
    store = world.client.app.state.store  # type: ignore[attr-defined]
    project = store.load(world.project_id)
    level = project.levels[world.level_id]
    elements = {k: v for k, v in level.elements.items() if k != camera_id}
    store.save(
        project.model_copy(
            update={"levels": {world.level_id: level.model_copy(update={"elements": elements})}}
        )
    )
    lost = world.client.post(url, json={"image_id": str(image_id)})
    assert lost.status_code == 409
    assert "point de vue estimé" in lost.json()["detail"]
    world.close()


def test_no_render_without_a_plan(world: World) -> None:
    """D2 : une carte Rendus sans plan relié (lien plan → rendus coupé, lot L1) ne se rend pas,
    409 « Reliez un plan pour rendre »."""
    board = world.client.get(f"{base(world)}/board").json()
    view = next(k for k in board["links"] if k["kind"] == "renders_view")
    cut = world.client.patch(f"{base(world)}/board/items", json={"links_delete": [view["id"]]})
    assert cut.status_code == 200, cut.text
    refused = world.client.post(
        f"{base(world)}/levels/{world.level_id}/realistic",
        json={"camera_id": str(world.camera_id)},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "Reliez un plan pour rendre"
    world.close()


def test_variant_is_the_same_view_with_another_seed(world: World) -> None:
    world.ask()
    first = render_one(world)
    rid = str(first["request_id"])
    variant = world.client.post(f"{base(world)}/realistic/{rid}/variant")
    assert variant.status_code == 201, variant.text
    assert variant.json()["camera_id"] == str(world.camera_id)
    assert variant.json()["id"] != rid
    # Double clic : la même variante en attente.
    again = world.client.post(f"{base(world)}/realistic/{rid}/variant")
    assert again.status_code == 200 and again.json()["id"] == variant.json()["id"]
    job = world.worker.post("/api/worker/claim", headers=WORKER).json()
    assert job["request_id"] == variant.json()["id"]
    assert job["seed"] == variant_seed(UUID(variant.json()["id"]))
    # Une demande inconnue, ou pas encore faite, n'a pas de variante.
    assert world.client.post(f"{base(world)}/realistic/{uuid4()}/variant").status_code == 404
    taken = variant.json()["id"]
    assert world.client.post(f"{base(world)}/realistic/{taken}/variant").status_code == 404
    world.close()


def test_cover_of_the_project(world: World) -> None:
    world.ask()
    rid = str(render_one(world, 640, 480)["request_id"])
    cover_url = f"{base(world)}/cover"
    assert world.client.get(cover_url).headers["X-Cover-Kind"] == "plan"
    before = next(
        p["updated_at"]
        for p in world.client.get("/api/projects").json()
        if p["id"] == str(world.project_id)
    )
    put = world.client.put(f"{base(world)}/realistic/{rid}/cover")
    assert put.status_code == 200, put.text
    assert put.json() == {"cover_id": rid}
    assert listing(world)["cover_id"] == rid
    shown = world.client.get(cover_url)
    assert shown.status_code == 200 and shown.headers["X-Cover-Kind"] == "render"
    assert shown.headers["content-type"] == "image/webp"
    # La liste « Mes projets » relit la vignette : le projet est marqué modifié.
    after = next(
        p["updated_at"]
        for p in world.client.get("/api/projects").json()
        if p["id"] == str(world.project_id)
    )
    assert after > before
    # Un membre lecteur (hors liste blanche) voit la couverture mais ne peut pas la changer.
    marie = world.person("marie@example.org")
    user = world.service.user_by_email("marie@example.org")
    assert user is not None
    world.service.set_member(world.project_id, user.id, "viewer", added_by=world.augus_id)
    assert marie.get(cover_url).headers["X-Cover-Kind"] == "render"
    assert marie.put(f"{base(world)}/realistic/{rid}/cover").status_code == 403
    assert marie.delete(f"{base(world)}/realistic/cover").status_code == 403
    # Un éditeur hors liste blanche non plus (KAN-89 : bientôt en ligne).
    world.service.set_member(world.project_id, user.id, "editor", added_by=world.augus_id)
    refused = marie.put(f"{base(world)}/realistic/{rid}/cover")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Rendus réalistes : bientôt en ligne"
    # Rendu inconnu : 404 ; retirer la couverture rend le choix automatique.
    assert world.client.put(f"{base(world)}/realistic/{uuid4()}/cover").status_code == 404
    cleared = world.client.delete(f"{base(world)}/realistic/cover")
    assert cleared.status_code == 200 and cleared.json() == {"cover_id": None}
    assert world.client.get(cover_url).headers["X-Cover-Kind"] == "plan"
    assert listing(world)["cover_id"] is None
    world.close()


def test_reactions_on_a_render(world: World) -> None:
    world.ask()
    rid = str(render_one(world)["request_id"])
    put = world.client.put(f"{base(world)}/realistic/{rid}/reactions/heart")
    assert put.status_code == 200, put.text
    pills = put.json()
    assert [(p["key"], p["count"], p["mine"], p["emoji"]) for p in pills] == [
        ("heart", 1, True, "❤️")
    ]
    assert world.client.put(f"{base(world)}/realistic/{rid}/reactions/heart").json() == pills
    assert listing(world)["reactions"] == pills
    # Même journal que KAN-78 : la cloche prévient le lanceur (ici, lui-même : rien).
    store = world.client.app.state.store  # type: ignore[attr-defined]
    events = (store.project_dir(world.project_id) / "events.jsonl").read_text(encoding="utf-8")
    added = [json.loads(x) for x in events.splitlines() if '"reaction.added"' in x]
    assert added[-1]["item_id"] == rid and added[-1]["item_kind"] == "render"
    # Une réaction inconnue est refusée ; un rendu inconnu : 404.
    assert world.client.put(f"{base(world)}/realistic/{rid}/reactions/rm").status_code == 422
    assert world.client.put(f"{base(world)}/realistic/{uuid4()}/reactions/up").status_code == 404
    # Les pastilles de l'espace de travail ne listent pas les rendus réalistes.
    assert world.client.get(f"{base(world)}/reactions").json() == []
    removed = world.client.delete(f"{base(world)}/realistic/{rid}/reactions/heart")
    assert removed.status_code == 200 and removed.json() == []
    # Un autre compte, même commentateur du projet : 403 (liste blanche).
    marie = world.person("marie@example.org")
    user = world.service.user_by_email("marie@example.org")
    assert user is not None
    world.service.set_member(world.project_id, user.id, "commenter", added_by=world.augus_id)
    assert marie.put(f"{base(world)}/realistic/{rid}/reactions/up").status_code == 403
    world.close()


def test_a_done_render_rings_the_bell(world: World) -> None:
    world.ask()
    render_one(world)
    store = world.client.app.state.store  # type: ignore[attr-defined]
    events = (store.project_dir(world.project_id) / "events.jsonl").read_text(encoding="utf-8")
    done = [json.loads(x) for x in events.splitlines() if '"render.done"' in x]
    assert done and done[-1]["actor"] == str(world.augus_id)
    bell = world.client.get("/api/notifications").json()
    assert any(i["type"] == "render.done" and i["mine"] for i in bell["items"])
    world.close()


def test_seed_reaches_the_engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le poste écrit le tirage d'une variante ; le moteur Z-Image le reçoit, et seulement
    lui (un premier rendu garde le tirage par défaut de KAN-88)."""
    assert read_seed(tmp_path) is None
    (tmp_path / SEED).write_text("123", encoding="utf-8")
    assert read_seed(tmp_path) == 123
    (tmp_path / SEED).write_text("-4", encoding="utf-8")
    assert read_seed(tmp_path) is None
    fake = types.ModuleType("kan91_fake_zimage")
    calls: list[dict[str, object]] = []

    def render_scene(bake_path: Path, camera_path: Path, out_png: Path, **kwargs: object) -> object:
        calls.append(kwargs)
        out_png.write_bytes(png_bytes(32, 16, (1, 2, 3)))
        return types.SimpleNamespace(seconds=1.0, vram_peak_mib=0, width=32, height=16)

    fake.render_scene = render_scene  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "kan91_fake_zimage", fake)
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "kan91_fake_zimage")
    args = (tmp_path / PROJECT, tmp_path / BAKE, tmp_path / CAMERA, tmp_path / OUT)
    engine.render("zimage", *args)
    engine.render("zimage", *args, seed=77)
    assert "seed" not in calls[0] and calls[1]["seed"] == 77
