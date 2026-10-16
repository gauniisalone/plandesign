"""E4 : rotation d'un escalier (règles de trémie en direct) et retournement d'une porte."""

from __future__ import annotations

import math

import pytest

from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.layouts import ElementTrialBody, check_element_trial
from openplan.scene.ops import AddFurniture, AddStairs, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Opening, Stairs, Swing
from openplan.schemas.scene import Project
from openplan.validation.layout import check_layout
from tests.factories import only_level, rect_room_project


def _with_stairs(x: float, y: float, yaw: float) -> tuple[Project, Stairs]:
    project = rect_room_project(6.0, 5.0)
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddStairs(kind="straight", position=Point2(x=x, y=y), yaw=yaw)
    )
    stairs = next(e for e in only_level(project).elements.values() if isinstance(e, Stairs))
    return project, stairs


def _types(project: Project, stairs: Stairs, **trial: object) -> tuple[bool, set[str]]:
    level = only_level(project)
    check = check_element_trial(project, level.id, stairs.id, ElementTrialBody(**trial))  # type: ignore[arg-type]
    return check.valid, {v.type for v in check.violations}


def test_stairs_in_the_middle_of_the_room_are_fine_both_ways() -> None:
    project, stairs = _with_stairs(3.0, 2.5, 0.0)
    assert not [v for v in check_layout(project) if stairs.id in v.element_ids]
    valid, types = _types(project, stairs, yaw=math.pi / 2)
    assert valid and not types & {"stairs_on_wall", "stairs_outside_room"}


def test_stairs_turned_into_a_wall_are_refused() -> None:
    project, stairs = _with_stairs(3.0, 0.8, 0.0)
    valid, types = _types(project, stairs, yaw=math.pi / 2)
    assert not valid and "stairs_on_wall" in types


def test_stairs_moved_outside_are_refused() -> None:
    project, stairs = _with_stairs(3.0, 2.5, 0.0)
    valid, types = _types(project, stairs, position=Point2(x=3.0, y=-4.0))
    assert not valid and "stairs_outside_room" in types


def test_furniture_in_the_turned_stairwell_is_reported() -> None:
    project, stairs = _with_stairs(3.0, 2.5, 0.0)
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddFurniture(asset_id="nightstand", position=Point2(x=3.0, y=3.8))
    )
    assert _types(project, stairs)[0]
    valid, types = _types(project, stairs, yaw=math.pi / 2)
    assert not valid and "furniture_in_stairwell" in types


def test_door_flipped_outward_frees_its_swing() -> None:
    """Porte de W1 (y = 0), gond à gauche, vers l'intérieur : une table de chevet dans le
    débattement la bloque ; ouverte vers l'extérieur, la pièce est libre."""
    project = rect_room_project()
    level = only_level(project)
    door = next(e for e in level.elements.values() if isinstance(e, Opening) and e.swing)
    project = apply_op(
        project, level.id, AddFurniture(asset_id="nightstand", position=Point2(x=0.9, y=0.4))
    )
    inward = check_element_trial(project, level.id, door.id, ElementTrialBody())
    assert not inward.valid and inward.message == "débattement de porte gêné"
    outward = check_element_trial(
        project,
        level.id,
        door.id,
        ElementTrialBody(swing=Swing(hinge="left", direction="outward")),
    )
    assert outward.valid


def test_trial_on_a_wall_is_rejected() -> None:
    project = rect_room_project()
    level = only_level(project)
    wall = next(e for e in level.elements.values() if e.type == "wall")
    with pytest.raises(DetectionError):
        check_element_trial(project, level.id, wall.id, ElementTrialBody(yaw=1.0))


def test_stairs_widened_into_a_wall_are_refused() -> None:
    project, stairs = _with_stairs(3.0, 2.5, 0.0)
    assert _types(project, stairs, width=1.2)[0], "au milieu : élargi sans souci"
    valid, types = _types(project, stairs, width=1.2, position=Point2(x=3.0, y=0.5))
    assert not valid and "stairs_on_wall" in types
