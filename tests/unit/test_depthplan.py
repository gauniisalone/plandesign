"""Plan depuis la profondeur (2.3a) : pièce rectangulaire et pièce en L, profondeur exacte."""

from __future__ import annotations

import math
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from openplan.adapters.protocols import DepthMap, ImageArray
from openplan.pipeline.depthplan import (
    STEP,
    WallObservation,
    observe_room,
    propose_floorplan_from_depth,
    room_polygon,
    run_depthplan,
)
from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError
from openplan.scene.factory import new_project
from openplan.schemas import Camera, ImageRef, Room, Source, Wall
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level
from tests.synthetic_depth import (
    Face,
    SyntheticCamera,
    box_faces,
    render_depth,
    render_depth_union,
)

H = 1.4
CAMERA = SyntheticCamera(
    position=(1.0, 0.5, H),
    yaw=math.radians(45),
    pitch=math.radians(-12),
    focal_px=500.0,
    width_px=960,
    height_px=720,
)
# Pièce en L : cellule A [0,4]×[0,3] + cellule B [2,6]×[0,6] (renfoncement : y > 3 seulement
# pour x > 2). Vue depuis (1 ; 0,5) vers le coin opposé.
CELL_A = box_faces(4.0, 3.0, 2.5)
CELL_B = [
    Face((0.0, 0.0, -1.0), 0.0),
    Face((0.0, 0.0, 1.0), 2.5),
    Face((-1.0, 0.0, 0.0), -2.0),
    Face((1.0, 0.0, 0.0), 6.0),
    Face((0.0, -1.0, 0.0), 0.0),
    Face((0.0, 1.0, 0.0), 6.0),
]
L_TRUTH: Polygon = box(0, 0, 4, 3).union(box(2, 0, 6, 6))


def _project_with_image() -> tuple[object, ImageRef]:
    project = new_project("profondeur")
    ref = ImageRef(
        path="images/p.png", sha256="0" * 64, width_px=960, height_px=720, exif_stripped=True
    )
    return project.model_copy(update={"images": {ref.id: ref}}), ref


def _polygon(level: object) -> Polygon:
    from openplan.schemas import Level

    assert isinstance(level, Level)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    walls = {w.id: w for w in level.elements.values() if isinstance(w, Wall)}
    points = []
    for wid in room.boundary_walls:
        points.append(level.vertices[walls[wid].start_vertex].position)
    return Polygon([(p.x, p.y) for p in points])


def _iou(a: Polygon, b: Polygon) -> float:
    return a.intersection(b).area / a.union(b).area


def test_observe_rectangular_room_finds_floor_and_visible_walls() -> None:
    depth = render_depth(box_faces(4.0, 3.0, 2.5), CAMERA, noise=0.01, seed=1)
    obs, planes = observe_room(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert obs is not None and len(planes) >= 3
    assert obs.camera_height_m == pytest.approx(H, rel=0.03)
    # Depuis (1 ; 0,5) vers le coin (4 ; 3) : mur de face y = 3 (à 2,5 m) et mur droit x = 4
    # (à 3 m) ; l'ordre des axes dépend du plus grand mur, on compare les distances absolues.
    distances = sorted(abs(w.offset) for w in obs.walls)
    assert distances[:2] == pytest.approx([2.5, 3.0], rel=0.03)
    assert all(w.count > 100 for w in obs.walls)


def test_room_polygon_handles_a_notch() -> None:
    assumptions: list = []
    walls = [
        WallObservation("y", 2.5, (-1.0, 1.0), 500, 0.01),  # mur de face proche, à gauche
        WallObservation("y", 5.5, (1.0, 5.0), 800, 0.01),  # mur de face lointain, à droite
        WallObservation("x", 5.0, (0.0, 5.5), 600, 0.01),  # mur droit
        WallObservation("x", -1.0, (0.0, 2.5), 300, 0.01),  # mur gauche
    ]
    polygon, kept = room_polygon(walls, assumptions)
    assert len(kept) == 4 and any("Renfoncement" in a.text for a in assumptions)
    expected = box(-1.0, -0.5, 5.0, 5.5).difference(box(-1.0, 2.5, 1.0, 5.5))
    assert _iou(polygon, expected) > 0.99
    # Deux détections du même mur (étendues qui se recouvrent) : une seule gardée.
    twice = [*walls, WallObservation("y", 2.6, (-0.8, 0.9), 100, 0.02)]
    _, kept = room_polygon(twice, [])
    assert len(kept) == 4


def test_propose_l_room_from_exact_depth() -> None:
    depth = render_depth_union([CELL_A, CELL_B], CAMERA, noise=0.01, seed=2)
    obs, _ = observe_room(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert obs is not None
    project, ref = _project_with_image()
    level_id = only_level(project).id  # type: ignore[arg-type]
    after, result = propose_floorplan_from_depth(
        project,  # type: ignore[arg-type]
        level_id,
        obs,
        image_id=ref.id,
        camera_height_m=H,
        yaw=CAMERA.yaw,
        pitch=CAMERA.pitch,
        focal_px=CAMERA.focal_px,
    )
    assert result.step == STEP and result.patch and after.history[-1].actor == "pipeline:floorplan"
    level = only_level(after)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    assert len(walls) == 6, "pièce en L : six murs"
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    # Vérité exprimée dans le repère du plan : axes du plan (repère caméra) → monde, puis
    # coordonnées relatives à la caméra, décalées comme dans la scène.
    right, cam_up, forward = CAMERA.basis()
    to_world = np.vstack([right, -cam_up, forward])
    x_dir, y_dir = obs.axes[0] @ to_world, obs.axes[1] @ to_world
    origin = np.asarray(CAMERA.position)
    truth = Polygon(
        [
            (
                float((np.array([x, y, H]) - origin) @ x_dir) + camera.position.x,
                float((np.array([x, y, H]) - origin) @ y_dir) + camera.position.y,
            )
            for x, y in L_TRUTH.exterior.coords
        ]
    )
    iou = _iou(_polygon(level), truth)
    assert iou >= 0.7, f"IoU {iou:.2f}"
    # Vus : le mur de face proche (y = 3), le mur lointain (y = 6) et le mur droit (x = 6).
    # Supposés : le mur derrière la caméra, le mur gauche (à peine dans le champ) et le petit
    # retour du renfoncement (x = 2 entre y = 3 et 6), dont la face visible est tournée ailleurs.
    assert sum(w.source is Source.ESTIMATED for w in walls) == 3
    assert sum(w.source is Source.INFERRED for w in walls) == 3
    assert validate_invariants(after).valid
    assert any("Renfoncement" in a.text for a in result.assumptions)
    with pytest.raises(DetectionError, match="déjà des murs"):
        propose_floorplan_from_depth(after, level_id, obs, image_id=ref.id)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


class BoxDepth:
    """Estimateur simulé : profondeur exacte d'une pièce 4 × 3 depuis la pose de la fixture,
    rendue à la taille de l'image (la focale vient des points de fuite de l'image)."""

    model_id = "fake_box_depth@0"

    def predict(self, image: ImageArray) -> DepthMap:
        h, w = image.height, image.width
        camera = SyntheticCamera(
            position=(0.7, 0.6, 1.4),
            yaw=0.8727,
            pitch=-0.2094,
            focal_px=900.0,
            width_px=w,
            height_px=h,
        )
        depth = render_depth(box_faces(4.0 - 0.15, 3.0 - 0.15, 2.5), camera)
        return DepthMap(depth=depth, metric=True, model_id=self.model_id)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_run_depthplan_on_the_fixture_image(tmp_path: Path) -> None:
    from openplan.pipeline.ingestion import import_image

    project = new_project("plan profondeur")
    project, ref, _ = import_image(project, tmp_path, FIXTURE.read_bytes(), original_name="i.png")
    level_id = only_level(project).id
    run = run_depthplan(
        project,
        tmp_path,
        level_id,
        ref.id,
        tmp_path / "runs",
        estimator=BoxDepth(),
        camera_height_m=1.4,
    )
    assert run.result.step == STEP and run.walls_seen >= 2
    assert run.camera_height_m == pytest.approx(1.4, rel=0.05)
    walls = [e for e in only_level(run.project).elements.values() if isinstance(e, Wall)]
    assert len(walls) == 4 and sum(w.source is Source.ESTIMATED for w in walls) >= 2
    assert (tmp_path / "runs" / str(run.run_id) / "depthplan" / "walls.json").is_file()
    with pytest.raises(ImageNotFoundError):
        run_depthplan(project, tmp_path, level_id, uuid4(), tmp_path / "runs", estimator=BoxDepth())


def test_propose_without_walls_or_image() -> None:
    project, ref = _project_with_image()
    level_id = only_level(project).id  # type: ignore[arg-type]
    obs, _ = observe_room(
        render_depth(box_faces(4, 3, 2.5), CAMERA), CAMERA.focal_px, CAMERA.principal_point
    )
    assert obs is not None
    empty = type(obs)(camera_height_m=obs.camera_height_m, axes=obs.axes)
    same, result = propose_floorplan_from_depth(
        project,  # type: ignore[arg-type]
        level_id,
        empty,
        image_id=ref.id,
    )
    assert same == project and result.confidence == 0
    from uuid import uuid4

    with pytest.raises(DetectionError, match="image inconnue"):
        propose_floorplan_from_depth(
            project,  # type: ignore[arg-type]
            level_id,
            obs,
            image_id=uuid4(),
        )
