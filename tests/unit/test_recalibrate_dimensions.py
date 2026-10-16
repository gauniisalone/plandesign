"""R3 : Recalibrer avec longueur, largeur et hauteur de la pièce."""

from __future__ import annotations

import math

import pytest

from openplan.geom.scale import CalibrationError, apply_axis_scale
from openplan.pipeline.recalibrate import (
    DimensionsBody,
    preview_dimensions,
    recalibrate_dimensions,
    room_axes,
)
from openplan.scene import history
from openplan.scene.ops import AddFurniture, apply_op
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Furniture, Opening, RoofPlane, Wall
from tests.factories import attic_project, only_level, rect_room_project


def _span(level, axis: str) -> float:  # type: ignore[no-untyped-def]
    values = [getattr(v.position, axis) for v in level.vertices.values()]
    return max(values) - min(values)


def test_preview_says_before_and_after_without_writing() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    current = preview_dimensions(project, level_id, DimensionsBody())
    assert current.text == "" and current.before.length_m == pytest.approx(3.85)
    assert current.before.width_m == pytest.approx(2.85)
    preview = preview_dimensions(project, level_id, DimensionsBody(length_m=7.7, width_m=4.275))
    assert preview.text == "3,85 × 2,85 m → 7,70 × 4,28 m"
    assert preview.sx == pytest.approx(2.0) and preview.sy == pytest.approx(1.5)
    assert not preview.uniform and project.history_cursor == 0


def test_length_and_width_stretch_the_plan_along_the_room_axes() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    project = apply_op(
        project, level_id, AddFurniture(asset_id="desk_140", position=Point2(x=2.0, y=1.5))
    )
    before = only_level(project)
    after, summary, dims = recalibrate_dimensions(
        project, level_id, DimensionsBody(length_m=7.7, width_m=4.275), actor="user"
    )
    level = only_level(after)
    assert _span(level, "x") == pytest.approx(_span(before, "x") * 2.0)
    assert _span(level, "y") == pytest.approx(_span(before, "y") * 1.5)
    assert level.height == before.height, "hauteur non donnée : inchangée"
    door = next(e for e in level.elements.values() if isinstance(e, Opening) and e.name == "door")
    old_door = before.elements[door.id]
    assert isinstance(old_door, Opening) and door.offset == pytest.approx(old_door.offset * 2.0)
    desk = next(e for e in level.elements.values() if isinstance(e, Furniture))
    assert desk.transform.position.x == pytest.approx(4.0) and desk.transform.yaw == pytest.approx(
        0.0
    )
    assert desk.bbox.width == pytest.approx(1.4), "les cotes des meubles ne s'étirent pas"
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    assert all(w.source is Source.USER_DEFINED for w in walls)
    assert summary.text == "3,85 × 2,85 m → 7,70 × 4,28 m" and dims.after.length_m == pytest.approx(
        7.7
    )
    assert after.history[-1].label.startswith("Recalibrer : 3,85 × 2,85 m")
    undone = history.undo(after)
    assert _span(only_level(undone), "x") == pytest.approx(_span(before, "x"))


def test_one_value_scales_uniformly_like_before() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    before = only_level(project)
    after, summary, _ = recalibrate_dimensions(
        project, level_id, DimensionsBody(height_m=3.0), actor="user"
    )
    level = only_level(after)
    s = 3.0 / before.height
    assert _span(level, "x") == pytest.approx(_span(before, "x") * s)
    assert _span(level, "y") == pytest.approx(_span(before, "y") * s)
    assert level.height == pytest.approx(3.0) and level.height_source is Source.USER_DEFINED
    assert summary.what == "la hauteur sous plafond" and summary.text == ""
    after, summary, _ = recalibrate_dimensions(
        project, level_id, DimensionsBody(length_m=7.7), actor="user"
    )
    assert _span(only_level(after), "y") == pytest.approx(_span(before, "y") * 2.0)
    assert only_level(after).height == before.height
    with pytest.raises(CalibrationError, match="invraisemblable"):
        recalibrate_dimensions(project, level_id, DimensionsBody(length_m=0.1), actor="user")
    with pytest.raises(CalibrationError, match="au moins une dimension"):
        recalibrate_dimensions(project, level_id, DimensionsBody(), actor="user")


def test_height_with_length_and_width_sets_eaves_and_ridge() -> None:
    project = attic_project()
    level_id = only_level(project).id
    before = only_level(project)
    _theta, length, width = room_axes(project, level_id)
    after, _, dims = recalibrate_dimensions(
        project,
        level_id,
        DimensionsBody(length_m=length * 0.8, width_m=width * 0.9, height_m=before.height * 0.85),
        actor="user",
    )
    level = only_level(after)
    assert level.height == pytest.approx(before.height * 0.85, abs=1e-3)
    for roof in (e for e in level.elements.values() if isinstance(e, RoofPlane)):
        old = before.elements[roof.id]
        assert isinstance(old, RoofPlane)
        assert roof.eave_height == pytest.approx(old.eave_height * 0.85)
        assert math.tan(roof.pitch) != pytest.approx(math.tan(old.pitch))
    assert dims.sz == pytest.approx(0.85, abs=1e-3)


def test_background_stretches_with_an_aligned_room() -> None:
    level = only_level(rect_room_project())
    level = level.model_copy(
        update={"background_transform": {"scale": 0.01, "tx": 1.0, "ty": 2.0, "rotation": 0.0}}
    )
    scaled, notes = apply_axis_scale(level, 0.0, 2.0, 1.5)
    t = scaled.background_transform
    assert t["stretch_x"] == pytest.approx(2.0) and t["stretch_y"] == pytest.approx(1.5)
    assert t["tx"] == pytest.approx(2.0) and t["ty"] == pytest.approx(3.0) and not notes
    tilted = level.model_copy(
        update={"background_transform": {**level.background_transform, "rotation": 0.3}}
    )
    _, notes = apply_axis_scale(tilted, 0.0, 2.0, 1.5)
    assert notes and "non aligné" in notes[0]
