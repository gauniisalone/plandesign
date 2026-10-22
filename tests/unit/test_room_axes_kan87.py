"""KAN-87 : une chambre estimée en couloir de 0,83 × 5,82 m. Le repère de la pièce s'alignait
sur une surface inclinée qui n'est pas un mur (couette, meuble), les vrais murs devenaient
obliques et étaient écartés, et le plan retombait sur les lignes de fuite. Cas synthétique, sans
les photos privées : pièce de 3,5 × 3,5 m, vue en diagonale, et une face inclinée à 24° dont
l'orientation ne suit pas les murs. Puis le plan trop étroit annoncé « approximatif »."""

from __future__ import annotations

import math

import pytest

from openplan.geom.planes import rotate_plane
from openplan.pipeline.attic import SLOPE_MAX_TILT, attic_axes, find_floor
from openplan.pipeline.confidence import CHECK_MAX, MIN_ROOM_WIDTH_M, assess
from openplan.pipeline.depthplan import AXIS_ALIGNED, observe_room, room_axes
from openplan.pipeline.reference import narrow
from openplan.schemas.scene import RoomSize
from tests.synthetic_depth import Face, SyntheticCamera, box_faces, render_depth

SIDE = 3.5
CAMERA = SyntheticCamera(
    position=(0.6, 0.6, 1.5),
    yaw=math.radians(45),
    pitch=math.radians(-8),
    focal_px=500.0,
    width_px=960,
    height_px=720,
)


def _tilted_face(tilt_deg: float, towards_deg: float, height_at_centre: float) -> Face:
    """Face inclinée de `tilt_deg` sur l'horizontale, montant vers `towards_deg`, passant à
    `height_at_centre` au centre de la pièce : intérieur sous la face."""
    t, d = math.radians(tilt_deg), math.radians(towards_deg)
    n = (math.sin(t) * math.cos(d), math.sin(t) * math.sin(d), math.cos(t))
    c = SIDE / 2.0
    return Face(n, n[0] * c + n[1] * c + n[2] * height_at_centre)


def _aligned_walls(planes: list, floor_idx: int, axes) -> int:  # type: ignore[no-untyped-def]
    up = planes[floor_idx].plane.normal
    count = 0
    for i, p in enumerate(planes):
        if i == floor_idx:
            continue
        if math.acos(min(1.0, abs(float(p.plane.normal @ up)))) <= SLOPE_MAX_TILT:
            continue
        n = rotate_plane(p.plane, axes).normal
        count += abs(float(n[0])) >= AXIS_ALIGNED or abs(float(n[1])) >= AXIS_ALIGNED
    return count


def test_tilted_object_no_longer_turns_the_walls_oblique() -> None:
    # Face inclinée à 24°, orientée à 40° des murs (dans la chambre : 36° et −42°), la plus
    # grande surface vue, comme la couette sur la photo du lit.
    faces = [*box_faces(SIDE, SIDE, 2.5), _tilted_face(24.0, 40.0, 2.0)]
    depth = render_depth(faces, CAMERA, noise=0.01, seed=3)
    obs, planes = observe_room(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert obs is not None
    floor_idx = find_floor(planes)
    assert floor_idx is not None
    up = planes[floor_idx].plane.normal
    # La cause : le repère des combles s'aligne sur la face inclinée, aucun mur n'y est droit.
    assert _aligned_walls(planes, floor_idx, attic_axes(up, planes, floor_idx)) == 0
    # Le correctif : le repère qui aligne le plus de points de murs.
    assert _aligned_walls(planes, floor_idx, room_axes(up, planes, floor_idx)) >= 2
    # Les deux murs vus (x = 3,5 et y = 3,5) à 2,9 m de la caméra, sur leurs axes.
    distances = sorted(abs(w.offset) for w in obs.walls)
    assert len(distances) >= 2
    assert distances[:2] == pytest.approx([SIDE - 0.6, SIDE - 0.6], rel=0.05)
    assert {w.axis for w in obs.walls} == {"x", "y"}


def test_a_room_narrower_than_two_metres_is_approximate() -> None:
    trust = assess("assumed", narrow_room=True)
    assert trust.level == "approximatif" and trust.expected_error > CHECK_MAX
    assert "moins de 2,00 m de large" in trust.reason
    assert narrow(RoomSize(length_m=2.14, width_m=0.92, height_m=2.5))
    assert not narrow(RoomSize(length_m=3.29, width_m=2.87, height_m=2.3))
    # La mesure de référence change l'échelle : la largeur se juge après (×3 : 2,76 m).
    assert not narrow(RoomSize(length_m=2.14, width_m=0.92, height_m=2.5), 3.0)
    assert not narrow(None)
    assert MIN_ROOM_WIDTH_M == 2.0
    assert assess("assumed").level != "approximatif"
