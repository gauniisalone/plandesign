"""Détection heuristique sur la fixture synthétique rendue par Blender (pose connue).

Seuils (docs/architecture/03 §5) : points de fuite à ±2°, distances aux murs à ±15 %.
Le rendu est commis dans tests/fixtures/rect_room/ ; il se régénère avec
`uv run python -m tests.fixtures.rect_room.render`.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.heuristic.box_detector import HeuristicBoxDetector
from openplan.adapters.protocols import ImageArray
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.floorplan import DetectionError, propose_floorplan
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from openplan.schemas import Camera, Room, Source, Wall
from openplan.schemas.bake import RenderCamera
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room"
IMAGE = FIXTURE / "image.png"
pytestmark = pytest.mark.skipif(not IMAGE.is_file(), reason="fixture image.png absente")


@pytest.fixture(scope="module")
def truth() -> RenderCamera:
    return RenderCamera.model_validate(json.loads((FIXTURE / "camera.json").read_text()))


@pytest.fixture(scope="module")
def image() -> ImageArray:
    from PIL import Image

    with Image.open(IMAGE) as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    return ImageArray(image_id=uuid4(), pixels=pixels)


def angle_diff(a: float, b: float, period: float) -> float:
    d = (a - b) % period
    return min(d, period - d)


def test_camera_pose_within_two_degrees(image: ImageArray, truth: RenderCamera) -> None:
    analysis = HeuristicBoxDetector().analyse(image)
    assert analysis.frame is not None, analysis.warnings
    cam = analysis.camera
    assert angle_diff(cam.pitch, truth.pitch, 2 * math.pi) < math.radians(2.0)
    assert angle_diff(cam.roll, 0.0, 2 * math.pi) < math.radians(2.0)
    assert cam.yaw_hint is not None
    assert angle_diff(cam.yaw_hint, truth.yaw, math.pi / 2) < math.radians(2.0)
    assert cam.focal_px == pytest.approx(truth.focal_px, rel=0.10)
    assert analysis.frame.focal_from_vps
    assert 0.25 <= cam.confidence <= 0.6


def expected_distances(truth: RenderCamera) -> list[float]:
    """Pièce 4 × 3 de la fabrique, murs de 0,15 m : faces intérieures visibles en y = 2,925
    (mur de face) et x = 3,925 (mur latéral), vues depuis la position de la caméra."""
    return sorted([2.925 - truth.position.y, 3.925 - truth.position.x])


def test_floor_edges_give_wall_distances(image: ImageArray, truth: RenderCamera) -> None:
    analysis = HeuristicBoxDetector().analyse(image)
    assert {e.axis for e in analysis.floor_edges} == {"x", "y"}, analysis.warnings
    h = truth.position.z
    distances = sorted(abs(e.offset) * h for e in analysis.floor_edges)
    assert distances == pytest.approx(expected_distances(truth), rel=0.15)
    elements = analysis.elements()
    assert all(e.category == "wall" and e.score <= 0.6 for e in elements)


def test_propose_floorplan_places_estimated_and_inferred_walls(
    image: ImageArray, truth: RenderCamera
) -> None:
    project = new_project("fixture")
    image_id = uuid4()
    from openplan.schemas import ImageRef

    ref = ImageRef(
        id=image_id,
        path="images/x.png",
        sha256="0" * 64,
        width_px=1280,
        height_px=960,
        exif_stripped=True,
    )
    project = project.model_copy(update={"images": {image_id: ref}})
    level = only_level(project)
    analysis = HeuristicBoxDetector().analyse(image)
    after, result = propose_floorplan(
        project, level.id, analysis, image_id=image_id, camera_height_m=truth.position.z
    )
    assert result.step == "floorplan.heuristic_box" and result.patch
    assert validate_invariants(after).valid
    new_level = only_level(after)
    walls = [e for e in new_level.elements.values() if isinstance(e, Wall)]
    assert len(walls) == 4
    estimated = [w for w in walls if w.source is Source.ESTIMATED]
    inferred = [w for w in walls if w.source is Source.INFERRED]
    assert len(estimated) == 2 and len(inferred) == 2
    assert all(w.provenance.image_ids == [image_id] for w in walls)
    camera = next(e for e in new_level.elements.values() if isinstance(e, Camera))
    assert camera.image_id == image_id and camera.position.z == truth.position.z
    # Distance caméra → murs détectés (perpendiculaire à leur axe).
    found = sorted(_distance(new_level, w, camera) for w in estimated)
    assert found == pytest.approx(expected_distances(truth), rel=0.15)
    assert any(isinstance(e, Room) for e in new_level.elements.values()), "pièce inférée"
    assert after.history[-1].actor == "pipeline:floorplan"
    assert any("Hauteur de caméra" in a.text for a in result.assumptions)
    with pytest.raises(DetectionError, match="déjà des murs"):
        propose_floorplan(after, level.id, analysis, image_id=image_id)


def test_detect_and_propose_end_to_end(tmp_path: Path) -> None:
    project = new_project("bout en bout")
    project, ref, _ = import_image(project, tmp_path, IMAGE.read_bytes(), original_name="image.png")
    level = only_level(project)
    run = detect_and_propose(project, tmp_path, level.id, ref.id, tmp_path / "runs")
    assert run.detection.step == "detection.heuristic_box"
    names = {a.name for a in run.detection.artifacts}
    assert names == {"segments.json", "camera.json", "overlay.png"}
    assert all(a.path.is_file() for a in run.detection.artifacts)
    assert run.floorplan.confidence > 0 and len(run.project.history) == 1
    walls = [e for e in only_level(run.project).elements.values() if isinstance(e, Wall)]
    assert len(walls) == 4
    with pytest.raises(DetectionError, match="niveau inconnu"):
        detect_and_propose(project, tmp_path, uuid4(), ref.id, tmp_path / "runs")
    # Une seconde photo (ici la même) sur un niveau déjà muni de murs : confrontation, pas
    # de nouveau rectangle. Elle ne voit qu'un mur par axe : rien ne bouge, la caméra est ajoutée.
    again = detect_and_propose(run.project, tmp_path, level.id, ref.id, tmp_path / "runs")
    assert again.floorplan.step == "floorplan.add_view" and len(again.project.history) == 2
    cameras = [e for e in only_level(again.project).elements.values() if isinstance(e, Camera)]
    assert [c.name for c in cameras] == ["Photo", "Photo 2"]
    assert cameras[1].metadata["observed_extents_m"] == {}
    assert cameras[1].metadata["observed_distances_m"], "distances aux murs vus consignées"
    sources = {
        w.name: w.source for w in only_level(again.project).elements.values() if isinstance(w, Wall)
    }
    assert sources == {w.name: w.source for w in walls}
    assert validate_invariants(again.project).violations == []


def _distance(level: object, wall: Wall, camera: Camera) -> float:
    from openplan.schemas import Level

    assert isinstance(level, Level)
    a = level.vertices[wall.start_vertex].position
    b = level.vertices[wall.end_vertex].position
    dx, dy = b.x - a.x, b.y - a.y
    length = math.hypot(dx, dy)
    return abs((camera.position.x - a.x) * dy - (camera.position.y - a.y) * dx) / length
