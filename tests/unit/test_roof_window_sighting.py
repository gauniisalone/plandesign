"""C2 : fenêtres de toit vues sur une photo de combles, placées sur leur pente."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.protocols import DetectedElement, ImageArray, RoofWindowDetector
from openplan.pipeline.attic import analyse_attic, rescale
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.roof_windows import RoofWindowSighting, locate_roof_windows
from openplan.scene.factory import new_project
from openplan.schemas import RoofPlane
from openplan.schemas.elements import RoofWindow
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level
from tests.synthetic_depth import SyntheticCamera, attic_faces, render_depth

CAMERA = SyntheticCamera(
    position=(1.5, 0.3, 1.45), yaw=0.0, pitch=0.0, focal_px=420.0, width_px=640, height_px=480
)
RIDGE, EAVE, HALF = 3.2, 1.0, 2.5
SLOPE = (RIDGE - EAVE) / HALF
# Velux de 0,78 m sur la pente de gauche (y > 0) : x de 4,0 à 4,78, y de 1,9 (bas) à 1,3.
VELUX_X = (4.0, 4.78)
VELUX_Y = (1.9, 1.3)


def _z(y: float) -> float:
    return RIDGE - SLOPE * y


def project_box(camera: SyntheticCamera) -> tuple[int, int, int, int]:
    right, up, forward = camera.basis()
    origin = np.asarray(camera.position)
    cx, cy = camera.principal_point
    us, vs = [], []
    for x in VELUX_X:
        for y in VELUX_Y:
            d = np.array([x, y, _z(y)]) - origin
            depth = float(d @ forward)
            us.append(cx + camera.focal_px * float(d @ right) / depth)
            vs.append(cy - camera.focal_px * float(d @ up) / depth)
    return int(min(us)), int(min(vs)), int(max(us)) + 1, int(max(vs)) + 1


def _box(bbox: tuple[int, int, int, int], score: float = 0.4) -> DetectedElement:
    return DetectedElement(
        category="roof_window", bbox_px=bbox, score=score, label="a roof window", model_id="t@0"
    )


def _locate(boxes: list[DetectedElement]) -> tuple[list[RoofWindowSighting], list[str]]:
    depth = render_depth(attic_faces(), CAMERA, noise=0.005)
    _est, planes, cls, pixels, _w = analyse_attic(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert cls is not None
    return locate_roof_windows(
        boxes,
        planes,
        cls.kinds,
        cls.axes,
        cls.floor.plane.distance,
        CAMERA.focal_px,
        CAMERA.principal_point,
        pixels,
        depth.shape,
    )


def test_a_skylight_box_lands_on_its_slope() -> None:
    sightings, notes = _locate([_box(project_box(CAMERA))])
    assert len(sightings) == 1, notes
    seen = sightings[0]
    assert seen.forward_m == pytest.approx(sum(VELUX_X) / 2 - CAMERA.position[0], abs=0.15)
    assert seen.lateral_m > 0, "pente de gauche"
    assert seen.width_m == pytest.approx(0.78, abs=0.25)
    assert seen.z_bottom_m == pytest.approx(_z(VELUX_Y[0]), abs=0.12)
    assert seen.z_top_m == pytest.approx(_z(VELUX_Y[1]), abs=0.12)
    assert seen.scaled(2.0).forward_m == pytest.approx(2 * seen.forward_m)


def test_boxes_not_on_a_slope_or_unsure_are_left_out() -> None:
    right, up, forward = CAMERA.basis()
    cx, cy = CAMERA.principal_point
    # Fenêtre de pignon : au fond (x = 6), à hauteur d'yeux, au milieu.
    gable = []
    for y, z in ((-0.3, 1.2), (0.3, 1.8)):
        d = np.array([6.0, y, z]) - np.asarray(CAMERA.position)
        depth = float(d @ forward)
        gable.append((cx + 420.0 * float(d @ right) / depth, cy - 420.0 * float(d @ up) / depth))
    (u0, v0), (u1, v1) = gable
    bbox = (int(min(u0, u1)), int(min(v0, v1)), int(max(u0, u1)), int(max(v0, v1)))
    sightings, notes = _locate([_box(bbox, 0.5), _box(project_box(CAMERA), 0.2)])
    assert sightings == []
    assert any("pente" in n for n in notes), notes
    assert not any("0.20" in n for n in notes), "une boîte sous le seuil n'est pas commentée"


def _geometry(sightings: list[RoofWindowSighting]) -> AtticGeometry:
    from openplan.pipeline.attic import Estimate

    def e(v: float) -> Estimate:
        return Estimate(value=v, sigma=0.1)

    return AtticGeometry(
        length=e(6.0),
        width=e(5.0),
        ridge_height=e(RIDGE),
        eave_height=e(EAVE),
        camera_height=e(1.45),
        gable_distance=e(4.5),
        roof_windows=sightings,
    )


def test_apply_attic_places_the_seen_skylight() -> None:
    sightings, _ = _locate([_box(project_box(CAMERA))])
    project = new_project("c2")
    level = only_level(project)
    after, step = apply_attic(project, level.id, _geometry(sightings))
    elements = only_level(after).elements
    windows = [e for e in elements.values() if isinstance(e, RoofWindow)]
    assert len(windows) == 1 and validate_invariants(after).valid
    window = windows[0]
    roof = elements[window.roof]
    assert isinstance(roof, RoofPlane) and roof.name == "Versant haut"
    assert window.source == "estimated" and window.confidence == pytest.approx(0.4)
    assert window.provenance is not None and window.provenance.model == "t@0"
    # Versant haut : égout de (6, 5) à (0, 5) ; velux de x = 4,0 à 4,78 → offset 1,22.
    assert window.offset == pytest.approx(6.0 - VELUX_X[1], abs=0.25)
    pitch = math.atan(SLOPE)
    assert window.distance == pytest.approx((HALF - VELUX_Y[0]) / math.cos(pitch), abs=0.25)
    assert any("fenêtre(s) de toit" in a.text for a in step.assumptions)


def test_rescale_carries_the_sightings() -> None:
    from openplan.pipeline.attic import AtticEstimate, Estimate

    seen = RoofWindowSighting(
        forward_m=2.0,
        lateral_m=1.0,
        width_m=0.8,
        z_bottom_m=1.5,
        z_top_m=2.0,
        score=0.4,
        bbox_px=(0, 0, 10, 10),
        model_id="t@0",
    )
    standard = seen.model_copy(update={"standard_size": True})
    est = AtticEstimate(camera_height=Estimate(value=1.0, sigma=0.1), roof_windows=[seen, standard])
    doubled = rescale(est, 2.0)
    assert doubled.roof_windows[0].z_top_m == pytest.approx(4.0)
    assert doubled.roof_windows[0].width_m == pytest.approx(1.6)
    kept = doubled.roof_windows[1]
    assert kept.width_m == pytest.approx(0.8), "une taille standard ne suit pas l'échelle"
    assert (kept.z_bottom_m, kept.z_top_m) == pytest.approx((3.25, 3.75))


def test_standard_size_when_the_box_gives_no_credible_frame() -> None:
    project = new_project("c2")
    level = only_level(project)
    seen = RoofWindowSighting(
        forward_m=2.9,
        lateral_m=1.0,
        width_m=0.78,
        z_bottom_m=1.8,
        z_top_m=2.4,
        score=0.5,
        bbox_px=(0, 0, 10, 10),
        model_id="t@0",
        standard_size=True,
    )
    after, _ = apply_attic(project, level.id, _geometry([seen]))
    window = next(e for e in only_level(after).elements.values() if isinstance(e, RoofWindow))
    assert (window.width, window.length) == pytest.approx((0.78, 0.98))
    pitch = math.atan(SLOPE)
    middle = (2.1 - EAVE) / math.sin(pitch)
    assert window.distance == pytest.approx(middle - 0.49, abs=1e-6)


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "rect_room" / "image.png"


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture absente")
def test_autoplan_poses_the_skylight_without_any_gesture(tmp_path: Path) -> None:
    from openplan.pipeline.autoplan import run_autoplan
    from openplan.pipeline.ingestion import import_image
    from tests.unit.test_autoplan import AtticDepth

    project, ref, _ = import_image(
        new_project("auto"), tmp_path, FIXTURE.read_bytes(), original_name="p.png"
    )
    focal = 0.66 * ref.width_px

    class Detector:
        model_id = "fake_owl@0"

        def detect(self, image: ImageArray) -> list[DetectedElement]:
            return []

        def detect_roof_windows(self, image: ImageArray) -> list[DetectedElement]:
            camera = SyntheticCamera(
                position=(1.5, 0.3, 1.5),
                yaw=0.0,
                pitch=-0.14,
                focal_px=focal,
                width_px=image.width,
                height_px=image.height,
            )
            return [_box(project_box(camera))]

    assert isinstance(Detector(), RoofWindowDetector)
    run = run_autoplan(
        project,
        tmp_path,
        only_level(project).id,
        ref.id,
        tmp_path / "runs",
        depth=AtticDepth(focal_px=focal),
        openings=Detector(),
    )
    assert run.method == "attic", [s.model_dump() for s in run.stages]
    windows = [e for e in only_level(run.project).elements.values() if isinstance(e, RoofWindow)]
    stages = {s.key: s for s in run.stages}
    assert len(windows) == 1, stages["roof_windows"].detail if "roof_windows" in stages else stages
    assert stages["roof_windows"].status == "done"
    assert validate_invariants(run.project).valid
