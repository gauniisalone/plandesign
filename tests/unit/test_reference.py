"""KAN-66 : mesure de référence sur une photo. Sur la scène de contrôle KAN-27 (profondeur
apprise enregistrée, sans GPU), une référence exacte réduit l'erreur de largeur au moins de
moitié et ramène le faîtage dans ±20 % ; refus motivés ; routes PUT (remplace) et DELETE,
annulables ; migration 0.15.0 → 0.16.0."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from uuid import UUID

import numpy as np
import pytest
from fastapi.testclient import TestClient

from openplan.api.app import create_app
from openplan.api.settings import Settings
from openplan.pipeline.attic import analyse_attic, rescale
from openplan.pipeline.reference import (
    ReferenceRefusedError,
    camera_basis,
    check_length,
    depth_paths,
    load_depth,
    reference_factor,
    segment_distance,
    with_basis,
    write_depth,
)
from openplan.scene.migrate import load_project
from openplan.scene.store import ProjectStore
from openplan.schemas.scene import ImageRef, Project
from tests.control_attic import CAMERA_HEIGHT, GABLE_DISTANCE, LENGTH, RIDGE, WIDTH, control_attic
from tests.factories import only_level

DEPTH = Path(__file__).parents[1] / "fixtures" / "control_attic_depth.npz"


def _fixture() -> tuple[np.ndarray, float, tuple[float, float]]:
    data = np.load(DEPTH)
    depth = data["depth"].astype(np.float32)
    return depth, float(data["focal_px"]), (depth.shape[1] / 2.0, depth.shape[0] / 2.0)


def _pixel(y: float, z: float) -> tuple[float, float]:
    """Pixel d'un point (x = LENGTH, y, z) du pignon du fond, vu de la caméra « Photo »
    (1,5 ; 2,5 ; 1,45), regard selon +x, sans tangage."""
    _depth, f, (cx, cy) = _fixture()
    return cx + f * (WIDTH / 2.0 - y) / GABLE_DISTANCE, cy + f * (
        CAMERA_HEIGHT - z
    ) / GABLE_DISTANCE


SEGMENTS = (
    ("pignon au sol, d'un coin à l'autre", (0.0, 0.02), (5.0, 0.02)),
    ("pignon à 0,30 m", (0.5, 0.30), (4.5, 0.30)),
    ("pignon à 0,60 m", (1.0, 0.60), (4.0, 0.60)),
)


@pytest.mark.parametrize(("what", "a", "b"), SEGMENTS, ids=[s[0] for s in SEGMENTS])
def test_mh3_reference_halves_the_width_error_and_brings_the_ridge_within_20(
    what: str, a: tuple[float, float], b: tuple[float, float]
) -> None:
    """MH3 (voie a, décision PO du 20 oct. 2026). Base mesurée sur la profondeur enregistrée :

    | échelle                                  | largeur (vérité 5,00 m) | faîtage (3,54 m) |
    | hauteur de prise de vue saisie 1,45 m    | 10,42 m (+108 %)        | 5,69 m (+61 %)   |
    | hauteur supposée 1,50 m (par défaut)     | 10,78 m (+116 %)        | 5,89 m (+66 %)   |
    | référence : coins du pignon au sol, 5 m  |  7,50 m (+50 %)         | 4,09 m (+16 %)   |
    | référence : segments de 4 m ou 3 m       |  7,31 m (+46 %)         | 3,99 m (+13 %)   |

    L'erreur de largeur restante venait surtout de la largeur prise au sol : les jambettes
    n'étaient pas extraites (KAN-27), la « largeur » était celle où les pentes rencontrent le
    sol, 5,00 + 2 × 1,00 / tan 45,5° = 6,97 m (écart de +5 à +8 % par rapport à elle). Depuis
    KAN-46, les jambettes sont déduites du bord du sol : largeur entre jambettes 6,75 m (+35 %)
    à la hauteur saisie, 4,86 m (−3 %) avec la référence au sol. Le faîtage (+13/16 %) relève
    de KAN-72. Ici : erreur de largeur au moins divisée par deux, faîtage dans ±20 %."""
    depth, f, principal = _fixture()
    raw, _planes, _cls, _px, _warn = analyse_attic(depth, f, principal)
    today = rescale(raw, CAMERA_HEIGHT)
    assert today.width and today.ridge_height
    p1, p2 = _pixel(*a), _pixel(*b)
    truth = math.hypot(b[0] - a[0], b[1] - a[1])
    relative = reference_factor(
        segment_distance(depth, f, principal, p1, p2), truth, today.scale_factor
    )
    width, ridge = today.width.value * relative, today.ridge_height.value * relative
    before_error = abs(today.width.value - WIDTH) / WIDTH
    assert before_error > 0.2, "base : largeur fausse de plus de 20 % à la hauteur saisie"
    assert abs(width - WIDTH) / WIDTH <= before_error / 2, what
    assert abs(ridge - RIDGE) / RIDGE <= 0.20, what


def test_refusals_say_why() -> None:
    depth, f, principal = _fixture()
    with pytest.raises(ReferenceRefusedError, match="entre 5 cm et 30 m"):
        check_length(0.01)
    with pytest.raises(ReferenceRefusedError, match="entre 5 cm et 30 m"):
        check_length(45.0)
    # D'une pente à l'autre, par le vide : deux surfaces.
    with pytest.raises(ReferenceRefusedError, match="même surface"):
        segment_distance(depth, f, principal, (20.0, 40.0), (300.0, 40.0))
    with pytest.raises(ReferenceRefusedError, match="hors de la photo"):
        segment_distance(depth, f, principal, (-5.0, 10.0), (100.0, 10.0))
    with pytest.raises(ReferenceRefusedError, match=r"×4,00"):
        reference_factor(1.0, 4.0, 1.0)


# ------------------------------------------------------------------ routes
def _project_with_photo(store: ProjectStore) -> tuple[Project, UUID, float]:
    """La scène de contrôle comme plan « estimé » depuis une photo 320 × 240, à l'échelle de la
    hauteur de prise de vue (facteur du modèle), profondeur gardée."""
    depth, f, principal = _fixture()
    raw, *_ = analyse_attic(depth, f, principal)
    factor = rescale(raw, CAMERA_HEIGHT).scale_factor
    project = control_attic()
    ref = ImageRef(
        path="images/photo.jpg", sha256="0" * 64, width_px=320, height_px=240, exif_stripped=True
    )
    project = project.model_copy(update={"images": {ref.id: ref}})
    level_id = only_level(project).id
    project = with_basis(
        project, level_id, camera_basis(ref.id, factor, CAMERA_HEIGHT, entered=True)
    )
    store.save(project)
    write_depth(store.project_dir(project.id), ref.id, depth, f, principal, (320, 240))
    return project, ref.id, factor


def _xs(project: dict[str, Any]) -> float:
    level = next(iter(project["levels"].values()))
    ys = [float(v["position"]["y"]) for v in level["vertices"].values()]
    return max(ys) - min(ys)


def test_put_replaces_delete_returns_undo_restores(tmp_path: Path) -> None:  # noqa: PLR0915
    store = ProjectStore(tmp_path / "data")
    project, image_id, _ = _project_with_photo(store)
    pid = str(project.id)
    url = f"/api/projects/{pid}/images/{image_id}/reference"
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        width0 = _xs(project.model_dump(mode="json"))
        p1, p2 = _pixel(0.0, 0.02), _pixel(5.0, 0.02)
        body = {"p1": list(p1), "p2": list(p2), "length_m": 5.0, "label": "pignon"}
        r = client.put(url, json=body)
        assert r.status_code == 200, r.text
        after = r.json()
        level = next(iter(after["levels"].values()))
        basis = level["scale_basis"]
        assert basis["source"] == "reference"
        assert basis["text"] == "Échelle calée sur votre mesure de 5,00 m (pignon)"
        assert basis["previous"]["source"] == "camera_height"
        assert basis["before"]["width_m"] == pytest.approx(min(width0, LENGTH), rel=0.05)
        segments = after["images"][str(image_id)]["reference_segments"]
        assert len(segments) == 1 and segments[0]["length_m"] == 5.0
        assert segments[0]["author"] == "local"
        assert basis["segment_id"] == segments[0]["id"]
        relative = _xs(after) / width0
        assert 0.6 < relative < 0.8  # le plan rétrécit d'environ 0,72
        assert after["history"][-1]["label"] == "Mesure de référence : 5,00 m (pignon)"
        assert segments[0]["id"] in after["measurements"]
        assert after["calibrations"][-1]["measurement_ids"] == [segments[0]["id"]]
        n_measurements, n_calibrations = len(after["measurements"]), len(after["calibrations"])
        # Une seule mesure par photo : la nouvelle remplace, l'échelle part de la caméra.
        q1, q2 = _pixel(1.0, 0.60), _pixel(4.0, 0.60)
        r2 = client.put(url, json={"p1": list(q1), "p2": list(q2), "length_m": 3.0})
        assert r2.status_code == 200, r2.text
        again = r2.json()
        segments2 = again["images"][str(image_id)]["reference_segments"]
        assert len(segments2) == 1 and segments2[0]["id"] != segments[0]["id"]
        assert segments2[0]["id"] in again["measurements"]
        assert segments[0]["id"] not in again["measurements"]
        assert len(again["measurements"]) == n_measurements
        assert len(again["calibrations"]) == n_calibrations
        basis2 = next(iter(again["levels"].values()))["scale_basis"]
        assert basis2["previous"]["source"] == "camera_height"
        assert basis2["text"] == "Échelle calée sur votre mesure de 3,00 m"
        # Refus : 422 avec la raison, rien d'écrit.
        bad = client.put(url, json={"p1": [20, 40], "p2": [300, 40], "length_m": 5.0})
        assert bad.status_code == 422 and "même surface" in bad.json()["detail"]
        assert client.put(url, json={**body, "length_m": 50}).status_code == 422
        out = client.put(url, json={**body, "p1": [400, 10]})
        assert out.status_code == 422 and "hors de la photo" in out.json()["detail"]
        # Retirer : retour à l'échelle de la hauteur de prise de vue.
        gone = client.delete(f"{url}/{segments2[0]['id']}")
        assert gone.status_code == 200, gone.text
        back = gone.json()
        assert _xs(back) == pytest.approx(width0, rel=1e-6)
        assert next(iter(back["levels"].values()))["scale_basis"]["source"] == "camera_height"
        assert back["images"][str(image_id)]["reference_segments"] == []
        assert segments2[0]["id"] not in back["measurements"]
        assert len(back["calibrations"]) == n_calibrations - 1
        assert client.delete(f"{url}/{segments2[0]['id']}").status_code == 404
        # Annuler le retrait : la mesure revient avec son échelle.
        undone = client.post(f"/api/projects/{pid}/undo")
        assert undone.status_code == 200, undone.text
        reread = client.get(f"/api/projects/{pid}").json()
        assert len(reread["images"][str(image_id)]["reference_segments"]) == 1
        assert next(iter(reread["levels"].values()))["scale_basis"]["source"] == "reference"


def test_confidence_follows_the_measurement(tmp_path: Path) -> None:
    """KAN-31 + KAN-66 : la confiance est recalculée par le serveur et renvoyée dans le
    projet. Hauteur saisie : « approximatif » ; une mesure : « à vérifier », « échelle calée
    sur votre mesure » ; la retirer : le niveau d'avant, sa raison d'avant."""
    store = ProjectStore(tmp_path / "data")
    project, image_id, _ = _project_with_photo(store)
    before = only_level(project).scale_basis
    assert before is not None and before.confidence is not None
    assert before.confidence.level == "approximatif"
    url = f"/api/projects/{project.id}/images/{image_id}/reference"
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        p1, p2 = _pixel(0.0, 0.02), _pixel(5.0, 0.02)
        put = client.put(url, json={"p1": list(p1), "p2": list(p2), "length_m": 5.0})
        assert put.status_code == 200, put.text
        confidence = next(iter(put.json()["levels"].values()))["scale_basis"]["confidence"]
        assert confidence["level"] == "a_verifier" and confidence["label"] == "à vérifier"
        assert "échelle calée sur votre mesure" in confidence["reason"]
        segment = put.json()["images"][str(image_id)]["reference_segments"][0]["id"]
        gone = client.delete(f"{url}/{segment}")
        assert gone.status_code == 200, gone.text
        back = next(iter(gone.json()["levels"].values()))["scale_basis"]["confidence"]
        assert back == before.confidence.model_dump(mode="json")


def test_a_photo_without_kept_depth_asks_to_reestimate(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "data")
    project, image_id, _ = _project_with_photo(store)
    for path in depth_paths(store.project_dir(project.id), image_id):
        path.unlink()
    app = create_app(store=store, settings=Settings(data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        r = client.put(
            f"/api/projects/{project.id}/images/{image_id}/reference",
            json={"p1": [10, 10], "p2": [100, 10], "length_m": 2.04},
        )
        assert r.status_code == 422 and "réestimez la photo" in r.json()["detail"]


def test_depth_is_kept_small_and_in_photo_pixels(tmp_path: Path) -> None:
    """Profondeur 1280 × 960 gardée en 640 × 480 (float16) ; les points se donnent en pixels de
    la photo d'origine et tombent au même endroit de la grille réduite."""
    big = np.tile(np.linspace(1.0, 5.0, 1280, dtype=np.float32), (960, 1))
    from uuid import uuid4

    image_id = uuid4()
    write_depth(tmp_path, image_id, big, 1024.0, (640.0, 480.0), (1280, 960))
    stored = load_depth(tmp_path, image_id)
    assert stored is not None and stored.depth.shape == (480, 640)
    assert stored.focal_px == 512.0 and stored.principal == (320.0, 240.0)
    u, v = stored.to_grid((1000.0, 500.0))
    assert (u, v) == (500.0, 250.0)
    npy, meta = depth_paths(tmp_path, image_id)
    assert np.load(npy).dtype == np.float16
    assert json.loads(meta.read_text(encoding="utf-8"))["image_size"] == [1280, 960]


def test_migration_from_0_15_0_has_no_reference_and_no_basis() -> None:
    data = control_attic().model_dump(mode="json")
    data["schema_version"] = "0.15.0"
    for level in data["levels"].values():
        level.pop("scale_basis", None)
    loaded = load_project(data)
    assert loaded.schema_version == "0.16.0"
    assert all(level.scale_basis is None for level in loaded.levels.values())
