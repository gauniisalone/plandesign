"""R2 : le niveau et la pièce prennent une sorte et un type déduits de la reconnaissance,
modifiables ; l'assistant les lit."""

from __future__ import annotations

import pytest

from openplan.pipeline.intention import describe_room
from openplan.scene.ops import OpError, UpdateLevel, apply_op
from openplan.schemas.elements import Room
from tests.factories import attic_project, only_level, rect_room_project


def test_recognised_attic_names_the_level_grenier() -> None:
    project = attic_project()
    level = only_level(project)
    assert level.kind == "attic" and level.name == "Grenier"
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    assert room.name == "Combles" and room.room_type == "attic"
    text = describe_room(project, level.id)
    assert "Niveau « Grenier » (combles / grenier)" in text and "(attic)" in text


def test_level_kind_and_name_are_editable_and_checked() -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    assert only_level(project).kind is None
    after = apply_op(project, level_id, UpdateLevel(fields={"kind": "upper", "name": "Étage"}))
    assert only_level(after).kind == "upper" and only_level(after).name == "Étage"
    with pytest.raises(OpError):
        apply_op(project, level_id, UpdateLevel(fields={"kind": "grenier"}))
