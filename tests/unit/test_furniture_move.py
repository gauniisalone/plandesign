"""Glisser-déposer d'un meuble (3.6) : les règles à la position essayée, sans modifier la scène."""

from __future__ import annotations

import math
from uuid import UUID

import pytest

from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.layouts import (
    OUTSIDE_ROOM,
    FurnitureMoveBody,
    FurniturePlaceBody,
    check_furniture_move,
    check_furniture_place,
)
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas import Furniture
from openplan.schemas.common import Point2
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _with_desk() -> tuple[Project, UUID, Furniture]:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    desk = next(e for e in only_level(project).elements.values() if isinstance(e, Furniture))
    return project, level_id, desk


def test_move_check_says_the_hard_rule_in_two_words_and_changes_nothing() -> None:
    project, level_id, desk = _with_desk()
    ok = check_furniture_move(
        project,
        level_id,
        desk.id,
        FurnitureMoveBody(position=Point2(x=2.0, y=1.5)),
    )
    assert ok.valid and ok.message == ""
    # Dans le mur du fond (y = 3) : règle dure « meuble dans un mur ».
    in_wall = check_furniture_move(
        project,
        level_id,
        desk.id,
        FurnitureMoveBody(position=Point2(x=2.0, y=3.0)),
    )
    assert not in_wall.valid and in_wall.message == "meuble dans un mur"
    assert in_wall.violations[0].severity == "high"
    # Dans le débattement de la porte (charnière à x = 0,5 sur le mur y = 0).
    swing = check_furniture_move(
        project,
        level_id,
        desk.id,
        FurnitureMoveBody(position=Point2(x=0.9, y=0.6), yaw=math.pi),
    )
    assert not swing.valid and "porte" in swing.message
    # Dehors, sans toucher un mur : « hors de la pièce ».
    outside = check_furniture_move(
        project,
        level_id,
        desk.id,
        FurnitureMoveBody(position=Point2(x=8.0, y=8.0)),
    )
    assert not outside.valid and outside.message == "hors de la pièce"
    assert outside.violations[0].type == OUTSIDE_ROOM
    # La scène n'a pas bougé.
    still = only_level(project).elements[desk.id]
    assert isinstance(still, Furniture) and still.transform.position.x == 2.0
    with pytest.raises(DetectionError, match="meuble inconnu"):
        check_furniture_move(
            project,
            level_id,
            level_id,
            FurnitureMoveBody(position=Point2(x=1.0, y=1.0)),
        )


def test_place_check_tries_a_catalog_piece_without_adding_it() -> None:
    """F3 : le meuble choisi dans la bibliothèque est essayé sous le curseur avec les mêmes
    règles que le glisser ; la scène ne change pas ; un meuble inconnu est refusé."""
    from openplan.scene.catalog import CatalogError

    project = rect_room_project()
    level_id = only_level(project).id
    before = len(only_level(project).elements)
    ok = check_furniture_place(
        project, level_id, FurniturePlaceBody(asset_id="toilet", position=Point2(x=2.0, y=1.5))
    )
    assert ok.valid and ok.message == ""
    in_wall = check_furniture_place(
        project, level_id, FurniturePlaceBody(asset_id="bathtub", position=Point2(x=2.0, y=3.0))
    )
    assert not in_wall.valid and in_wall.message == "meuble dans un mur"
    outside = check_furniture_place(
        project, level_id, FurniturePlaceBody(asset_id="toilet", position=Point2(x=8.0, y=8.0))
    )
    assert not outside.valid and outside.message == "hors de la pièce"
    assert len(only_level(project).elements) == before
    with pytest.raises(CatalogError, match="meuble inconnu"):
        check_furniture_place(
            project, level_id, FurniturePlaceBody(asset_id="licorne", position=Point2(x=1, y=1))
        )
