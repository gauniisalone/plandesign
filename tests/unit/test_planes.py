"""Ajustement de plans et estimation des combles sur des profondeurs synthétiques exactes."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openplan.geom.planes import (
    Plane,
    backproject,
    fit_planes,
    plane_from_points,
    ransac_plane,
)
from openplan.pipeline.attic import (
    USABLE_HEIGHT_M,
    AtticEstimate,
    analyse_attic,
    planes_overlay,
    rescale,
)
from tests.synthetic_depth import SyntheticCamera, attic_faces, box_faces, render_depth

ATTIC = {"length": 6.0, "width": 5.0, "ridge_height": 3.2, "eave_height": 1.0}
CAMERA = SyntheticCamera(
    position=(1.5, 0.3, 1.45), yaw=0.0, pitch=-0.14, focal_px=420.0, width_px=640, height_px=480
)


def test_plane_from_points_and_orientation() -> None:
    pts = np.array([[0.0, 0.0, 2.0], [1.0, 0.0, 2.0], [0.0, 1.0, 2.0], [1.0, 1.0, 2.0]])
    plane = plane_from_points(pts)
    assert plane is not None
    assert plane.distance == pytest.approx(2.0)
    assert plane.normal @ np.array([0.0, 0.0, -1.0]) == pytest.approx(1.0), "vers la caméra"
    assert plane_from_points(np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 2.0], [0.0, 0.0, 3.0]])) is None
    assert Plane(np.array([0.0, 0.0, -1.0]), 2.0).height_at(0.0, 0.0) == pytest.approx(2.0)
    assert Plane(np.array([1.0, 0.0, 0.0]), 2.0).height_at(0.0, 0.0) is None


def test_ransac_recovers_plane_among_outliers() -> None:
    rng = np.random.default_rng(1)
    inliers = np.column_stack(
        [rng.uniform(-2, 2, 600), rng.uniform(-2, 2, 600), np.full(600, 3.0)]
    ) + rng.normal(0, 0.01, (600, 3))
    outliers = rng.uniform(-3, 3, (200, 3)) + np.array([0.0, 0.0, 4.0])
    points = np.vstack([inliers, outliers])
    pixels = np.column_stack([rng.integers(0, 640, len(points)), rng.integers(0, 480, len(points))])
    fitted = ransac_plane(
        points, pixels.astype(np.intp), rng=np.random.default_rng(0), window_px=1000
    )
    assert fitted is not None
    assert abs(float(fitted.plane.normal[2])) > 0.999 and fitted.plane.distance == pytest.approx(
        3.0, abs=0.01
    )
    assert 560 <= fitted.count <= 640 and fitted.rms_m < 0.02


def test_backproject_roundtrip() -> None:
    depth = np.full((48, 64), 2.0, dtype=np.float32)
    depth[0, 0] = np.nan
    depth[1, 1] = 0.0
    points, pixels = backproject(depth, focal_px=100.0, principal_point=(32.0, 24.0), stride=1)
    assert len(points) == 48 * 64 - 2 and pixels.shape == (len(points), 2)
    corner = points[(pixels[:, 0] == 63) & (pixels[:, 1] == 47)][0]
    assert corner == pytest.approx([(63 - 32) * 2 / 100, (47 - 24) * 2 / 100, 2.0])


def test_fit_planes_finds_the_box_walls() -> None:
    cam = SyntheticCamera(
        position=(1.0, 1.0, 1.4), yaw=0.8, pitch=-0.2, focal_px=500.0, width_px=400, height_px=300
    )
    depth = render_depth(box_faces(4.0, 3.5, 2.5), cam, noise=0.005)
    points, pixels = backproject(depth, cam.focal_px, cam.principal_point, stride=3)
    planes = fit_planes(points, pixels, (400, 300))
    distances = sorted(round(p.plane.distance, 1) for p in planes)
    # Sol à 1,4 m sous la caméra ; mur de droite (y = 3,5) à 2,5 m ; mur de face (x = 4) à 3 m.
    # Le plafond, 1,1 m au-dessus, sort du champ (visée vers le bas).
    assert distances == [1.4, 2.5, 3.0]
    assert all(p.rms_m < 0.05 for p in planes)


def attic_estimate(camera: SyntheticCamera, **kwargs: float) -> AtticEstimate:
    params = {**ATTIC, **kwargs}
    depth = render_depth(attic_faces(**params), camera, noise=0.01, outlier_fraction=0.02)
    estimate, planes, cls, pixel_index, warnings = analyse_attic(
        depth, camera.focal_px, camera.principal_point
    )
    assert cls is not None, warnings
    assert len(planes) >= 4
    overlay = planes_overlay(
        np.zeros((*depth.shape, 3), dtype=np.uint8), planes, cls, pixel_index, 4
    )
    assert overlay.shape == (*depth.shape, 3) and overlay.max() > 0
    return estimate


def check_attic(est: AtticEstimate, camera: SyntheticCamera, rel: float = 0.06) -> None:
    length, width = ATTIC["length"], ATTIC["width"]
    ridge, eave = ATTIC["ridge_height"], ATTIC["eave_height"]
    assert est.camera_height is not None and est.ridge_height is not None
    assert est.width is not None and est.gable_distance is not None
    assert est.camera_height.value == pytest.approx(camera.position[2], rel=rel)
    assert est.ridge_height.value == pytest.approx(ridge, rel=rel)
    assert est.width.value == pytest.approx(width, rel=rel)
    assert est.gable_distance.value == pytest.approx(length - camera.position[0], rel=rel)
    assert [round(e.value, 1) for e in est.eave_heights] == pytest.approx([eave, eave], abs=0.15)
    # Le mur latéral opposé à la visée sort du champ : son égout est déduit par symétrie.
    sources = sorted(e.source for e in est.eave_heights)
    assert sources == ["estimated", "inferred"] and est.width.source == "inferred"
    assert est.width.note is not None and "symétrique" in est.width.note
    slope = (ridge - eave) / (width / 2)
    expected_pitch = math.degrees(math.atan(slope))
    assert est.slope_pitch_deg == pytest.approx([expected_pitch] * 2, abs=3.0)
    assert est.usable_width is not None
    assert est.usable_width.value == pytest.approx(2 * (ridge - USABLE_HEIGHT_M) / slope, rel=rel)
    assert est.length is None, "un seul pignon vu"
    for e in (est.camera_height, est.ridge_height, est.width, est.gable_distance):
        assert e.sigma >= 0.15 * e.value, "l'incertitude d'échelle est toujours comptée"
    kinds = {p.kind for p in est.planes}
    assert {"floor", "slope", "gable", "side_wall"} <= kinds


def test_attic_estimate_along_the_ridge() -> None:
    check_attic(attic_estimate(CAMERA), CAMERA)


def test_attic_estimate_with_yawed_camera() -> None:
    """Le repère des combles vient des pentes : la visée n'a pas à suivre le faîtage."""
    cam = SyntheticCamera(
        position=(1.5, -0.4, 1.3),
        yaw=0.35,
        pitch=-0.10,
        focal_px=420.0,
        width_px=640,
        height_px=480,
    )
    check_attic(attic_estimate(cam), cam)


def test_rescale_on_user_camera_height() -> None:
    est = attic_estimate(CAMERA)
    assert est.camera_height is not None and est.ridge_height is not None and est.width
    doubled = rescale(est, 2 * est.camera_height.value)
    assert doubled.scale_factor == pytest.approx(2.0)
    assert doubled.camera_height is not None and doubled.ridge_height is not None
    assert doubled.camera_height.source == "user_defined"
    assert doubled.ridge_height.value == pytest.approx(2 * est.ridge_height.value)
    assert doubled.ridge_height.sigma == pytest.approx(0.10 * doubled.ridge_height.value, rel=0.2)
    assert doubled.width is not None and doubled.width.source == est.width.source
    assert doubled.planes[0].distance_m == pytest.approx(2 * est.planes[0].distance_m)
    assert doubled.scale_note is not None and "facteur 2,00" in doubled.scale_note
    assert rescale(AtticEstimate(), 1.5) == AtticEstimate(), "sans sol, rien à recaler"


def test_attic_without_floor_yields_empty_estimate() -> None:
    cam = SyntheticCamera(
        position=(1.5, 0.0, 1.0), yaw=0.0, pitch=0.9, focal_px=600.0, width_px=320, height_px=240
    )
    depth = render_depth(attic_faces(**ATTIC), cam)
    estimate, _planes, cls, _pixels, warnings = analyse_attic(
        depth, cam.focal_px, cam.principal_point
    )
    assert cls is None and estimate.camera_height is None
    assert any("sol" in w for w in warnings)


def test_frame_disagreement_warning() -> None:
    depth = render_depth(attic_faces(**ATTIC), CAMERA)
    sideways = np.array([1.0, 0.0, 0.0])
    _est, _planes, _cls, _pixels, warnings = analyse_attic(
        depth, CAMERA.focal_px, CAMERA.principal_point, frame_up=sideways
    )
    assert any("divergent" in w for w in warnings)


def test_unrecognised_far_gable_is_measured_from_the_farthest_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Photos d'augus (4 oct. 2026) : le pignon du fond, petit et encombré, n'était pas un plan
    net ; la longueur tombait sur 5 m supposés au lieu du grenier long qu'on voit. Le repli
    mesure le pignon aux points les plus lointains dans l'axe du faîtage."""
    from openplan.pipeline import attic as attic_module
    from openplan.pipeline.autoplan import attic_geometry

    long_attic = {**ATTIC, "length": 9.0}
    depth = render_depth(attic_faces(**long_attic), CAMERA, noise=0.01)
    seen, *_ = analyse_attic(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert seen.gable_distance is not None
    monkeypatch.setattr(attic_module, "_gables_summary", lambda *a, **k: None)
    est, _planes, _cls, _pixels, warnings = analyse_attic(
        depth, CAMERA.focal_px, CAMERA.principal_point
    )
    assert est.gable_distance is not None
    assert est.gable_distance.value == pytest.approx(9.0 - CAMERA.position[0], rel=0.08)
    assert any("non reconnu" in w for w in warnings)
    from uuid import uuid4

    geometry = attic_geometry(est, uuid4(), uuid4())
    assert geometry is not None and geometry.length.value > 7.0, "plus les 5 m supposés"


def test_far_along_ridge_needs_a_photo_along_the_ridge() -> None:
    from openplan.pipeline.attic import far_along_ridge

    rng = np.random.default_rng(0)
    points = np.column_stack(
        [rng.uniform(-1, 1, 2000), rng.uniform(-1, 1, 2000), rng.uniform(0.5, 7.0, 2000)]
    )
    along_view = np.eye(3)[[2, 0, 1]]  # faîtage = axe optique
    assert far_along_ridge(points, along_view) == pytest.approx(7.0, abs=0.3)
    across = np.eye(3)  # faîtage = x caméra : la photo regarde en travers
    assert far_along_ridge(points, across) is None
