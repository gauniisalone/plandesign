"""Murs obliques depuis la profondeur : un mur vu de biais coupe la pièce par son demi-plan
(trapèze, pièce en L dont le fond est de biais) ; un meuble de biais, un mur trop court ou trop
bas ne la déforment pas (repli sur le rectangle). Profondeur synthétique, sans photo."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from openplan.pipeline.depthplan import (
    MIN_OBLIQUE_DEG,
    WallObservation,
    observe_room,
    propose_floorplan_from_depth,
    room_polygon,
)
from openplan.schemas import Camera, Level, Source, Wall
from openplan.schemas.results import Assumption
from openplan.validation.invariants import validate_invariants
from tests.factories import only_level
from tests.synthetic_depth import Face, SyntheticCamera, box_faces, render_depth, render_depth_union
from tests.unit.test_depthplan import _iou, _polygon, _project_with_image

H = 1.4
CAMERA = SyntheticCamera(
    position=(1.0, 0.5, H),
    yaw=math.radians(45),
    pitch=math.radians(-12),
    focal_px=500.0,
    width_px=960,
    height_px=720,
)
TILT = 12.0


def _oblique_face(through: tuple[float, float], tilt_deg: float) -> Face:
    """Mur de fond (normale +y) tourné de `tilt_deg` autour du point `through` : il monte
    vers +x. Intérieur : sous la droite."""
    t = math.radians(tilt_deg)
    n = (-math.sin(t), math.cos(t), 0.0)
    return Face(n, n[0] * through[0] + n[1] * through[1])


def _to_plan(obs, camera: Camera, polygon: Polygon) -> Polygon:  # type: ignore[no-untyped-def]
    """Vérité (monde) exprimée dans le repère du plan (comme pour la pièce en L)."""
    right, cam_up, forward = CAMERA.basis()
    to_world = np.vstack([right, -cam_up, forward])
    x_dir, y_dir = obs.axes[0] @ to_world, obs.axes[1] @ to_world
    origin = np.asarray(CAMERA.position)
    return Polygon(
        [
            (
                float((np.array([x, y, H]) - origin) @ x_dir) + camera.position.x,
                float((np.array([x, y, H]) - origin) @ y_dir) + camera.position.y,
            )
            for x, y in polygon.exterior.coords
        ]
    )


def _propose(faces_or_cells, union: bool = False):  # type: ignore[no-untyped-def]
    depth = (
        render_depth_union(faces_or_cells, CAMERA, noise=0.01, seed=4)
        if union
        else render_depth(faces_or_cells, CAMERA, noise=0.01, seed=4)
    )
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
    return obs, after, result


def _directions(level: Level, source: Source) -> list[float]:
    """Direction (degrés, modulo 180) des murs de cette source."""
    out = []
    for wall in level.elements.values():
        if isinstance(wall, Wall) and wall.source is source:
            a = level.vertices[wall.start_vertex].position
            b = level.vertices[wall.end_vertex].position
            out.append(math.degrees(math.atan2(b.y - a.y, b.x - a.x)) % 180)
    return out


def _angle(a: float, b: float) -> float:
    d = abs(a - b) % 180
    return min(d, 180 - d)


def test_trapezoid_room_keeps_its_oblique_wall() -> None:
    # Pièce 4 m de large, mur de fond de biais à 12° : 3,00 m à gauche, 3,85 m à droite.
    faces = [*box_faces(4.0, 6.0, 2.5)[:5], _oblique_face((0.0, 3.0), TILT)]
    obs, after, result = _propose(faces)
    level = only_level(after)
    # MH1 : la droite vraie est gardée, avec son étendue.
    tilted = [w for w in obs.walls if w.tilt_deg >= MIN_OBLIQUE_DEG]
    assert len(tilted) == 1 and tilted[0].tilt_deg == pytest.approx(TILT, abs=1.5)
    assert tilted[0].along[1] - tilted[0].along[0] > 2.0
    # MH3 : deux murs vus, à 90 − 12 = 78° l'un de l'autre ; les deux supposés d'équerre.
    seen = _directions(level, Source.ESTIMATED)
    assert len(seen) == 2 and _angle(*seen) == pytest.approx(90 - TILT, abs=2.0)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    assert len(walls) == 4 and validate_invariants(after).valid
    assert any("sauf ceux vus de biais" in a.text for a in result.assumptions)
    assert any("Mur vu de biais (12°" in a.text for a in result.assumptions)
    # MH2 : le polygone suit le trapèze sur la partie vue (devant et à droite de la caméra).
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    truth = _to_plan(
        obs, camera, Polygon([(0, 0), (4, 0), (4, 3.0 + 4 * math.tan(math.radians(TILT))), (0, 3)])
    )
    seen_part = _to_plan(obs, camera, box(1.0, 0.5, 6.0, 6.0))
    iou = _iou(_polygon(level).intersection(seen_part), truth.intersection(seen_part))
    assert iou >= 0.9, f"IoU {iou:.2f}"


def test_l_room_with_an_oblique_far_wall() -> None:
    # Pièce en L de test_depthplan, fond de la cellule B de biais (6,00 m à droite, 5,30 m à
    # gauche) : renfoncement et mur de biais ensemble.
    cell_a = box_faces(4.0, 3.0, 2.5)
    cell_b = [
        Face((0.0, 0.0, -1.0), 0.0),
        Face((0.0, 0.0, 1.0), 2.5),
        Face((-1.0, 0.0, 0.0), -2.0),
        Face((1.0, 0.0, 0.0), 6.0),
        Face((0.0, -1.0, 0.0), 0.0),
        _oblique_face((6.0, 6.0), 10.0),
    ]
    obs, after, result = _propose([cell_a, cell_b], union=True)
    level = only_level(after)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    assert len(walls) == 6, "pièce en L : six murs"
    assert validate_invariants(after).valid
    assert any("Renfoncement" in a.text for a in result.assumptions)
    seen = _directions(level, Source.ESTIMATED)
    assert any(_angle(d, e) == pytest.approx(80.0, abs=2.5) for d in seen for e in seen)
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    tan = math.tan(math.radians(10.0))
    truth_l = box(0, 0, 4, 3).union(Polygon([(2, 0), (6, 0), (6, 6), (2, 6 - 4 * tan)]))
    iou = _iou(_polygon(level), _to_plan(obs, camera, truth_l))
    assert iou >= 0.7, f"IoU {iou:.2f}"


def _oblique(
    distance: float,
    tilt_deg: float,
    along: tuple[float, float],
    *,
    count: int = 800,
    height_m: float = 2.4,
) -> WallObservation:
    """Mur de fond (côté +y) tourné de `tilt_deg` (il monte vers +x), à `distance` de la
    caméra, vu sur `along` le long de sa tangente (x décroît quand on avance)."""
    t = math.radians(tilt_deg)
    n = (-math.sin(t), math.cos(t))
    ends = [(n[0] * distance - n[1] * s, n[1] * distance + n[0] * s) for s in along]
    xs = sorted(x for x, _ in ends)
    offset = (ends[0][1] + ends[1][1]) / 2
    return WallObservation("y", offset, (xs[0], xs[1]), count, 0.01, n, distance, along, height_m)


def _side(x: float, top: float) -> WallObservation:
    """Mur latéral x = `x`, vu de y = −0,5 à `top`."""
    if x > 0:
        return WallObservation("x", x, (-0.5, top), 900, 0.01, (1.0, 0.0), x, (-0.5, top), 2.4)
    return WallObservation("x", x, (-0.5, top), 400, 0.01, (-1.0, 0.0), -x, (-top, 0.5), 2.4)


# Pièce −1 ≤ x ≤ 3, fond y = 3,5 vu à gauche seulement (x ≤ 1 : la droite est masquée).
BOX = [
    _side(3.0, 3.5),
    _side(-1.0, 3.5),
    WallObservation("y", 3.5, (-1.0, 1.0), 900, 0.01, (0.0, 1.0), 3.5, (-1.0, 1.0), 2.4),
]
# Armoire de biais à 15°, à droite (x de 1,5 à 2,8), à 2 m de la caméra.
WARDROBE = (-3.43, -2.09)


def test_an_oblique_wall_cuts_the_room() -> None:
    assumptions: list[Assumption] = []
    # Fond de biais à 10° : y = 2,87 à gauche (x = −1), 3,58 à droite (x = 3).
    walls = [_side(3.0, 3.5), _side(-1.0, 2.85), _oblique(3.0, 10.0, (-3.575, 0.486))]
    polygon, kept = room_polygon(walls, assumptions)
    assert sum(w.normal is not None for w in kept) == 1
    # Rectangle −1 ≤ x ≤ 3, −0,5 ≤ y, coupé par la droite de biais (au-dessus : dehors).
    assert polygon.area < box(-1.0, -0.5, 3.0, 4.0).area
    xs = sorted({round(x, 6) for x, _ in polygon.exterior.coords})
    assert xs == [-1.0, 3.0]
    assert any("Mur vu de biais (10°" in a.text for a in assumptions)


@pytest.mark.parametrize(
    ("wall", "reason"),
    [
        # Armoire en travers devant le mur droit, vu derrière elle.
        (_oblique(2.0, 15.0, WARDROBE), "vu derrière lui"),
        # Tête de lit de biais : trop basse.
        (_oblique(2.0, 15.0, WARDROBE, height_m=0.9), "trop bas"),
        # Quelques points seulement.
        (_oblique(2.0, 15.0, WARDROBE, count=50), "trop peu de points"),
        # Un bout de 20 cm.
        (_oblique(2.0, 15.0, (-2.30, -2.09)), "trop court"),
    ],
)
def test_furniture_does_not_bend_the_room(wall: WallObservation, reason: str) -> None:
    assumptions: list[Assumption] = []
    polygon, kept = room_polygon([*BOX, wall], assumptions)
    assert all(w.normal is None for w in kept)
    assert any(reason in a.text for a in assumptions)
    # Repli : le plan d'avant, ce même meuble posé d'équerre.
    before, _ = room_polygon([*BOX, wall.posed()], [])
    assert _iou(polygon, before) > 0.999


def test_a_steep_rejected_wall_is_ignored_and_a_slight_one_is_posed() -> None:
    assumptions: list[Assumption] = []
    steep = _oblique(2.0, 35.0, (0.0, 1.0))
    polygon, kept = room_polygon([*BOX, steep], assumptions)
    assert len(kept) == 3
    assert any("Mur oblique (35° du repère) ignoré" in a.text for a in assumptions)
    assert _iou(polygon, box(-1.0, -0.5, 3.0, 3.5)) > 0.99
    # Sous 4° : posé d'équerre, sans note (Manhattan, comme avant).
    notes: list[Assumption] = []
    slight = _oblique(3.5, 2.0, (-1.0, 3.0))
    polygon, kept = room_polygon([*BOX[:2], slight], notes)
    assert all(w.normal is None for w in kept) and not any("biais" in a.text for a in notes)
    assert polygon.area == pytest.approx(box(-1.0, -0.5, 3.0, slight.offset).area)
