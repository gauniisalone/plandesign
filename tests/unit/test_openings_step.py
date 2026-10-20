"""Projection des boîtes porte/fenêtre sur le mur hôte (2.4b) : géométrie exacte, filtres."""

from __future__ import annotations

import math
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
import pytest

from openplan.adapters.protocols import DetectedElement, ImageArray
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.pipeline.openings import (
    ACTOR,
    STEP,
    CameraRays,
    apply_openings,
    run_openings,
)
from openplan.schemas import Camera, ImageRef, Opening, OpeningKind, Project, Source
from openplan.schemas.common import Point3
from openplan.validation.invariants import validate_invariants
from tests.factories import by_name, only_level, rect_room_project

Pose = dict[str, float | tuple[float, float, float]]
SIZE = (1280, 960)
# Face au mur W3 (y = 3, face intérieure y = 2,925) avec 5° de biais et 8° de plongée : tout
# le mur est dans le champ.
WINDOW_POSE: Pose = {
    "position": (2.0, 0.5, 1.4),
    "yaw": math.radians(95),
    "pitch": -0.14,
    "focal": 800.0,
}
# Fenêtre de la fabrique : W3 parcouru de (4,3) vers (0,3), offset 1,2, largeur 1,2, appui 0,9,
# hauteur 1,25.
WINDOW_CORNERS = [(2.8, 2.925, 0.9), (1.6, 2.925, 0.9), (2.8, 2.925, 2.15), (1.6, 2.925, 2.15)]
# Porte de la fabrique sur W1 (y = 0, face y = 0,075), vue de biais (≈ 65°) et de haut.
DOOR_POSE: Pose = {
    "position": (2.0, 2.4, 1.4),
    "yaw": math.atan2(0.075 - 2.4, 0.915 - 2.0),
    "pitch": math.radians(-5),
    "focal": 560.0,
}
DOOR_SIZE = (960, 720)
DOOR_CORNERS = [(0.5, 0.075, 0.0), (1.33, 0.075, 0.0), (1.33, 0.075, 2.04), (0.5, 0.075, 2.04)]


def _camera(pose: Pose, image_id: UUID) -> Camera:
    x, y, z = pose["position"]  # type: ignore[misc]
    return Camera(
        name="Photo",
        position=Point3(x=x, y=y, z=z),
        yaw=float(pose["yaw"]),  # type: ignore[arg-type]
        pitch=float(pose["pitch"]),  # type: ignore[arg-type]
        focal_px=float(pose["focal"]),  # type: ignore[arg-type]
        image_id=image_id,
        source=Source.ESTIMATED,
        confidence=0.5,
    )


def _bbox(
    camera: Camera, size: tuple[int, int], corners: list[tuple[float, float, float]]
) -> tuple[int, int, int, int]:
    """Rectangle image englobant les coins 3D, comme le ferait un détecteur."""
    rays = CameraRays.from_camera(camera, *size)
    us, vs = [], []
    for point in corners:
        d = np.array(point) - rays.origin
        z = float(d @ rays.forward)
        us.append(rays.principal_point[0] + rays.focal_px * float(d @ rays.right) / z)
        vs.append(rays.principal_point[1] - rays.focal_px * float(d @ rays.up) / z)
    box = (round(min(us)), round(min(vs)), round(max(us)), round(max(vs)))
    assert 0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1], box
    return box


def _scene(pose: Pose, size: tuple[int, int], *, drop: str | None) -> tuple[Project, UUID, UUID]:
    """Pièce de la fabrique (sans l'ouverture `drop` si demandé) + caméra de la photo."""
    project = rect_room_project()
    level = only_level(project)
    ref = ImageRef(
        path="images/p.png",
        sha256="0" * 64,
        width_px=size[0],
        height_px=size[1],
        exif_stripped=True,
    )
    camera = _camera(pose, ref.id)
    elements = dict(level.elements)
    if drop is not None:
        del elements[by_name(level, drop, Opening).id]
    elements[camera.id] = camera
    new_level = level.model_copy(update={"elements": elements})
    project = project.model_copy(update={"levels": {level.id: new_level}, "images": {ref.id: ref}})
    return project, level.id, ref.id


def _element(category: str, bbox: tuple[int, int, int, int], score: float = 0.6) -> DetectedElement:
    return DetectedElement(category=category, bbox_px=bbox, score=score, model_id="fake_owl@0")  # type: ignore[arg-type]


def test_window_box_is_projected_on_its_wall() -> None:
    project, level_id, image_id = _scene(WINDOW_POSE, SIZE, drop="window")
    camera = _camera(WINDOW_POSE, image_id)
    box = _bbox(camera, SIZE, WINDOW_CORNERS)
    after, observations, warnings = apply_openings(
        project, level_id, image_id, [_element("window", box)]
    )
    (obs,) = observations
    assert obs.filtered is None and obs.kind == "window" and obs.host_wall_name == "W3", obs
    assert obs.width_m == pytest.approx(1.2, rel=0.03)
    assert obs.offset_m == pytest.approx(1.2, abs=0.05)
    assert obs.sill_m == pytest.approx(0.9, abs=0.04)
    assert obs.height_m == pytest.approx(1.25, abs=0.05)
    assert warnings == [] and after.history[-1].actor == ACTOR
    added = only_level(after).elements[obs.element_id]  # type: ignore[index]
    assert isinstance(added, Opening) and added.source is Source.OBSERVED
    assert added.kind is OpeningKind.WINDOW and added.confidence == 0.6
    assert added.provenance is not None and added.provenance.image_ids == [image_id]
    assert validate_invariants(after).valid


def test_door_seen_obliquely_is_projected_and_snapped_to_the_floor() -> None:
    project, level_id, image_id = _scene(DOOR_POSE, DOOR_SIZE, drop="door")
    camera = _camera(DOOR_POSE, image_id)
    box = _bbox(camera, DOOR_SIZE, DOOR_CORNERS)
    _, observations, _ = apply_openings(project, level_id, image_id, [_element("door", box, 0.72)])
    (obs,) = observations
    assert obs.filtered is None and obs.kind == "door" and obs.host_wall_name == "W1"
    assert obs.sill_m == 0.0
    assert obs.width_m == pytest.approx(0.83, rel=0.05)
    assert obs.height_m == pytest.approx(2.04, rel=0.03)
    assert obs.offset_m == pytest.approx(0.5, abs=0.06)


def test_filters_are_explained() -> None:
    project, level_id, image_id = _scene(WINDOW_POSE, SIZE, drop=None)
    camera = _camera(WINDOW_POSE, image_id)
    window_box = _bbox(camera, SIZE, WINDOW_CORNERS)
    low = _bbox(
        camera, SIZE, [(2.6, 2.925, 0.1), (2.0, 2.925, 0.1), (2.6, 2.925, 0.6), (2.0, 2.925, 0.6)]
    )
    huge = _bbox(
        camera, SIZE, [(3.4, 2.925, 0.5), (0.3, 2.925, 0.5), (3.4, 2.925, 2.0), (0.3, 2.925, 2.0)]
    )
    high_door = _bbox(
        camera, SIZE, [(1.0, 2.925, 1.2), (0.3, 2.925, 1.2), (1.0, 2.925, 2.3), (0.3, 2.925, 2.3)]
    )
    same, observations, warnings = apply_openings(
        project,
        level_id,
        image_id,
        [
            _element("window", window_box),  # déjà présente dans la fabrique
            _element("window", low),  # tableau ou plinthe
            _element("window", huge),  # trop large
            _element("door", high_door),  # une « porte » qui ne part pas du sol : fenêtre
            _element("furniture", (0, 0, 10, 10)),  # ignorée
        ],
    )
    assert len(observations) == 4
    by_reason = {o.filtered: o for o in observations}
    kept = by_reason[None]
    assert (
        kept.category == "door"
        and kept.kind == "window"
        and kept.sill_m == pytest.approx(1.2, abs=0.05)
    )
    assert "recouvre" in by_reason["deja_present"].reason
    assert "tableau" in by_reason["trop_bas_pour_une_fenetre"].reason
    wide = by_reason["largeur_implausible"]
    assert wide.width_m is not None and wide.width_m > 3.0 and "3,0 m" in wide.reason
    assert len(warnings) == 3 and all("écartée" in w for w in warnings)
    added = [
        e
        for e in only_level(same).elements.values()
        if isinstance(e, Opening) and e.provenance is not None and e.provenance.step == STEP
    ]
    assert len(added) == 1 and added[0].kind is OpeningKind.WINDOW
    assert added[0].source is Source.OBSERVED
    assert validate_invariants(same).valid


def test_box_above_the_ceiling_is_filtered() -> None:
    up: Pose = {**WINDOW_POSE, "pitch": 0.4}
    project, level_id, image_id = _scene(up, SIZE, drop=None)
    _, observations, _ = apply_openings(
        project, level_id, image_id, [_element("window", (600, 0, 800, 100))]
    )
    (obs,) = observations
    assert obs.filtered == "depasse_plafond" and "plafond à 2,50 m" in obs.reason


def test_requires_the_camera_of_the_photo() -> None:
    project, level_id, image_id = _scene(WINDOW_POSE, SIZE, drop=None)
    level = only_level(project)
    without_camera = level.model_copy(
        update={"elements": {k: v for k, v in level.elements.items() if not isinstance(v, Camera)}}
    )
    orphan = project.model_copy(update={"levels": {level.id: without_camera}})
    with pytest.raises(DetectionError, match="caméra de cette photo"):
        apply_openings(orphan, level_id, image_id, [])
    with pytest.raises(ImageNotFoundError):
        apply_openings(project, level_id, uuid4(), [])
    with pytest.raises(DetectionError, match="niveau inconnu"):
        apply_openings(project, uuid4(), image_id, [])


class FakeDetector:
    model_id = "fake_owl@0"

    def __init__(self, boxes: list[DetectedElement]) -> None:
        self.boxes = boxes

    def detect(self, image: ImageArray) -> list[DetectedElement]:
        del image
        return self.boxes


FIXTURE_IMAGE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


@pytest.mark.skipif(not FIXTURE_IMAGE.is_file(), reason="fixture absente")
def test_run_openings_writes_overlay_and_json(tmp_path: Path) -> None:
    from openplan.pipeline.ingestion import import_image

    project = rect_room_project()
    project, ref, _ = import_image(
        project, tmp_path, FIXTURE_IMAGE.read_bytes(), original_name="i.png"
    )
    level = only_level(project)
    window = by_name(level, "window", Opening)
    camera = _camera(WINDOW_POSE, ref.id)
    elements = {k: v for k, v in level.elements.items() if k != window.id}
    elements[camera.id] = camera
    project = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    box = _bbox(camera, (ref.width_px, ref.height_px), WINDOW_CORNERS)
    run = run_openings(
        project,
        tmp_path,
        level.id,
        ref.id,
        tmp_path / "runs",
        detector=FakeDetector([_element("window", box)]),
    )
    assert run.result.step == STEP and run.result.patch and run.result.model_ids == ["fake_owl@0"]
    assert [o.kind for o in run.observations] == ["window"]
    assert {a.name for a in run.result.artifacts} == {"openings_overlay.png"}
    assert (tmp_path / "runs" / str(run.run_id) / "openings" / "openings.json").is_file()
    assert run.project.history[-1].run_id == run.run_id
    empty = run_openings(
        project, tmp_path, level.id, ref.id, tmp_path / "runs", detector=FakeDetector([])
    )
    assert not empty.result.patch and any("Aucune porte" in w for w in empty.result.warnings)
    assert empty.project == project
