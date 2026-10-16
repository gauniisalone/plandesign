"""Format de scène : aller-retour JSON, discriminant, invariants de construction."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from openplan.schemas import (
    SOURCE_PRIORITY,
    Element,
    Level,
    Measurement,
    MeasurementKind,
    Opening,
    OpeningKind,
    Point2,
    Project,
    Source,
    Swing,
    Uncertainty,
    Vertex,
    Wall,
)
from tests.factories import by_name, only_level


def test_json_round_trip_is_lossless(rect_project: Project) -> None:
    restored = Project.model_validate_json(rect_project.model_dump_json())
    assert restored == rect_project


def test_dict_round_trip_in_json_mode(rect_project: Project) -> None:
    restored = Project.model_validate(rect_project.model_dump(mode="json"))
    assert restored == rect_project


def test_element_union_is_discriminated_by_type() -> None:
    a, b = uuid4(), uuid4()
    payload = {
        "type": "wall",
        "start_vertex": str(a),
        "end_vertex": str(b),
        "source": "estimated",
        "confidence": 0.5,
    }
    element = TypeAdapter(Element).validate_python(payload)
    assert isinstance(element, Wall)
    assert element.thickness == 0.15


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"[Ee]xtra"):
        Vertex(position=Point2(x=0, y=0), source=Source.MEASURED, confidence=1.0, colour="red")  # type: ignore[call-arg]


@pytest.mark.parametrize("confidence", [-0.1, 1.01])
def test_confidence_is_bounded(confidence: float) -> None:
    with pytest.raises(ValidationError):
        Vertex(position=Point2(x=0, y=0), source=Source.MEASURED, confidence=confidence)


def test_wall_needs_two_distinct_vertices() -> None:
    v = uuid4()
    with pytest.raises(ValidationError, match="distincts"):
        Wall(start_vertex=v, end_vertex=v, source=Source.ESTIMATED, confidence=0.5)


def test_swing_is_only_for_doors() -> None:
    with pytest.raises(ValidationError, match="porte"):
        Opening(
            kind=OpeningKind.WINDOW,
            host_wall=uuid4(),
            offset=0.0,
            width=1.0,
            height=1.0,
            swing=Swing(hinge="left", direction="inward"),
            source=Source.OBSERVED,
            confidence=0.5,
        )


def test_measurement_targets_exactly_one_thing() -> None:
    with pytest.raises(ValidationError, match="exactement"):
        Measurement(kind=MeasurementKind.WALL_LENGTH, value=3.1)
    with pytest.raises(ValidationError, match="exactement"):
        Measurement(
            kind=MeasurementKind.WALL_LENGTH,
            value=3.1,
            element_id=uuid4(),
            vertex_ids=(uuid4(), uuid4()),
        )


def test_level_rejects_key_id_mismatch() -> None:
    vertex = Vertex(position=Point2(x=0, y=0), source=Source.MEASURED, confidence=1.0)
    with pytest.raises(ValidationError, match="ne correspond pas"):
        Level(vertices={uuid4(): vertex})


def test_history_cursor_cannot_exceed_history() -> None:
    with pytest.raises(ValidationError, match="dépasse"):
        Project(name="x", history_cursor=1)


def test_uncertainty_interval_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="désordonné"):
        Uncertainty(interval=(1.0, 0.0))


def test_source_priority_puts_measured_first_and_generated_last() -> None:
    assert SOURCE_PRIORITY[Source.MEASURED] == max(SOURCE_PRIORITY.values())
    assert SOURCE_PRIORITY[Source.GENERATED] == min(SOURCE_PRIORITY.values())
    assert SOURCE_PRIORITY[Source.USER_DEFINED] > SOURCE_PRIORITY[Source.OBSERVED]


def test_factory_builds_expected_topology(rect_project: Project) -> None:
    level = only_level(rect_project)
    assert len(level.vertices) == 4
    w1 = by_name(level, "W1", Wall)
    door = by_name(level, "door", Opening)
    assert door.host_wall == w1.id
    assert w1.source is Source.MEASURED
    assert len(rect_project.measurements) == 1
