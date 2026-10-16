"""E2 : redimensionner un meuble sur le plan — bornes du catalogue et règles en direct."""

from __future__ import annotations

import pytest

from openplan.pipeline.layouts import FurnitureMoveBody, check_furniture_move
from openplan.scene.catalog import asset
from openplan.scene.ops import AddFurniture, OpError, UpdateElement, apply_op
from openplan.schemas.common import Point2
from openplan.schemas.elements import Furniture
from tests.factories import only_level, rect_room_project


def _with_desk():  # type: ignore[no-untyped-def]
    project = rect_room_project(5.0, 4.0)
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.5, y=2.0))
    )
    desk = next(e for e in only_level(project).elements.values() if isinstance(e, Furniture))
    return project, level_id, desk


def test_resize_stays_within_the_catalog_range() -> None:
    project, level_id, desk = _with_desk()
    limits = asset("desk_140").width
    wider = desk.bbox.model_copy(update={"width": limits.max})
    resized = apply_op(
        project,
        level_id,
        UpdateElement(element_id=desk.id, fields={"bbox": wider.model_dump(mode="json")}),
    )
    assert only_level(resized).elements[desk.id].bbox.width == pytest.approx(limits.max)  # type: ignore[union-attr]
    too_wide = desk.bbox.model_copy(update={"width": limits.max + 0.5})
    with pytest.raises(OpError, match="hors de la plage du catalogue"):
        apply_op(
            project,
            level_id,
            UpdateElement(element_id=desk.id, fields={"bbox": too_wide.model_dump(mode="json")}),
        )


def test_live_check_uses_the_tried_size() -> None:
    project, level_id, desk = _with_desk()
    # Contre le mur du bas (y = 0) : à sa profondeur normale il tient, trop profond il entre
    # dans le mur.
    near_wall = Point2(x=2.5, y=0.075 + desk.bbox.depth / 2 + 0.02)
    ok = check_furniture_move(project, level_id, desk.id, FurnitureMoveBody(position=near_wall))
    assert ok.valid
    deep = check_furniture_move(
        project,
        level_id,
        desk.id,
        FurnitureMoveBody(position=near_wall, depth=desk.bbox.depth + 0.4),
    )
    assert not deep.valid and "mur" in deep.message
