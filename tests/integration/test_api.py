"""Scénario complet via l'API : projet → murs → pièce → mesure → calibration → undo → exports."""

from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.scene.store import ProjectStore


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = ProjectStore(tmp_path / "data")
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as c:
        yield c


def _walls(project: dict[str, Any]) -> dict[str, dict[str, Any]]:
    level = next(iter(project["levels"].values()))
    return {k: v for k, v in level["elements"].items() if v["type"] == "wall"}


def _draw_rectangle(client: TestClient, pid: str, lid: str) -> dict[str, Any]:
    corners = [(0, 0), (4, 0), (4, 3), (0, 3)]
    project: dict[str, Any] = {}
    for i in range(4):
        x0, y0 = corners[i]
        x1, y1 = corners[(i + 1) % 4]
        r = client.post(
            f"/api/projects/{pid}/levels/{lid}/ops",
            json={"op": "add_wall", "start": {"x": x0, "y": y0}, "end": {"x": x1, "y": y1}},
        )
        assert r.status_code == 200, r.text
        project = r.json()["project"]
    return project


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_full_slice_scenario(client: TestClient) -> None:
    r = client.post("/api/projects", json={"name": "Chambre"})
    assert r.status_code == 201
    project = r.json()
    pid = project["id"]
    lid = next(iter(project["levels"]))
    assert client.get("/api/projects").json()[0]["name"] == "Chambre"

    project = _draw_rectangle(client, pid, lid)
    assert len(_walls(project)) == 4
    level = project["levels"][lid]
    assert len(level["vertices"]) == 4

    r = client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    body = r.json()
    assert r.status_code == 200 and body["report"]["valid"]
    assert len(body["derived"]["rooms"]) == 1
    room_id, room = next(iter(body["derived"]["rooms"].items()))
    assert room["area_m2"] == pytest.approx(3.85 * 2.85)

    # Mesure sur le mur du bas (le premier créé), puis calibration à 3,10 m.
    walls = _walls(body["project"])
    bottom = next(iter(walls.values()))  # ordre d'insertion : le premier mur tracé
    r = client.post(
        f"/api/projects/{pid}/measurements",
        json={"kind": "wall_length", "value": 3.1, "element_id": bottom["id"]},
    )
    assert r.status_code == 201, r.text
    r = client.post(f"/api/projects/{pid}/levels/{lid}/calibrate", params={"dry_run": "true"})
    assert r.status_code == 200 and r.json()["applied"] is False
    assert r.json()["calibration"]["scale_factor"] == pytest.approx(0.775)
    r = client.post(f"/api/projects/{pid}/levels/{lid}/calibrate")
    assert r.status_code == 200 and r.json()["applied"] is True
    calibrated = r.json()["project"]
    assert _walls(calibrated)[bottom["id"]]["source"] == "measured"
    derived = client.get(f"/api/projects/{pid}/levels/{lid}/derived").json()
    assert derived["walls"][bottom["id"]]["length_m"] == pytest.approx(3.1)
    assert derived["rooms"][room_id]["area_m2"] == pytest.approx((3.1 - 0.15) * (3 * 0.775 - 0.15))

    # Undo de la calibration, puis redo.
    r = client.post(f"/api/projects/{pid}/undo")
    assert r.status_code == 200
    assert _walls(r.json())[bottom["id"]]["source"] == "user_defined"
    r = client.post(f"/api/projects/{pid}/redo")
    assert _walls(r.json())[bottom["id"]]["source"] == "measured"

    # Validation et exports.
    assert client.get(f"/api/projects/{pid}/validation").json()["valid"]
    _check_exports(client, pid, lid)

    # Suppression complète.
    assert client.delete(f"/api/projects/{pid}").status_code == 204
    assert client.get(f"/api/projects/{pid}").status_code == 404
    assert client.get("/api/projects").json() == []


def _check_exports(client: TestClient, pid: str, lid: str) -> None:
    """SVG, PDF, DXF et JSON du plan calibré (mur de 3,10 m)."""
    svg = client.get(f"/api/projects/{pid}/exports/svg", params={"level_id": lid})
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg+xml")
    assert "3.10 m" in svg.text
    pdf = client.get(f"/api/projects/{pid}/exports/pdf", params={"level_id": lid})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-") and b"(3.10 m)" in pdf.content
    assert 'filename="Chambre - ' in pdf.headers["content-disposition"]
    cut = client.get(
        f"/api/projects/{pid}/exports/section-pdf",
        params={"level_id": lid, "axis": "y", "value": 1.0},
    )
    assert cut.status_code == 200 and cut.content.startswith(b"%PDF-")
    assert b"hors tout" in cut.content and "coupe y" in cut.headers["content-disposition"]
    bad = {"level_id": lid, "axis": "z", "value": 1.0}
    assert client.get(f"/api/projects/{pid}/exports/section-pdf", params=bad).status_code == 422
    dxf = client.get(f"/api/projects/{pid}/exports/dxf", params={"level_id": lid})
    assert dxf.status_code == 200 and dxf.headers["content-type"].startswith("application/dxf")
    assert "MURS" in dxf.text and dxf.headers["content-disposition"].endswith('.dxf"')
    js = client.get(f"/api/projects/{pid}/exports/json")
    assert js.status_code == 200 and js.json()["id"] == pid and js.json()["history"]
    assert js.headers["content-disposition"].endswith('.openplan.json"')
    shared = client.get(f"/api/projects/{pid}/exports/json", params={"share": "true"})
    assert shared.status_code == 200 and shared.json()["id"] == pid
    assert shared.json()["history"] == [] and shared.json()["history_cursor"] == 0
    assert shared.json()["levels"] == js.json()["levels"]
    assert shared.headers["content-disposition"].endswith('.partage.openplan.json"')


def test_catalog_and_add_furniture(client: TestClient) -> None:
    """Catalogue (3.1) servi en lecture ; un meuble posé par commande entre dans la scène et
    dans le bake ; un asset inconnu ou une dimension hors plage répondent 409."""
    catalog = client.get("/api/catalog").json()
    assert len(catalog["assets"]) >= 34
    assert all(a["license"] == "cc0" for a in catalog["assets"])
    low = next(a for a in catalog["assets"] if a["id"] == "low_storage_eave")
    assert low["anchor"] == "eave_wall" and low["height"]["max"] <= 0.9
    project = client.post("/api/projects", json={"name": "meubles"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "add_furniture", "asset_id": "bed_double_160", "position": {"x": 2, "y": 1.5}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    beds = [
        e for e in body["project"]["levels"][lid]["elements"].values() if e["type"] == "furniture"
    ]
    assert len(beds) == 1 and beds[0]["source"] == "user_defined"
    assert beds[0]["bbox"] == {"width": 1.6, "depth": 2.0, "height": 0.5}
    assert body["report"]["valid"]
    bake = client.get(f"/api/projects/{pid}/levels/{lid}/bake").json()
    assert len([p for p in bake["prisms"] if p["kind"] == "furniture"]) == 1
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "add_furniture", "asset_id": "licorne", "position": {"x": 0, "y": 0}},
    )
    assert r.status_code == 409 and "meuble inconnu" in r.json()["detail"]
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={
            "op": "add_furniture",
            "asset_id": "desk_140",
            "position": {"x": 0, "y": 0},
            "width": 9,
        },
    )
    assert r.status_code == 409 and "hors de la plage" in r.json()["detail"]


def test_glb_of_an_empty_level_is_an_empty_scene_not_a_500(client: TestClient) -> None:
    """Ouvrir la 3D d'un projet neuf : trimesh refusait la scène vide (KeyError 'world')."""
    import trimesh

    project = client.post("/api/projects", json={"name": "vide"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    for style in ("provenance", "realistic"):
        glb = client.get(
            f"/api/projects/{pid}/exports/glb", params={"level_id": lid, "style": style}
        )
        assert glb.status_code == 200 and glb.content[:4] == b"glTF"
        assert len(glb.content) % 4 == 0
        assert int.from_bytes(glb.content[8:12], "little") == len(glb.content)
        loaded = trimesh.load(trimesh.util.wrap_as_stream(glb.content), file_type="glb")
        assert len(loaded.geometry) == 0


def test_glb_export_and_blender_status(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "3d"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    glb = client.get(f"/api/projects/{pid}/exports/glb", params={"level_id": lid})
    assert glb.status_code == 200 and glb.headers["content-type"] == "model/gltf-binary"
    assert glb.content[:4] == b"glTF"
    bake = client.get(f"/api/projects/{pid}/levels/{lid}/bake").json()
    assert len([p for p in bake["prisms"] if p["kind"] == "wall"]) == 4
    assert len([p for p in bake["prisms"] if p["kind"] == "floor"]) == 1
    status = client.get("/api/blender").json()
    assert set(status) == {"available", "binary"}
    # Artefact inexistant ou nom dangereux → 409 propre, jamais un chemin arbitraire.
    rid = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/projects/{pid}/runs/{rid}/blender/nope.png").status_code == 409
    assert client.get(f"/api/projects/{pid}/runs/{rid}/blender/..%2Fx").status_code in (404, 409)


def test_adopt_view_route_makes_the_plan_follow_the_photo(tmp_path: Path) -> None:
    """Contradiction entre une photo et le plan : « garder la photo » déplace le mur le moins
    sûr et la vérification se tait ; une caméra sans observation répond 409."""
    from openplan.pipeline.multiview import add_view
    from openplan.schemas import Camera, Source
    from tests.factories import by_name, only_level
    from tests.unit.test_multiview import SECOND_VIEW, H, analysis, photo1_scene

    project, image_id = photo1_scene((Source.ESTIMATED,) * 4)
    level_id = only_level(project).id
    viewed, _ = add_view(
        project, level_id, analysis(SECOND_VIEW), image_id=image_id, camera_height_m=H * 1.15
    )
    store = ProjectStore(tmp_path / "data")
    store.save(viewed)
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        pid = str(viewed.id)
        before = client.get(f"/api/projects/{pid}/validation").json()
        assert [v["type"] for v in before["violations"]] == ["inconsistent_views"]
        assert "écart +70 cm" in before["violations"][0]["message"]
        camera = by_name(only_level(viewed), "Photo 2", Camera)
        r = client.post(f"/api/projects/{pid}/levels/{level_id}/views/{camera.id}/adopt")
        assert r.status_code == 200, r.text
        assert r.json()["report"]["violations"] == []
        assert client.get(f"/api/projects/{pid}").json()["history_cursor"] == 2
        first = by_name(only_level(viewed), "Photo", Camera)
        r = client.post(f"/api/projects/{pid}/levels/{level_id}/views/{first.id}/adopt")
        assert r.status_code == 409 and "deux murs opposés" in r.json()["detail"]
        # « Tourner la vue » : la caméra 2 tourne d'un quart de tour, sa visée aussi, aucun
        # mur ne bouge, et la cohérence est recontrôlée dans la réponse.
        before = client.get(f"/api/projects/{pid}").json()["levels"][str(level_id)]["elements"]
        r = client.post(f"/api/projects/{pid}/levels/{level_id}/views/{camera.id}/rotate")
        assert r.status_code == 200, r.text
        body = r.json()
        turned = body["project"]["levels"][str(level_id)]["elements"][str(camera.id)]
        assert turned["metadata"]["orientation_user_fixed"] is True
        assert abs(turned["yaw"] - before[str(camera.id)]["yaw"]) == pytest.approx(
            math.pi / 2, abs=1e-6
        ) or abs(turned["yaw"] - before[str(camera.id)]["yaw"]) == pytest.approx(
            3 * math.pi / 2, abs=1e-6
        )
        assert [v["type"] for v in body["report"]["violations"]] == ["inconsistent_views"]
        assert client.get(f"/api/projects/{pid}").json()["history_cursor"] == 3
        r = client.post(
            f"/api/projects/{pid}/levels/{level_id}/views/{camera.id}/rotate",
            json={"quarter_turns": 3},
        )
        assert r.status_code == 200 and r.json()["report"]["violations"] == []
        r = client.post(f"/api/projects/{pid}/levels/{level_id}/views/{first.id}/rotate")
        assert r.status_code == 409 and "à tourner" in r.json()["detail"]


FIXTURE_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")
def test_plan_from_depth_route(tmp_path: Path) -> None:
    """Plan depuis la profondeur (2.3b) : polygone appliqué au niveau vide, 409 ensuite."""
    from tests.unit.test_depthplan import BoxDepth

    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), depth_estimator=BoxDepth()
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "profondeur"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        image = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("i.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
        ).json()["image"]
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/detect-depth",
            json={"image_id": image["id"], "camera_height_m": 1.4},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["run"]["result"]["step"] == "floorplan.depth" and body["report"]["valid"]
        assert body["run"]["walls_seen"] >= 2 and body["run"]["camera_height_m"] > 1.0
        walls = [
            e
            for e in body["run"]["project"]["levels"][lid]["elements"].values()
            if e["type"] == "wall"
        ]
        assert len(walls) == 4 and any(w["source"] == "estimated" for w in walls)
        assert c.get(f"/api/projects/{pid}").json()["history_cursor"] == 1
        r = c.post(f"/api/projects/{pid}/levels/{lid}/detect-depth", json={"image_id": image["id"]})
        assert r.status_code == 409 and "déjà des murs" in r.json()["detail"]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/detect-depth", json={"image_id": pid})
        assert r.status_code == 404


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")
def test_openings_route_projects_boxes_on_the_host_wall(tmp_path: Path) -> None:
    """Portes et fenêtres (2.4b) : la boîte d'un détecteur simulé, projetée par la caméra
    de la photo, devient une fenêtre `observed` ; 409 sans caméra pour la photo ; artefact."""
    from openplan.schemas import Camera, Project
    from tests.factories import only_level
    from tests.unit.test_openings_step import FakeDetector, _bbox, _element

    detector = FakeDetector([])
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), opening_detector=detector
    )
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "baies"}).json()["id"]
        lid = next(iter(c.get(f"/api/projects/{pid}").json()["levels"]))
        png = FIXTURE_IMAGE.read_bytes()
        image = c.post(
            f"/api/projects/{pid}/images", files={"file": ("i.png", png, "image/png")}
        ).json()["image"]
        other = c.post(
            f"/api/projects/{pid}/images", files={"file": ("j.png", png, "image/png")}
        ).json()["image"]
        # Sans caméra pour cette photo : rien à projeter, 409 explicite.
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image['id']}/openings")
        assert r.status_code == 409 and "caméra de cette photo" in r.json()["detail"]
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/detect",
            json={"image_id": image["id"], "camera_height_m": 1.4},
        )
        assert r.status_code == 200, r.text
        # Une fenêtre de 1,2 m sur la face du mur de face, vue par la caméra heuristique.
        scene = Project.model_validate(r.json()["run"]["project"])
        level = only_level(scene)
        camera = next(e for e in level.elements.values() if isinstance(e, Camera))
        far = next(
            level.vertices[w.start_vertex].position.y
            for w in level.elements.values()
            if getattr(w, "name", None) == "Mur haut"
        )
        face = far - 0.075
        # Centrée sur le point du mur de face que la caméra regarde (elle est en biais).
        cx = camera.position.x + (face - camera.position.y) / math.tan(camera.yaw)
        corners = [
            (cx - 0.6, face, 0.8),
            (cx + 0.6, face, 0.8),
            (cx - 0.6, face, 2.0),
            (cx + 0.6, face, 2.0),
        ]
        box = _bbox(camera, (image["width_px"], image["height_px"]), corners)
        detector.boxes = [_element("window", box, 0.55), _element("door", (0, 0, 20, 30), 0.3)]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image['id']}/openings")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["report"]["valid"] and body["run"]["result"]["step"] == "openings.detect"
        kept = [o for o in body["run"]["observations"] if o["filtered"] is None]
        dropped = [o for o in body["run"]["observations"] if o["filtered"]]
        assert len(kept) == 1 and kept[0]["kind"] == "window"
        assert kept[0]["host_wall_name"] == "Mur haut"
        assert kept[0]["width_m"] == pytest.approx(1.2, rel=0.03)
        assert len(dropped) == 1 and dropped[0]["reason"]
        opening = body["run"]["project"]["levels"][lid]["elements"][kept[0]["element_id"]]
        assert opening["type"] == "opening" and opening["source"] == "observed"
        assert opening["confidence"] == 0.55 and opening["provenance"]["image_ids"] == [image["id"]]
        assert c.get(f"/api/projects/{pid}").json()["history_cursor"] == 2
        run_id = body["run"]["run_id"]
        art = c.get(f"/api/projects/{pid}/runs/{run_id}/openings/openings_overlay.png")
        assert art.status_code == 200 and art.headers["content-type"] == "image/png"
        assert c.get(f"/api/projects/{pid}/runs/{run_id}/openings/../x").status_code in (404, 409)
        # L'autre photo n'a pas de caméra : toujours 409, la scène n'a pas bougé.
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{other['id']}/openings")
        assert r.status_code == 409
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{pid}/openings")
        assert r.status_code == 404


def test_inspiration_runs_as_a_background_job(tmp_path: Path) -> None:
    """La route répond 202 tout de suite ; l'état se suit par /jobs/{id} jusqu'au résultat ;
    le périphérique est annoncé ; une tâche inconnue répond 404."""
    import time

    from tests.unit.test_depth_step import GradientDepth
    from tests.unit.test_ingestion import jpeg_with_exif
    from tests.unit.test_inspiration import FakeGenerator

    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store,
        settings=Settings(data_dir=tmp_path / "data", device="cpu"),
        depth_estimator=GradientDepth(),
        image_generator=FakeGenerator(),
    )
    with TestClient(app) as c:
        device = c.get("/api/device").json()
        assert device["requested"] == "cpu" and device["device"] == "cpu"
        pid = c.post("/api/projects", json={"name": "inspi"}).json()["id"]
        image = c.post(
            f"/api/projects/{pid}/images", files={"file": ("p.jpg", jpeg_with_exif(), "image/jpeg")}
        ).json()["image"]
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/inspire", json={"steps": 12})
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["status"] in ("running", "done") and job["kind"] == "inspiration"
        assert job["device"] == "cpu" and "processeur" in job["message"]
        assert job["expected_s"] == pytest.approx(240.0)
        for _ in range(200):
            job = c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.02)
        assert job["status"] == "done", job
        result = job["result"]
        assert result["source"] == "generated" and result["image_id"] == image["id"]
        names = {a["name"] for a in result["result"]["artifacts"]}
        assert "inspiration.png" in names
        png = c.get(f"/api/projects/{pid}/runs/{result['run_id']}/inspiration/inspiration.png")
        assert png.status_code == 200
        assert c.get(f"/api/projects/{pid}/jobs/{pid}").status_code == 404
        assert c.post(f"/api/projects/{pid}/images/{pid}/inspire").status_code == 404


def test_web_front_served_by_the_api_on_the_same_port(tmp_path: Path) -> None:
    """`openplan serve --web` : le front construit est servi sur `/`, l'API garde `/api`."""
    from openplan.api.app import WebBuildMissingError

    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>PlanDesign</title>", "utf-8")
    (web / "assets" / "app.js").write_text("console.log('ok')", "utf-8")
    store = ProjectStore(tmp_path / "data")
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data", web_dir=web))
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200 and "PlanDesign" in home.text
        assert client.get("/assets/app.js").text == "console.log('ok')"
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/projects").json() == []
    with pytest.raises(WebBuildMissingError, match=r"index\.html"):
        create_app(
            store=store, settings=Settings(data_dir=tmp_path / "data", web_dir=tmp_path / "nope")
        )


def test_token_required_outside_loopback(tmp_path: Path) -> None:
    from openplan.api.app import TokenMissingError

    marie = "marie-token-0123456789abcdef"
    exposed = Settings(
        data_dir=tmp_path / "data",
        host="0.0.0.0",
        api_token="s3cret-token-123456",
        api_tokens={"marie": marie},
    )
    with TestClient(create_app(settings=exposed)) as c:
        assert c.get("/api/health").status_code == 200, "la santé reste publique"
        assert c.get("/api/health").json()["exposed"] is True
        r = c.get("/api/projects")
        assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
        assert "jeton" in r.json()["detail"] and "s3cret" not in r.text
        assert r.headers["content-security-policy"], "en-têtes même sur un refus"
        assert c.get("/api/projects", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/api/projects", params={"token": "s3cret-token-123456"}).status_code == 401, (
            "jamais dans l'URL"
        )
        ok = c.get("/api/projects", headers={"Authorization": "Bearer s3cret-token-123456"})
        assert ok.status_code == 200 and ok.json() == []
        # Jeton nommé : l'historique porte user:marie ; le jeton unique reste user:invité.
        auth = {"Authorization": f"Bearer {marie}"}
        project = c.post("/api/projects", json={"name": "de marie"}, headers=auth).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/ops",
            json={"op": "add_wall", "start": {"x": 0, "y": 0}, "end": {"x": 3, "y": 0}},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert r.json()["project"]["history"][-1]["actor"] == "user:marie"
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/ops",
            json={"op": "add_wall", "start": {"x": 3, "y": 0}, "end": {"x": 3, "y": 2}},
            headers={"Authorization": "Bearer s3cret-token-123456"},
        )
        assert r.json()["project"]["history"][-1]["actor"] == "user:invité"
    # Journal des accès : acteur, méthode, chemin, code ; jamais le jeton, jamais le contenu.
    log = (tmp_path / "data" / "access.jsonl").read_text(encoding="utf-8")
    entries = [json.loads(line) for line in log.splitlines()]
    assert any(e["actor"] == "-" and e["status"] == 401 for e in entries)
    assert any(e["actor"] == "user:marie" and e["method"] == "POST" for e in entries)
    assert marie not in log and "s3cret" not in log and "de marie" not in log
    assert not any("/api/health" in e["path"] for e in entries)
    # Boucle locale : pas de jeton, même s'il est configuré.
    local = Settings(data_dir=tmp_path / "data", host="127.0.0.1", api_token="s3cret-token")
    with TestClient(create_app(settings=local)) as c:
        assert c.get("/api/projects").status_code == 200
    with pytest.raises(TokenMissingError, match="OPENPLAN_API_TOKEN"):
        create_app(settings=Settings(data_dir=tmp_path / "data", host="0.0.0.0"))


def test_second_heavy_task_waits_in_queue_then_starts(tmp_path: Path) -> None:
    """File d'attente : la seconde tâche lourde n'est plus refusée ; elle attend (`queued`, place
    1), s'annule, ou démarre seule quand la première finit."""
    import threading

    from tests.unit.test_depth_step import GradientDepth
    from tests.unit.test_ingestion import jpeg_with_exif
    from tests.unit.test_inspiration import FakeGenerator

    gate = threading.Event()

    class SlowGenerator(FakeGenerator):
        def generate(self, request):  # type: ignore[no-untyped-def]
            gate.wait(5)
            return super().generate(request)

    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store,
        settings=Settings(data_dir=tmp_path / "data", device="cpu"),
        depth_estimator=GradientDepth(),
        image_generator=SlowGenerator(),
    )
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "occupé"}).json()["id"]
        image = c.post(
            f"/api/projects/{pid}/images", files={"file": ("p.jpg", jpeg_with_exif(), "image/jpeg")}
        ).json()["image"]
        first = c.post(f"/api/projects/{pid}/images/{image['id']}/inspire", json={"steps": 12})
        assert first.status_code == 202, first.text
        assert first.json()["status"] == "running"
        url = f"/api/projects/{pid}/images/{image['id']}/inspire"
        second = c.post(url, json={"steps": 12})
        assert second.status_code == 202, second.text
        assert second.json()["status"] == "queued" and second.json()["queue_position"] == 1
        third = c.post(url, json={"steps": 12}).json()
        assert third["queue_position"] == 2
        running = c.get("/api/jobs/running").json()
        assert running["running"] is True and running["kind"] == "inspiration"
        assert running["queued"] == 2
        # Annuler une tâche en attente ; une tâche lancée ne s'annule pas (409).
        cancelled = c.delete(f"/api/projects/{pid}/jobs/{third['id']}")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert c.delete(f"/api/projects/{pid}/jobs/{first.json()['id']}").status_code == 409
        gate.set()
        job = second.json()
        for _ in range(500):
            job = c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
            if job["status"] in {"done", "failed"}:
                break
            time.sleep(0.02)
        assert job["status"] == "done", job
        assert c.get(f"/api/projects/{pid}/jobs/{first.json()['id']}").json()["status"] == "done"
        assert c.get(f"/api/projects/{pid}/jobs/{third['id']}").json()["status"] == "cancelled"


def test_render_views_routes(tmp_path: Path) -> None:
    """R1 : point de vue libre par commande, estimation qui annonce périphérique et durée (ou
    l'absence de Blender), rendu par point de vue refusé proprement sans Blender ou sans
    caméra, galerie vide au départ."""
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data", blender_bin="/nonexistent")
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "rendre"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/ops",
            json={"op": "add_camera", "position": {"x": 2, "y": 1.5}, "yaw": 0.3},
        )
        assert r.status_code == 200, r.text
        level = next(iter(r.json()["project"]["levels"].values()))
        cams = [e for e in level["elements"].values() if e["type"] == "camera"]
        assert len(cams) == 1 and cams[0]["name"] == "Point de vue 1"
        assert cams[0]["position"]["z"] == 1.5 and cams[0]["source"] == "user_defined"
        est = c.get(f"/api/projects/{pid}/levels/{lid}/render/estimate", params={"samples": 16})
        assert est.status_code == 200, est.text
        body = est.json()
        assert body["blender_available"] is False and "Blender" in body["message"]
        assert body["device"] in ("cuda", "cpu") and body["expected_s"] > 0
        r = c.post(f"/api/projects/{pid}/levels/{lid}/cameras/{cams[0]['id']}/render")
        assert r.status_code == 409 and "introuvable" in r.json()["detail"]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/cameras/{pid}/render")
        assert r.status_code == 409 and "point de vue inconnu" in r.json()["detail"]
        assert c.get(f"/api/projects/{pid}/renders").json() == []


def test_blender_run_without_binary_is_409(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data", blender_bin="/nonexistent")
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "x"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        r = c.post(f"/api/projects/{pid}/levels/{lid}/blender")
        assert r.status_code == 409 and "introuvable" in r.json()["detail"]
        # Rendu depuis la photo : sans caméra, le message le dit avant même de chercher Blender.
        r = c.post(f"/api/projects/{pid}/levels/{lid}/render")
        assert r.status_code == 409 and "aucune caméra" in r.json()["detail"]
        # Avec une caméra (combles) mais sans Blender : message explicite, le GLB reste servi.
        from tests.factories import attic_project

        attic = attic_project()
        store.save(attic)
        lid2 = next(iter(attic.levels))
        r = c.post(f"/api/projects/{attic.id}/levels/{lid2}/render", params={"samples": 8})
        assert r.status_code == 409 and "introuvable" in r.json()["detail"]
        glb = c.get(
            f"/api/projects/{attic.id}/exports/glb", params={"level_id": lid2, "style": "realistic"}
        )
        assert glb.status_code == 200 and glb.content[:4] == b"glTF"
        assert (
            c.get(
                f"/api/projects/{attic.id}/exports/glb", params={"level_id": lid2, "style": "x"}
            ).status_code
            == 422
        )


def test_images_roundtrip(client: TestClient) -> None:
    from tests.unit.test_ingestion import jpeg_with_exif

    project = client.post("/api/projects", json={"name": "photo"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    r = client.post(
        f"/api/projects/{pid}/images",
        files={"file": ("salon.jpg", jpeg_with_exif(), "image/jpeg")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    image = body["image"]
    assert image["exif_stripped"] is True and image["focal_35mm"] == 28.0
    assert image["width_px"] == 480 and body["project"]["images"][image["id"]] == image
    assert any("Résolution faible" in w for w in body["warnings"])

    f = client.get(f"/api/projects/{pid}/images/{image['id']}/file")
    assert f.status_code == 200 and f.headers["content-type"] == "image/jpeg"
    # Liste des projets (index SQLite) et vignette.
    (summary,) = [s for s in client.get("/api/projects").json() if s["id"] == pid]
    assert summary["image_count"] == 1 and summary["has_thumbnail"] is True
    assert summary["wall_count"] == 0 and summary["created_at"]
    thumb = client.get(f"/api/projects/{pid}/thumbnail")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert f.content[:2] == b"\xff\xd8"
    q = client.get(f"/api/projects/{pid}/images/{image['id']}/quality").json()
    assert q["quality"] == image["quality"] and q["warnings"] == body["warnings"]

    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={
            "op": "update_level",
            "fields": {"background_image": image["id"], "background_transform": {"scale": 0.01}},
        },
    )
    assert r.status_code == 200 and r.json()["project"]["levels"][lid]["background_image"]

    r = client.delete(f"/api/projects/{pid}/images/{image['id']}")
    assert r.status_code == 200
    assert r.json()["images"] == {} and r.json()["levels"][lid]["background_image"] is None
    assert client.get(f"/api/projects/{pid}/images/{image['id']}/file").status_code == 404
    assert client.get(f"/api/projects/{pid}/thumbnail").status_code == 404
    (summary,) = [s for s in client.get("/api/projects").json() if s["id"] == pid]
    assert summary["image_count"] == 0 and summary["has_thumbnail"] is False
    r = client.post(
        f"/api/projects/{pid}/images", files={"file": ("x.txt", b"hello", "text/plain")}
    )
    assert r.status_code == 422 and "illisible" in r.json()["detail"]


class RemoteFakeModel:
    """Modèle « distant » simulé : aucune requête réseau, une réponse JSON fixe."""

    model_id = "fake:remote"
    remote = True
    supports_images = True
    destination = "api.example.invalid"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, request: Any) -> Any:
        from openplan.adapters.protocols import LlmResponse

        self.calls += 1
        return LlmResponse(
            text='{"elements": [{"category": "window", "box": [0.3, 0.1, 0.7, 0.6], '
            '"confidence": 0.8}], "room": {"shape": "rectangular", "width_m": 4.0, '
            '"ceiling_height_m": 2.5, "confidence": 0.5}, "notes": "rendu synthétique"}',
            model_id=self.model_id,
            input_tokens=900,
            output_tokens=80,
        )


def test_brief_preview_and_intention_route(tmp_path: Path) -> None:
    """Aperçu sans modèle ni envoi ; brief avec un modèle simulé (texte seul, journal
    `intention`, 0 image) ; 409 sans modèle ; 403 sans consentement pour un modèle distant."""
    from tests.unit.test_intention import GOOD, ScriptedModel

    store = ProjectStore(tmp_path / "data")
    with TestClient(create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))) as c:
        project = c.post("/api/projects", json={"name": "sans modèle"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        c.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief/preview", json={"text": "un lit"})
        assert r.status_code == 200, r.text
        preview = r.json()
        assert preview["model_id"] is None and "m² au sol" in preview["room_description"]
        assert "bed_double" in preview["categories"]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief", json={"text": "un lit"})
        assert r.status_code == 409 and "aucun modèle" in r.json()["detail"]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief", json={"text": "ok"})
        assert r.status_code == 422

    local = ScriptedModel(json.dumps(GOOD))
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=local
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "intention"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        body = {"text": "chambre d'amis avec bureau, le lit fait 160 × 200"}
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief", json=body)
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["result"]["step"] == "intention.brief"
        assert [i["category"] for i in run["brief"]["items"]] == [
            "bed_double",
            "desk",
            "nightstand",
        ]
        assert run["brief"]["items"][0]["dimensions_m"]["width"] == 1.6
        assert (
            run["preview"]["destination"] == "localhost:11434" and run["preview"]["remote"] is False
        )
        log = c.get(f"/api/projects/{pid}/network-log").json()
        assert [e["purpose"] for e in log] == ["intention"] and log[0]["images"] == 0
        art = c.get(f"/api/projects/{pid}/runs/{run['run_id']}/intention/brief.json")
        assert art.status_code == 200 and art.json()["items"]
        # La scène n'a pas bougé : aucun meuble, historique intact.
        assert c.get(f"/api/projects/{pid}").json()["history_cursor"] == 4

    remote = ScriptedModel(json.dumps(GOOD))
    remote.remote = True  # type: ignore[misc]
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=remote
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "distant"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief/preview", json={"text": "un lit"})
        assert r.status_code == 200 and r.json()["remote"] is True
        r = c.post(f"/api/projects/{pid}/levels/{lid}/brief", json={"text": "un lit"})
        assert r.status_code == 403 and remote.requests == []


def test_layouts_route_proposes_then_applies_on_user_gesture(client: TestClient) -> None:
    """3.4 : brief → variantes valides et distinctes (score + phrase), artefact, scène intacte ;
    « Appliquer » ajoute les meubles `generated` par un geste utilisateur, annulable."""
    project = client.post("/api/projects", json={"name": "dispositions"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    brief = {
        "items": [
            {"category": "bed_double", "near": ["wall"]},
            {"category": "desk", "priority": "nice_to_have"},
            {"category": "piano"},
        ]
    }
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts", json={"brief": brief, "variants": 2}
    )
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["result"]["step"] == "layout.generate" and len(run["proposals"]) == 2
    best = run["proposals"][0]
    assert (
        best["valid"]
        and best["summary"].startswith(("Très bien", "Correct", "À revoir"))
        and "non placé" in best["summary"]
    )
    assert best["score"] >= run["proposals"][1]["score"]
    assert {f["source"] for f in best["furniture"]} == {"generated"}
    assert best["unplaced"][0]["category"] == "piano"
    art = client.get(f"/api/projects/{pid}/runs/{run['run_id']}/layout/layouts.json")
    assert art.status_code == 200 and art.json()["proposals"]
    before = client.get(f"/api/projects/{pid}").json()
    assert not any(
        e["type"] == "furniture" for e in next(iter(before["levels"].values()))["elements"].values()
    ), "rien n'entre dans la scène sans « Appliquer »"

    r = client.post(f"/api/projects/{pid}/levels/{lid}/layouts/apply", json={"proposal": best})
    assert r.status_code == 200, r.text
    after = r.json()["project"]
    level = next(iter(after["levels"].values()))
    added = [e for e in level["elements"].values() if e["type"] == "furniture"]
    assert len(added) == len(best["furniture"]) == 2
    assert after["history"][-1]["actor"] == "user"
    assert "Appliquer la variante 1" in after["history"][-1]["label"]
    undone = client.post(f"/api/projects/{pid}/undo").json()
    level = next(iter(undone["levels"].values()))
    assert not any(e["type"] == "furniture" for e in level["elements"].values())

    # Pièce inconnue : refus explicite ; variante vide : refus.
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts", json={"brief": brief, "room_id": pid}
    )
    assert r.status_code == 409 and "pièce inconnue" in r.json()["detail"]
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts/apply",
        json={"proposal": {**best, "furniture": []}},
    )
    assert r.status_code == 409 and "aucun meuble" in r.json()["detail"]


def test_ai_perception_requires_consent_and_logs(tmp_path: Path) -> None:
    from tests.unit.test_ingestion import jpeg_with_exif

    store = ProjectStore(tmp_path / "data")
    model = RemoteFakeModel()
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=model
    )
    with TestClient(app) as c:
        status = c.get("/api/ai").json()
        assert (
            status["configured"]
            and status["remote"]
            and status["destination"] == "api.example.invalid"
        )
        project = c.post("/api/projects", json={"name": "ia"}).json()
        pid = project["id"]
        image = c.post(
            f"/api/projects/{pid}/images", files={"file": ("p.jpg", jpeg_with_exif(), "image/jpeg")}
        ).json()["image"]
        # Sans consentement : refus, aucun appel, journal vide.
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/perceive")
        assert r.status_code == 403 and "allow_remote_models" in r.json()["detail"]
        c.patch(f"/api/projects/{pid}", json={"privacy": {"allow_remote_models": True}})
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/perceive")
        assert r.status_code == 403 and "remote_consent" in r.json()["detail"]
        assert model.calls == 0 and c.get(f"/api/projects/{pid}/network-log").json() == []
        # Double consentement : appel, artefacts, journal sans contenu.
        c.patch(
            f"/api/projects/{pid}",
            json={"privacy": {"allow_remote_models": True, "remote_consent": True}},
        )
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/perceive")
        assert r.status_code == 200, r.text
        run = r.json()
        assert model.calls == 2, "une sonde texte sans image, puis l'image"
        assert run["perception"]["room"]["width_m"] == 4.0
        assert run["elements"][0]["category"] == "window" and run["elements"][0]["score"] <= 0.6
        assert run["result"]["step"] == "perception.vlm" and run["result"]["patch"] == []
        assert any("rendu synthétique" in w for w in run["result"]["warnings"])
        names = {a["name"] for a in run["result"]["artifacts"]}
        assert names == {"vlm_raw.txt", "vlm_perception.json", "vlm_overlay.png"}
        art = c.get(f"/api/projects/{pid}/runs/{run['run_id']}/perception/vlm_overlay.png")
        assert art.status_code == 200 and art.headers["content-type"] == "image/png"
        probe, entry = c.get(f"/api/projects/{pid}/network-log").json()
        assert probe["probe"] and probe["images"] == 0 and probe["ok"]
        assert entry["remote"] and entry["images"] == 1 and entry["purpose"] == "perception"
        assert entry["ok"] and entry["destination"] == "api.example.invalid" and not entry["probe"]
        # La scène n'a pas changé.
        assert c.get(f"/api/projects/{pid}").json()["levels"] == project["levels"]


def test_depth_route_with_fake_estimator(tmp_path: Path) -> None:
    from tests.unit.test_depth_step import GradientDepth
    from tests.unit.test_ingestion import jpeg_with_exif

    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), depth_estimator=GradientDepth()
    )
    with TestClient(app) as c:
        models = c.get("/api/models").json()
        depth_entry = next(m for m in models if m["entry"]["id"].startswith("depth_anything"))
        assert isinstance(depth_entry["installed"], bool), "poids : installés ou non, jamais None"
        assert depth_entry["entry"]["license"] == "Apache-2.0"
        assert (
            next(m for m in models if m["entry"]["id"] == "heuristic_box@0.1")["installed"] is None
        )
        project = c.post("/api/projects", json={"name": "d"}).json()
        pid = project["id"]
        image = c.post(
            f"/api/projects/{pid}/images", files={"file": ("p.jpg", jpeg_with_exif(), "image/jpeg")}
        ).json()["image"]
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/depth")
        assert r.status_code == 200, r.text
        run = r.json()
        assert (
            run["stats"]["min_m"] == pytest.approx(1.0) and run["result"]["step"] == "depth.metric"
        )
        png = c.get(f"/api/projects/{pid}/runs/{run['run_id']}/depth/depth.png")
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        assert c.post(f"/api/projects/{pid}/images/{pid}/depth").status_code == 404


def test_attic_route_with_synthetic_depth(tmp_path: Path) -> None:
    """Profondeur synthétique exacte d'un comble, à la focale que l'API déduira de l'image.

    L'image est unie : aucun segment, donc aucun point de fuite, et la focale vient de l'EXIF
    (18 mm : grand angle, les deux versants sont vus) ; la profondeur simulée est rendue avec
    cette même focale."""
    import io

    from PIL import Image

    from openplan.adapters.exif_camera import ExifCameraEstimator
    from openplan.adapters.protocols import DepthMap, ImageArray
    from tests.synthetic_depth import SyntheticCamera, attic_faces, render_depth
    from tests.unit.test_ingestion import EXIF_IFD, FOCAL_35MM

    class AtticDepth:
        model_id = "fake_depth@0"

        def predict(self, image: ImageArray) -> DepthMap:
            focal = ExifCameraEstimator().estimate(image).focal_px
            assert focal == pytest.approx(320.0), "18 mm sur 36 mm de capteur, 640 px"
            cam = SyntheticCamera(
                position=(1.5, -0.4, 1.3),
                yaw=0.2,
                pitch=-0.10,
                focal_px=focal,
                width_px=image.width,
                height_px=image.height,
            )
            return DepthMap(
                depth=render_depth(attic_faces(), cam), metric=True, model_id=self.model_id
            )

    plain = io.BytesIO()
    exif = Image.Exif()
    exif.get_ifd(EXIF_IFD)[FOCAL_35MM] = 18
    Image.new("RGB", (640, 480), (128, 128, 128)).save(plain, format="JPEG", exif=exif.tobytes())
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), depth_estimator=AtticDepth()
    )
    with TestClient(app) as c:
        pid = c.post("/api/projects", json={"name": "combles"}).json()["id"]
        image = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("p.jpg", plain.getvalue(), "image/jpeg")},
        ).json()["image"]
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/attic")
        assert r.status_code == 200, r.text
        run = r.json()
        est = run["estimate"]
        assert est["camera_height"]["value"] == pytest.approx(1.3, rel=0.08)
        assert est["ridge_height"]["value"] == pytest.approx(3.2, rel=0.08)
        assert est["width"]["value"] == pytest.approx(5.0, rel=0.08)
        assert est["gable_distance"]["value"] == pytest.approx(4.5, rel=0.08)
        assert est["scale_factor"] == 1.0 and est["scale_note"] is None
        # Hauteur saisie 2,2 fois trop grande : tout est recalé, et l'écart (> 2) est signalé.
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/attic", json={"camera_height_m": 2.9})
        assert r.status_code == 200, r.text
        rescaled = r.json()
        assert rescaled["estimate"]["camera_height"]["source"] == "user_defined"
        assert rescaled["estimate"]["ridge_height"]["value"] == pytest.approx(7.14, rel=0.08)
        assert rescaled["estimate"]["scale_factor"] == pytest.approx(2.23, rel=0.08)
        assert any("divergent d'un facteur" in w for w in rescaled["result"]["warnings"])
        assert any("saisie 2,90 m" in a["text"] for a in rescaled["result"]["assumptions"])
        r = c.post(f"/api/projects/{pid}/images/{image['id']}/attic", json={"camera_height_m": 9.0})
        assert r.status_code == 422
        assert est["camera_height"]["source"] == "estimated"
        assert run["result"]["step"] == "attic.planes" and run["result"]["patch"] == []
        assert {a["name"] for a in run["result"]["artifacts"]} == {
            "camera.json",
            "planes.png",
            "attic.json",
        }
        png = c.get(f"/api/projects/{pid}/runs/{run['run_id']}/attic/planes.png")
        assert png.status_code == 200 and png.headers["content-type"] == "image/png"
        assert c.get(f"/api/projects/{pid}/runs/{run['run_id']}/attic/../x").status_code in (
            404,
            409,
        )
        assert c.post(f"/api/projects/{pid}/images/{pid}/attic").status_code == 404
        _apply_attic_via_api(c, pid, image["id"], run)


def _apply_attic_via_api(c: TestClient, pid: str, image_id: str, run: dict[str, Any]) -> None:
    """Validation par l'utilisateur : la longueur (un seul pignon vu) est déduite, la largeur
    corrigée à la main ; la scène reçoit murs, pentes, pièce et caméra avec ces sources."""
    est = run["estimate"]
    lid = next(iter(c.get(f"/api/projects/{pid}").json()["levels"]))
    body = {
        "length": {
            "value": est["gable_distance"]["value"] + 0.5,
            "sigma": 1.0,
            "source": "inferred",
        },
        "width": {"value": 5.2, "sigma": 0.05, "source": "user_defined"},
        "ridge_height": est["ridge_height"],
        "eave_height": est["eave_heights"][0],
        "camera_height": est["camera_height"],
        "gable_distance": est["gable_distance"],
        "image_id": image_id,
        "run_id": run["run_id"],
    }
    r = c.post(f"/api/projects/{pid}/levels/{lid}/attic", json=body)
    assert r.status_code == 200, r.text
    applied = r.json()
    by_type: dict[str, list[dict[str, object]]] = {}
    for e in applied["project"]["levels"][lid]["elements"].values():
        by_type.setdefault(str(e["type"]), []).append(e)
    assert len(by_type["wall"]) == 4 and len(by_type["roof_plane"]) == 2
    assert by_type["room"][0]["name"] == "Combles" and "ceiling" not in by_type
    assert by_type["camera"][0]["image_id"] == image_id
    gables = [w for w in by_type["wall"] if w["name"] == "Pignon"]
    assert all(w["source"] == "user_defined" for w in gables), "largeur saisie"
    eaves = [w for w in by_type["wall"] if str(w["name"]).startswith("Égout")]
    assert all(w["source"] == "inferred" for w in eaves), "longueur déduite"
    assert applied["report"]["valid"] and applied["result"]["step"] == "attic.apply"
    assert applied["project"]["history"][-1]["actor"] == "user", "validé par l'utilisateur"
    derived = c.get(f"/api/projects/{pid}/levels/{lid}/derived").json()
    assert len(derived["roofs"]) == 2
    assert c.post(f"/api/projects/{pid}/levels/{lid}/attic", json=body).status_code == 409
    svg = c.get(f"/api/projects/{pid}/exports/svg", params={"level_id": lid}).text
    assert "1,80 m sous pente" in svg
    glb = c.get(f"/api/projects/{pid}/exports/glb", params={"level_id": lid})
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"


def test_depth_route_without_weights_is_409(tmp_path: Path) -> None:
    from tests.unit.test_ingestion import jpeg_with_exif

    store = ProjectStore(tmp_path / "data")
    settings = Settings(data_dir=tmp_path / "data", models_dir=tmp_path / "aucun-poids")
    with TestClient(create_app(store=store, settings=settings)) as c:
        project = c.post("/api/projects", json={"name": "d"}).json()
        image = c.post(
            f"/api/projects/{project['id']}/images",
            files={"file": ("p.jpg", jpeg_with_exif(), "image/jpeg")},
        ).json()["image"]
        r = c.post(f"/api/projects/{project['id']}/images/{image['id']}/depth")
        assert r.status_code == 409 and "models pull" in r.json()["detail"]


def test_ai_unconfigured(client: TestClient) -> None:
    assert client.get("/api/ai").json() == {
        "configured": False,
        "model_id": None,
        "destination": None,
        "remote": None,
        "supports_images": None,
    }
    project = client.post("/api/projects", json={"name": "x"}).json()
    r = client.post(f"/api/projects/{project['id']}/images/{project['id']}/perceive")
    assert r.status_code == 409 and "aucun modèle" in r.json()["detail"]


def test_update_settings(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "x"}).json()
    pid = project["id"]
    r = client.patch(f"/api/projects/{pid}", json={"units_display": "cm", "name": "Salon"})
    assert r.status_code == 200
    assert r.json()["units_display"] == "cm" and r.json()["name"] == "Salon"
    assert r.json()["history"][-1]["label"] == "Modifier les réglages"
    r = client.patch(f"/api/projects/{pid}", json={"privacy": {"strip_exif": False}})
    assert r.json()["privacy"]["strip_exif"] is False
    assert client.patch(f"/api/projects/{pid}", json={"units_display": "ft"}).status_code == 422


def test_error_mapping(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "x"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    # Commande invalide (référence inconnue) → 409.
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "remove_element", "element_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 409 and "inconnu" in r.json()["detail"]
    # Rien à annuler → 409.
    assert client.post(f"/api/projects/{pid}/undo").status_code == 409
    # Corps mal formé → 422 (FastAPI).
    r = client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "teleport"})
    assert r.status_code == 422
    # Édition qui casse un invariant → 422 avec rapport.
    _draw_rectangle(client, pid, lid)
    wall_id = next(iter(_walls(client.get(f"/api/projects/{pid}").json())))
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={
            "op": "add_opening",
            "host_wall": wall_id,
            "kind": "door",
            "offset": 3.9,
            "width": 0.9,
            "height": 2.0,
        },
    )
    assert r.status_code == 422
    assert r.json()["report"]["violations"][0]["type"] == "hosted_element_exceeds_wall"
    # Calibration sans mesure → 409.
    assert client.post(f"/api/projects/{pid}/levels/{lid}/calibrate").status_code == 409
    # Projet inconnu → 404.
    assert client.get("/api/projects/00000000-0000-0000-0000-000000000000").status_code == 404


def test_critique_and_revise_routes(client: TestClient, tmp_path: Path) -> None:  # noqa: PLR0915
    """3.5 : remarques des règles sans modèle (chacune cite meuble + règle, triées, action),
    variante révisée par une action (à relire, rien dans la scène), refus explicites, puis
    modèle simulé (texte seul, journal `critique`), et 403 pour un distant sans consentement."""
    project = client.post("/api/projects", json={"name": "critique"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    brief = {"items": [{"category": "bed_double", "near": ["wall"]}, {"category": "desk"}]}
    run = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts", json={"brief": brief, "variants": 1}
    ).json()
    best = run["proposals"][0]
    desk = next(f for f in best["furniture"] if f["category"] == "desk")
    # Un brief contraire pour la critique : le bureau devait être près d'un radiateur (absent).
    contrary = {"items": [{"category": "desk", "near": ["radiator"]}]}
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts/critique",
        json={"proposal": best, "brief": contrary},
    )
    assert r.status_code == 200, r.text
    critique = r.json()
    assert critique["result"]["step"] == "layout.critique" and critique["model_count"] == 0
    assert any("règles seules" in w for w in critique["result"]["warnings"])
    remarks = critique["critique"]["remarks"]
    near = next(x for x in remarks if x["rule"] == "preference_near")
    assert near["furniture_id"] == desk["id"] and near["furniture_label"] and near["rule_label"]
    assert near["action"]["kind"] == "move_near" and near["action"]["target"] == "radiator"
    assert near["action"]["label"].startswith("Déplacer")
    art = client.get(f"/api/projects/{pid}/runs/{critique['run_id']}/layout/critique.json")
    assert art.status_code == 200 and art.json()["remarks"]
    # L'action impossible est refusée en clair ; « retirer » donne une variante révisée.
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts/revise",
        json={"proposal": best, "action": near["action"], "brief": contrary},
    )
    assert r.status_code == 409 and "aucune position" in r.json()["detail"]
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts/revise",
        json={
            "proposal": best,
            "action": {"kind": "remove", "furniture_id": desk["id"]},
            "brief": brief,
        },
    )
    assert r.status_code == 200, r.text
    revised = r.json()
    assert revised["index"] == best["index"] and len(revised["furniture"]) == 1
    assert revised["summary"].startswith(("Très bien", "Correct", "À revoir"))
    level = next(iter(client.get(f"/api/projects/{pid}").json()["levels"].values()))
    assert not any(e["type"] == "furniture" for e in level["elements"].values()), (
        "rien n'entre dans la scène par une critique ou une révision"
    )
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/layouts/critique",
        json={"proposal": {**best, "furniture": []}},
    )
    assert r.status_code == 409 and "aucun meuble" in r.json()["detail"]

    from tests.unit.test_critique import GOOD_CRITIQUE, ScriptedCritic

    store = ProjectStore(tmp_path / "data")
    local = ScriptedCritic(json.dumps(GOOD_CRITIQUE))
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=local
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "critique modèle"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        c.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
        run = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts", json={"brief": brief, "variants": 1}
        ).json()
        best = run["proposals"][0]
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts/critique/preview",
            json={"proposal": best, "brief": brief},
        )
        assert r.status_code == 200 and r.json()["destination"] == "localhost:11434"
        assert local.requests == [], "l'aperçu n'envoie rien"
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts/critique",
            json={"proposal": best, "brief": brief},
        )
        assert r.status_code == 200, r.text
        critique = r.json()
        assert critique["model_count"] >= 1
        assert critique["result"]["model_ids"] == ["llm:fake:critic"]
        assert any(x["origin"] == "model" for x in r.json()["critique"]["remarks"])
        log = c.get(f"/api/projects/{pid}/network-log").json()
        assert [e["purpose"] for e in log] == ["critique"] and log[0]["images"] == 0

    remote = ScriptedCritic(json.dumps(GOOD_CRITIQUE))
    remote.remote = True
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=remote
    )
    with TestClient(app) as c:
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts/critique",
            json={"proposal": best, "brief": brief},
        )
        assert r.status_code == 403 and remote.requests == []
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts/critique",
            json={"proposal": best, "brief": brief, "use_model": False},
        )
        assert r.status_code == 200 and r.json()["model_count"] == 0


def test_furniture_move_check_route(client: TestClient) -> None:
    """3.6 : pendant un glisser, la route dit si la position essayée casse une règle dure, en
    deux mots, sans rien modifier."""
    project = client.post("/api/projects", json={"name": "glisser"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/ops",
        json={"op": "add_furniture", "asset_id": "desk_140", "position": {"x": 2.0, "y": 1.5}},
    )
    assert r.status_code == 200, r.text
    level = next(iter(r.json()["project"]["levels"].values()))
    desk = next(e for e in level["elements"].values() if e["type"] == "furniture")
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/furniture/{desk['id']}/check",
        json={"position": {"x": 2.0, "y": 1.6}, "yaw": 0.0},
    )
    assert r.status_code == 200 and r.json()["valid"] and r.json()["message"] == ""
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/furniture/{desk['id']}/check",
        json={"position": {"x": 2.0, "y": 3.0}, "yaw": 0.0},
    )
    assert r.status_code == 200, r.text
    assert not r.json()["valid"] and r.json()["message"] == "meuble dans un mur"
    after = client.get(f"/api/projects/{pid}").json()
    moved = next(iter(after["levels"].values()))["elements"][desk["id"]]
    assert moved["transform"]["position"]["y"] == 1.5, "la vérification ne déplace rien"
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/furniture/{lid}/check",
        json={"position": {"x": 1.0, "y": 1.0}},
    )
    assert r.status_code == 409 and "meuble inconnu" in r.json()["detail"]


def test_furniture_place_check_route(client: TestClient) -> None:
    """F3 : un meuble de la bibliothèque, essayé avant d'être posé : mêmes règles, rien ajouté."""
    project = client.post("/api/projects", json={"name": "bibliotheque"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    url = f"/api/projects/{pid}/levels/{lid}/furniture/check"
    r = client.post(url, json={"asset_id": "armchair", "position": {"x": 2.0, "y": 1.5}})
    assert r.status_code == 200 and r.json()["valid"], r.text
    r = client.post(url, json={"asset_id": "armchair", "position": {"x": 2.0, "y": 3.0}})
    assert r.status_code == 200 and r.json()["message"] == "meuble dans un mur"
    after = client.get(f"/api/projects/{pid}").json()
    level = next(iter(after["levels"].values()))
    assert not [e for e in level["elements"].values() if e["type"] == "furniture"]
    r = client.post(url, json={"asset_id": "licorne", "position": {"x": 1.0, "y": 1.0}})
    assert r.status_code == 409 and "meuble inconnu" in r.json()["detail"]


def test_variant_render_route_refuses_clearly(tmp_path: Path) -> None:
    """3.7 : vignette d'une variante ; sans Blender la route le dit (409) et rien n'est lancé ;
    une variante vide est refusée avant de chercher Blender."""
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data", blender_bin="/nonexistent")
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "vignettes"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        c.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
        run = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts",
            json={"brief": {"items": [{"category": "bed_double"}]}, "variants": 1},
        ).json()
        best = run["proposals"][0]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/layouts/render", json={"proposal": best})
        assert r.status_code == 409 and "Blender introuvable" in r.json()["detail"]
        r = c.post(
            f"/api/projects/{pid}/levels/{lid}/layouts/render",
            json={"proposal": {**best, "furniture": []}},
        )
        assert r.status_code == 409 and "aucun meuble" in r.json()["detail"]
        assert c.get(f"/api/projects/{pid}/renders").json() == []


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")
def test_autoplan_route_builds_the_plan_in_the_background(tmp_path: Path) -> None:
    """Directive « tout automatique » : une photo déposée, une requête, un plan construit ;
    les poids absents sont dits une fois avec la commande et ne bloquent rien."""
    import time

    store = ProjectStore(tmp_path / "data")
    settings = Settings(data_dir=tmp_path / "data", device="cpu", models_dir=tmp_path / "vide")
    with TestClient(create_app(store=store, settings=settings)) as c:
        project = c.post("/api/projects", json={"name": "auto"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        image = c.post(
            f"/api/projects/{pid}/images",
            files={"file": ("p.png", FIXTURE_IMAGE.read_bytes(), "image/png")},
        ).json()["image"]
        r = c.post(f"/api/projects/{pid}/levels/{lid}/images/{image['id']}/autoplan")
        assert r.status_code == 202, r.text
        job = r.json()
        assert job["kind"] == "autoplan" and "environ" in job["message"]
        for _ in range(600):
            job = c.get(f"/api/projects/{pid}/jobs/{job['id']}").json()
            if job["status"] != "running":
                break
            time.sleep(0.05)
        assert job["status"] == "done", job
        assert job["stage"] == "Plan prêt"
        run = job["result"]["run"]
        assert run["kind"] == "photo" and run["method"] == "lines"
        assert len(run["notices"]) == 1 and "models pull" in run["notices"][0]
        assert job["result"]["report"]["valid"] is True
        saved = c.get(f"/api/projects/{pid}").json()
        kinds = [e["type"] for e in saved["levels"][lid]["elements"].values()]
        assert kinds.count("wall") >= 4 and saved["units_display"] == "m"
        assert c.post(f"/api/projects/{pid}/levels/{lid}/images/{pid}/autoplan").status_code == 404
        assert c.post(
            f"/api/projects/{pid}/levels/{pid}/images/{image['id']}/autoplan"
        ).status_code in (404, 409)


def test_recalibrate_route_scales_the_level_and_is_undoable(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "recal"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    before = client.get(f"/api/projects/{pid}/levels/{lid}/derived").json()
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/recalibrate",
        json={"real_m": 3.0, "ceiling": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["what"] == "la hauteur sous plafond"
    factor = body["summary"]["factor"]
    assert factor == pytest.approx(3.0 / 2.5)
    level = body["project"]["levels"][lid]
    assert level["height"] == pytest.approx(3.0) and level["height_source"] == "measured"
    after = body["derived"]
    assert after["bounds"]["max"]["x"] == pytest.approx(before["bounds"]["max"]["x"] * factor)
    assert body["report"]["valid"] is True
    # Annulable : le geste est celui de l'utilisateur.
    undone = client.post(f"/api/projects/{pid}/undo").json()
    assert undone["levels"][lid]["height"] == pytest.approx(2.5)
    # Référence trop courte : 409, rien n'est enregistré.
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/recalibrate",
        json={"real_m": 3.0, "a": {"x": 0, "y": 0}, "b": {"x": 0.001, "y": 0}},
    )
    assert r.status_code == 409


def test_assistant_act_route_commits_as_assistant(client: TestClient) -> None:
    project = client.post("/api/projects", json={"name": "assistant"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    r = client.post(
        f"/api/projects/{pid}/levels/{lid}/assistant/act",
        json={
            "text": "mets un bureau contre un mur",
            "reply": {
                "intents": [{"kind": "add_furniture", "category": "desk", "near": ["wall"]}],
                "message": "",
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    turn = body["turn"]
    assert turn["committed"] == 1 and len(turn["highlighted"]) == 1
    assert body["project"]["history"][-1]["actor"] == "assistant"
    kinds = [e["type"] for e in body["project"]["levels"][lid]["elements"].values()]
    assert kinds.count("furniture") == 1
    saved = client.get(f"/api/projects/{pid}").json()
    assert saved["history_cursor"] == body["project"]["history_cursor"]
    undone = client.post(f"/api/projects/{pid}/undo").json()
    assert "furniture" not in [e["type"] for e in undone["levels"][lid]["elements"].values()]


def test_assistant_phrase_route(client: TestClient) -> None:
    """P5c : l'assistant unifié (/assistant/say), phrases simples, sans modèle."""
    project = client.post("/api/projects", json={"name": "assistant"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    say = f"/api/projects/{pid}/levels/{lid}/assistant/say"
    r = client.post(say, json={"text": "ajoute un bureau contre le mur"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["turn"]["reader"] == "phrases" and body["remote_model"] is False
    assert body["turn"]["committed"] == 1
    assert any("posé" in p["summary"] for p in body["turn"]["parts"])
    body = client.post(say, json={"text": "bonjour, il fait beau"}).json()
    assert body["turn"]["committed"] == 0
    assert "pas compris" in body["turn"]["parts"][0]["summary"]
    body = client.post(say, json={"text": "propose-moi trois aménagements avec un bureau"}).json()
    layouts = body["turn"]["assistant"]["layouts"]
    assert layouts is not None and body["turn"]["committed"] == 0
    assert 1 <= len(layouts["proposals"]) <= 3
    # L'ancienne route n'existe plus : tout passe par l'assistant unifié.
    assert client.post(
        f"/api/projects/{pid}/levels/{lid}/assistant", json={"text": "x"}
    ).status_code in (404, 405)


def test_concurrent_ops_are_not_lost(tmp_path: Path) -> None:
    """Quatre murs envoyés en même temps (quatre clics rapides) : quatre murs enregistrés."""
    from concurrent.futures import ThreadPoolExecutor

    store = ProjectStore(tmp_path / "data")
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "course"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        segments = [((0, 0), (4, 0)), ((4, 0), (4, 3)), ((4, 3), (0, 3)), ((0, 3), (0, 0))]

        def add(seg: tuple[tuple[int, int], tuple[int, int]]) -> int:
            (x0, y0), (x1, y1) = seg
            r = c.post(
                f"/api/projects/{pid}/levels/{lid}/ops",
                json={
                    "op": "add_wall",
                    "start": {"x": x0, "y": y0},
                    "end": {"x": x1, "y": y1},
                },
            )
            return r.status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(add, segments))
        assert codes == [200, 200, 200, 200]
        saved = c.get(f"/api/projects/{pid}").json()
        kinds = [e["type"] for e in saved["levels"][lid]["elements"].values()]
        assert kinds.count("wall") == 4


def test_assistant_remote_model_needs_consent(tmp_path: Path) -> None:
    """B5 : un modèle distant n'est jamais appelé sans l'accord du projet ; en attendant, les
    phrases simples font le travail et le message le dit ; l'aperçu montre ce qui partirait."""
    import json as _json

    from openplan.adapters.protocols import LlmResponse

    class Remote:
        model_id = "gemini-test"
        remote = True
        supports_images = False
        destination = "generativelanguage.example.invalid"

        def __init__(self) -> None:
            self.requests: list[Any] = []

        def complete(self, request: Any) -> Any:
            self.requests.append(request)
            if request.purpose == "assistant" and len(request.messages) > 1:
                body = {
                    "intents": [{"kind": "add_furniture", "category": "desk", "near": ["wall"]}],
                    "message": "ok",
                }
                return LlmResponse(text=_json.dumps(body), model_id=self.model_id)
            return LlmResponse(text="ok", model_id=self.model_id)

    remote = Remote()
    store = ProjectStore(tmp_path / "data")
    app = create_app(
        store=store, settings=Settings(data_dir=tmp_path / "data"), language_model=remote
    )
    with TestClient(app) as c:
        project = c.post("/api/projects", json={"name": "distant"}).json()
        pid, lid = project["id"], next(iter(project["levels"]))
        _draw_rectangle(c, pid, lid)
        c.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
        preview = c.post(
            f"/api/projects/{pid}/levels/{lid}/assistant/preview",
            json={"text": "ajoute un bureau contre le mur"},
        ).json()
        assert preview["remote"] is True and preview["consented"] is False
        assert "ajoute un bureau contre le mur" in preview["text"]
        assert "Murs" in preview["text"] and any("photos" in n for n in preview["never"])
        say = f"/api/projects/{pid}/levels/{lid}/assistant/say"
        # Case Gemini de l'accueil décochée (par défaut) : rien ne part, phrases simples.
        r = c.post(say, json={"text": "ajoute un bureau contre le mur"}).json()
        assert remote.requests == [], "case décochée : aucun envoi"
        assert r["remote_model"] is True and r["gemini_case"] is False
        assert r["turn"]["reader"] == "phrases" and "Gemini" in r["turn"]["notice"]
        assert r["turn"]["committed"] == 1
        # Case cochée, mais pas d'accord pour ce projet : toujours rien.
        c.put("/api/home/settings", json={"gemini_text": True})
        r = c.post(say, json={"text": "ajoute un fauteuil contre le mur"}).json()
        assert remote.requests == [], "aucun envoi sans accord"
        assert r["turn"]["reader"] == "phrases" and "accord" in r["turn"]["notice"]
        c.patch(
            f"/api/projects/{pid}",
            json={
                "privacy": {
                    **project["privacy"],
                    "allow_remote_models": True,
                    "remote_consent": True,
                }
            },
        )
        r = c.post(say, json={"text": "il me faudrait un endroit pour travailler"}).json()
        assert r["turn"]["reader"] == "model" and r["turn"]["model_id"] == "llm:gemini-test"
        assert all(not req.images for req in remote.requests)
        log = c.get(f"/api/projects/{pid}/network-log").json()
        assert "assistant" in [e["purpose"] for e in log] and all(e["images"] == 0 for e in log)


def test_catalog_previews_and_models(client: TestClient) -> None:
    catalog = client.get("/api/catalog").json()
    first = catalog["assets"][0]
    assert first["room"] and first["preview"] and first["model"]
    png = client.get(f"/api/catalog/{first['id']}/preview")
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    glb = client.get(f"/api/catalog/{first['id']}/model")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert client.get("/api/catalog/licorne/preview").status_code in (404, 409)


def test_catalog_symbols_route(client: TestClient) -> None:
    """F4 : un symbole 2D par famille du catalogue, et le symbole par défaut."""
    body = client.get("/api/catalog/symbols").json()
    families = {a["category"] for a in client.get("/api/catalog").json()["assets"]}
    assert families <= set(body["symbols"])
    assert body["symbols"]["round_table"]["outline"] == "ellipse"
    assert body["generic"]["paths"]


def test_recalibrate_dimensions_routes(client: TestClient) -> None:
    """R3 : aperçu sans écriture, puis application annulable."""
    project = client.post("/api/projects", json={"name": "dimensions"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    base = f"/api/projects/{pid}/levels/{lid}/recalibrate/dimensions"
    current = client.post(f"{base}/preview", json={}).json()
    assert current["before"]["length_m"] > current["before"]["width_m"] > 0
    preview = client.post(f"{base}/preview", json={"length_m": 8.0, "width_m": 4.2}).json()
    assert preview["text"].endswith("→ 8,00 × 4,20 m")
    r = client.post(base, json={"length_m": 8.0, "width_m": 4.2})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["text"].endswith("→ 8,00 × 4,20 m")
    assert r.json()["dimensions"]["after"]["width_m"] == pytest.approx(4.2)
    assert client.post(base, json={}).status_code == 409


def test_architect_view_route(client: TestClient) -> None:
    """D3 : la route propose l'opération ; l'appliquer ajoute le point de vue, annulable."""
    project = client.post("/api/projects", json={"name": "architecte"}).json()
    pid, lid = project["id"], next(iter(project["levels"]))
    _draw_rectangle(client, pid, lid)
    client.post(f"/api/projects/{pid}/levels/{lid}/ops", json={"op": "detect_rooms"})
    op = client.get(f"/api/projects/{pid}/levels/{lid}/cameras/architect").json()
    assert op["op"] == "add_camera" and op["name"] == "Vue d'architecte" and op["focal_px"] > 0
    r = client.post(f"/api/projects/{pid}/levels/{lid}/ops", json=op)
    assert r.status_code == 200, r.text
    level = next(iter(r.json()["project"]["levels"].values()))
    assert any(e.get("name") == "Vue d'architecte" for e in level["elements"].values())


def test_finish_route_needs_an_existing_render(client: TestClient) -> None:
    """D5 : la finition porte sur un rendu de la galerie ; un rendu inconnu est refusé."""
    from uuid import uuid4

    project = client.post("/api/projects", json={"name": "finition"}).json()
    r = client.post(
        f"/api/projects/{project['id']}/renders/{uuid4()}/finish", json={"strength": 0.35}
    )
    assert r.status_code == 409 and "rendu introuvable" in r.json()["detail"]
    r = client.post(
        f"/api/projects/{project['id']}/renders/{uuid4()}/finish", json={"strength": 0.9}
    )
    assert r.status_code == 422
