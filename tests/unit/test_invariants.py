"""Invariants structurels : références, fermeture, confiance, mesures."""

from __future__ import annotations

from uuid import uuid4

from openplan.schemas import (
    Floor,
    Level,
    Measurement,
    MeasurementKind,
    Opening,
    Project,
    Room,
    Source,
    Wall,
)
from openplan.validation.invariants import check_invariants, validate_invariants, wall_length
from tests.factories import by_name, only_level


def _replace(project: Project, level: Level, **update: object) -> Project:
    return project.model_copy(update={"levels": {level.id: level.model_copy(update=update)}})


def _types(project: Project) -> list[str]:
    return sorted(v.type for v in check_invariants(project))


def test_factory_project_is_valid(rect_project: Project) -> None:
    report = validate_invariants(rect_project)
    assert report.valid
    assert report.violations == []
    assert report.score == 1.0


def test_wall_length_is_euclidean(rect_project: Project) -> None:
    level = only_level(rect_project)
    assert wall_length(by_name(level, "W1", Wall), level.vertices) == 4.0
    assert wall_length(by_name(level, "W2", Wall), level.vertices) == 3.0


def test_orphan_wall_vertex(rect_project: Project) -> None:
    level = only_level(rect_project)
    w1 = by_name(level, "W1", Wall)
    broken = w1.model_copy(update={"end_vertex": uuid4()})
    project = _replace(rect_project, level, elements={**level.elements, w1.id: broken})
    types = _types(project)
    assert "orphan_wall_vertex" in types
    assert not validate_invariants(project).valid


def test_opening_beyond_wall_length(rect_project: Project) -> None:
    level = only_level(rect_project)
    door = by_name(level, "door", Opening)
    too_far = door.model_copy(update={"offset": 3.5})  # 3.5 + 0.83 > 4.0
    project = _replace(rect_project, level, elements={**level.elements, door.id: too_far})
    assert "hosted_element_exceeds_wall" in _types(project)


def test_openings_overlap_is_flagged_not_fatal(rect_project: Project) -> None:
    level = only_level(rect_project)
    door = by_name(level, "door", Opening)  # décalage 0,5, largeur 0,83
    second = door.model_copy(update={"id": uuid4(), "offset": 1.0, "name": "porte 2"})
    project = _replace(rect_project, level, elements={**level.elements, second.id: second})
    report = validate_invariants(project)
    overlap = [v for v in report.violations if v.type == "openings_overlap"]
    assert len(overlap) == 1 and overlap[0].severity == "medium"
    assert set(overlap[0].element_ids) == {door.id, second.id}
    assert report.valid, "chevauchement signalé, édition permise"
    apart = second.model_copy(update={"offset": 1.33})  # jointif : accepté
    assert "openings_overlap" not in _types(
        _replace(rect_project, level, elements={**level.elements, apart.id: apart})
    )


def test_opening_on_missing_wall(rect_project: Project) -> None:
    level = only_level(rect_project)
    door = by_name(level, "door", Opening)
    lost = door.model_copy(update={"host_wall": uuid4()})
    project = _replace(rect_project, level, elements={**level.elements, door.id: lost})
    assert "orphan_host_wall" in _types(project)


def test_room_not_closed(rect_project: Project) -> None:
    level = only_level(rect_project)
    room = by_name(level, "R1", Room)
    w1, w2, w3, w4 = (by_name(level, f"W{i}", Wall) for i in range(1, 5))
    # W1 et W3 sont opposés : ils ne partagent aucun sommet.
    open_room = room.model_copy(update={"boundary_walls": [w1.id, w3.id, w2.id, w4.id]})
    project = _replace(rect_project, level, elements={**level.elements, room.id: open_room})
    assert "room_not_closed" in _types(project)


def test_room_with_unknown_wall(rect_project: Project) -> None:
    level = only_level(rect_project)
    room = by_name(level, "R1", Room)
    bad = room.model_copy(update={"boundary_walls": [*room.boundary_walls[:3], uuid4()]})
    project = _replace(rect_project, level, elements={**level.elements, room.id: bad})
    assert "orphan_room_wall" in _types(project)


def test_derived_floor_with_polygon_is_inconsistent(rect_project: Project) -> None:
    level = only_level(rect_project)
    floor = by_name(level, "floor", Floor)
    bad = floor.model_copy(update={"polygon": []})
    project = _replace(rect_project, level, elements={**level.elements, floor.id: bad})
    assert "surface_derived_mismatch" in _types(project)
    assert validate_invariants(project).valid, "sévérité medium : ne bloque pas"


def test_full_confidence_requires_authoritative_source(rect_project: Project) -> None:
    level = only_level(rect_project)
    w2 = by_name(level, "W2", Wall)
    overconfident = w2.model_copy(update={"confidence": 1.0})  # source estimated
    project = _replace(rect_project, level, elements={**level.elements, w2.id: overconfident})
    assert "confidence_without_authority" in _types(project)


def test_measurement_target_must_exist(rect_project: Project) -> None:
    dangling = Measurement(kind=MeasurementKind.WALL_LENGTH, value=2.0, element_id=uuid4())
    project = rect_project.model_copy(
        update={"measurements": {**rect_project.measurements, dangling.id: dangling}}
    )
    assert "measurement_target_missing" in _types(project)


def test_source_enum_in_message(rect_project: Project) -> None:
    level = only_level(rect_project)
    w2 = by_name(level, "W2", Wall)
    project = _replace(
        rect_project,
        level,
        elements={**level.elements, w2.id: w2.model_copy(update={"confidence": 1.0})},
    )
    (violation,) = check_invariants(project)
    assert Source.ESTIMATED.value in violation.message
