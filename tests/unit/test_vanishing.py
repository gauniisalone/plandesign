"""Points de fuite et repère Manhattan sur des lignes synthétiques à caméra connue."""

from __future__ import annotations

import math

import numpy as np
import pytest
from numpy.typing import NDArray

from openplan.adapters.heuristic.camera import (
    backproject_floor,
    horizon_line,
    manhattan_frame,
    vp_direction,
)
from openplan.adapters.heuristic.vanishing import (
    VanishingPoint,
    angular_residual,
    find_vanishing_points,
    segment_lines,
)

Float = NDArray[np.float64]
WIDTH, HEIGHT, FOCAL = 1280, 960, 1000.0
PP = (WIDTH / 2, HEIGHT / 2)


def rotation(pitch: float, roll: float, yaw: float) -> Float:
    """Lignes = axes X, Y, Z (haut) du plan exprimés dans le repère caméra (x droite, y bas,
    z avant). Construit par la définition inverse de ManhattanFrame : up depuis pitch/roll,
    puis X tourné de yaw autour de up par rapport à l'avant horizontal."""
    up = np.array(
        [math.sin(roll) * math.cos(pitch), -math.cos(roll) * math.cos(pitch), math.sin(pitch)]
    )
    forward = np.array([0.0, 0.0, 1.0])
    horizontal = forward - (forward @ up) * up
    horizontal /= np.linalg.norm(horizontal)
    # e_x tel que l'angle (e_x → horizontal) autour de up vaut yaw.
    e_x = math.cos(yaw) * horizontal - math.sin(yaw) * np.cross(up, horizontal)
    e_y = np.cross(up, e_x)
    return np.vstack([e_x, e_y, up])


def project(points_world: Float, axes: Float, camera_pos: Float) -> Float:
    # axes lignes = e_i en coords caméra ⇒ vecteur caméra r = Σ w_i e_i = w @ axes
    cam_xyz = (points_world - camera_pos) @ axes
    u = PP[0] + FOCAL * cam_xyz[:, 0] / cam_xyz[:, 2]
    v = PP[1] + FOCAL * cam_xyz[:, 1] / cam_xyz[:, 2]
    return np.column_stack([u, v])


def synthetic_room(
    pitch: float, roll: float, yaw: float, seed: int = 1
) -> tuple[Float, Float, Float]:
    """Segments image d'une pièce 4 × 3 × 2,5 vue de (0.6, 0.5, 1.4), plus des parasites."""
    axes = rotation(pitch, roll, yaw)
    camera_pos = np.array([0.6, 0.5, 1.4])
    rng = np.random.default_rng(seed)
    edges: list[tuple[Float, Float]] = []
    # Lignes horizontales sur les murs (plinthes, corniches, cadres) le long de X et de Y.
    for z in (0.0, 0.9, 1.1, 2.5):
        for y in (0.0, 3.0):
            for x0, x1 in ((0.2, 1.4), (1.6, 3.0), (3.2, 3.9)):
                edges.append((np.array([x0, y, z]), np.array([x1, y, z])))
        for x in (0.0, 4.0):
            for y0, y1 in ((0.2, 1.2), (1.5, 2.8)):
                edges.append((np.array([x, y0, z]), np.array([x, y1, z])))
    for x in (0.0, 4.0, 2.8, 1.6):
        for y in (0.0, 3.0):
            edges.append((np.array([x, y, 0.0]), np.array([x, y, 2.5])))
    for y in (1.0, 2.0):
        edges.append((np.array([4.0, y, 0.0]), np.array([4.0, y, 2.5])))
    # Chaque arête est découpée en morceaux : un détecteur de segments fragmente aussi.
    pieces = 6
    starts = np.array([a + (b - a) * k / pieces for a, b in edges for k in range(pieces)])
    ends = np.array([a + (b - a) * (k + 1) / pieces for a, b in edges for k in range(pieces)])
    p0 = project(starts, axes, camera_pos)
    p1 = project(ends, axes, camera_pos)
    inside = (
        (p0[:, 0] > 0)
        & (p0[:, 0] < WIDTH)
        & (p0[:, 1] > 0)
        & (p0[:, 1] < HEIGHT)
        & (p1[:, 0] > 0)
        & (p1[:, 0] < WIDTH)
        & (p1[:, 1] > 0)
        & (p1[:, 1] < HEIGHT)
    )
    segments = np.hstack([p0[inside], p1[inside]]) + rng.normal(
        0.0, 0.5, size=(int(inside.sum()), 4)
    )
    # Parasites réalistes : segments courts (20 à 120 px) d'orientation quelconque.
    n_out = 40
    origin = rng.uniform([0, 0], [WIDTH, HEIGHT], size=(n_out, 2))
    theta = rng.uniform(0, math.pi, size=n_out)
    length = rng.uniform(20, 120, size=n_out)
    delta = np.column_stack([np.cos(theta) * length, np.sin(theta) * length])
    outliers = np.hstack([origin, origin + delta])
    return np.vstack([segments, outliers]), axes, camera_pos


def angle_diff(a: float, b: float, period: float = 2 * math.pi) -> float:
    d = (a - b) % period
    return min(d, period - d)


@pytest.mark.parametrize(
    ("pitch", "roll", "yaw"),
    [(-0.14, 0.05, 1.05), (0.1, -0.03, 0.6), (-0.05, 0.0, 1.4)],
)
def test_manhattan_frame_recovers_pose_and_focal(pitch: float, roll: float, yaw: float) -> None:
    segments, axes, camera_pos = synthetic_room(pitch, roll, yaw)
    assert len(segments) > 40
    vps = find_vanishing_points(segments)
    assert len(vps) == 3, "trois familles : X, Y et verticale"
    frame = manhattan_frame(vps, focal_prior_px=0.8 * WIDTH, principal_point=PP, long_side=WIDTH)
    assert frame is not None
    assert frame.focal_from_vps and frame.focal_px == pytest.approx(FOCAL, rel=0.03)
    assert angle_diff(frame.pitch, pitch) < math.radians(1.0)
    assert angle_diff(frame.roll, roll) < math.radians(1.0)
    assert angle_diff(frame.yaw_hint, yaw, period=math.pi / 2) < math.radians(1.0)
    # Le repère estimé coïncide avec le vrai, à une permutation/inversion des axes horizontaux près.
    assert abs(float(frame.up @ axes[2])) > math.cos(math.radians(1.0))
    # Rétro-projection d'un point du sol : retrouve ses coordonnées plan (caméra à l'origine).
    floor_world = np.array([[3.0, 2.5, 0.0], [1.0, 2.8, 0.0]])
    px = project(floor_world, axes, camera_pos)
    plan = backproject_floor(frame, px, camera_height=float(camera_pos[2]))
    expected = (floor_world - camera_pos)[:, :2]
    # Comparaison invariante à l'orientation du repère : distances à la caméra.
    assert np.linalg.norm(plan, axis=1) == pytest.approx(np.linalg.norm(expected, axis=1), rel=0.03)
    a, b, _c = horizon_line(frame)
    assert math.hypot(a, b) > 0


def test_residual_handles_infinite_vanishing_point() -> None:
    segments = np.array(
        [[0.0, 10.0, 100.0, 10.0], [0.0, 50.0, 100.0, 50.0], [0.0, 0.0, 0.0, 100.0]]
    )
    infinite = np.array([1.0, 0.0, 0.0])
    residual = angular_residual(segments, infinite)
    assert residual[0] == pytest.approx(0.0) and residual[1] == pytest.approx(0.0)
    assert residual[2] == pytest.approx(math.pi / 2)
    lines = segment_lines(segments)
    assert np.allclose(np.linalg.norm(lines[:, :2], axis=1), 1.0)
    vp = VanishingPoint(homogeneous=(1.0, 0.0, 0.0), inliers=(0, 1), weight=200.0)
    assert vp.at_infinity and vp.point is None
    d = vp_direction(vp, 1000.0, (0.0, 0.0))
    assert np.allclose(d, [1.0, 0.0, 0.0])
    assert find_vanishing_points(segments[:1]) == []


def test_frame_requires_enough_information() -> None:
    assert manhattan_frame([], 1000.0, PP, WIDTH) is None
    only_vertical = [VanishingPoint(homogeneous=(0.0, 1.0, 0.0), inliers=(0,), weight=1.0)]
    assert manhattan_frame(only_vertical, 1000.0, PP, WIDTH) is None
    one_horizontal = [VanishingPoint(homogeneous=(1.0, 0.0, 0.0), inliers=(0,), weight=1.0)]
    assert manhattan_frame(one_horizontal, 1000.0, PP, WIDTH) is None
    # Deux horizontales à l'infini : la verticale est leur produit vectoriel, focale a priori.
    two = [
        VanishingPoint(homogeneous=(1.0, 0.0, 0.0), inliers=(0,), weight=2.0),
        VanishingPoint(homogeneous=(0.0, 0.0, 1.0), inliers=(1,), weight=1.0),
    ]
    frame = manhattan_frame(two, 1000.0, (0.0, 0.0), WIDTH)
    assert frame is not None and not frame.focal_from_vps
    assert frame.pitch == pytest.approx(0.0) and frame.roll == pytest.approx(0.0)
