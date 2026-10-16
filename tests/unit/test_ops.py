"""Commandes d'édition : création, modification, suppression en cascade, détection de pièces."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from openplan.geom.wallgraph import WallGraph
from openplan.scene.factory import new_project
from openplan.scene.ops import (
    AddOpening,
    AddWall,
    DetectRooms,
    MoveVertex,
    OpError,
    RemoveElement,
    RemoveVertex,
    SetWallLength,
    UpdateElement,
    UpdateLevel,
    UpdateVertex,
    apply_op,
)
from openplan.schemas import (
    Ceiling,
    Floor,
    Opening,
    OpeningKind,
    Point2,
    Project,
    Room,
    Source,
    Wall,
)
from tests.factories import by_name, only_level


def _level_id(project: Project) -> UUID:
    return only_level(project).id


def test_add_walls_with_points_shares_coincident_vertices() -> None:
    project = new_project("p")
    lid = only_level(project).id
    corners = [Point2(x=0, y=0), Point2(x=4, y=0), Point2(x=4, y=3), Point2(x=0, y=3)]
    for i in range(4):
        project = apply_op(project, lid, AddWall(start=corners[i], end=corners[(i + 1) % 4]))
    level = only_level(project)
    assert len(level.vertices) == 4, "les points coïncidents réutilisent le sommet existant"
    assert sum(isinstance(e, Wall) for e in level.elements.values()) == 4
    assert len(project.history) == 4 and project.history_cursor == 4
    assert all(v.source is Source.USER_DEFINED for v in level.vertices.values())


def test_add_wall_by_vertex_id_and_errors(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    level = only_level(rect_project)
    a, b = list(level.vertices)[:2]
    after = apply_op(rect_project, lid, AddWall(start=a, end=Point2(x=9, y=9)))
    assert len(only_level(after).vertices) == 5
    with pytest.raises(OpError, match="sommet inconnu"):
        apply_op(rect_project, lid, AddWall(start=uuid4(), end=b))
    with pytest.raises(OpError, match="distincts"):
        apply_op(rect_project, lid, AddWall(start=a, end=a))
    with pytest.raises(OpError, match="niveau inconnu"):
        apply_op(rect_project, uuid4(), AddWall(start=a, end=b))


def test_detect_rooms_creates_room_floor_ceiling_once() -> None:
    project = new_project("p")
    lid = only_level(project).id
    corners = [Point2(x=0, y=0), Point2(x=4, y=0), Point2(x=4, y=3), Point2(x=0, y=3)]
    for i in range(4):
        project = apply_op(project, lid, AddWall(start=corners[i], end=corners[(i + 1) % 4]))
    project = apply_op(project, lid, DetectRooms())
    level = only_level(project)
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    assert len(rooms) == 1 and rooms[0].source is Source.INFERRED
    assert sum(isinstance(e, Floor) for e in level.elements.values()) == 1
    assert sum(isinstance(e, Ceiling) for e in level.elements.values()) == 1
    again = apply_op(project, lid, DetectRooms())
    assert again is project, "rien à détecter : pas d'entrée d'historique"


def test_move_vertex_by_user_marks_provenance(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    level = only_level(rect_project)
    w1 = by_name(level, "W1", Wall)
    corner = w1.end_vertex
    after = apply_op(rect_project, lid, MoveVertex(vertex_id=corner, to=Point2(x=4.5, y=0)))
    new_level = only_level(after)
    assert new_level.vertices[corner].source is Source.USER_DEFINED
    assert by_name(new_level, "W1", Wall).source is Source.USER_DEFINED, "mesure caduque"
    assert by_name(new_level, "W2", Wall).source is Source.ESTIMATED, "inchangé"
    assert WallGraph.from_level(new_level).wall_length(w1.id) == pytest.approx(4.5)


def test_move_vertex_by_pipeline_keeps_provenance(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    level = only_level(rect_project)
    w2 = by_name(level, "W2", Wall)
    corner = w2.end_vertex  # partagé avec W3 (estimated)
    after = apply_op(
        rect_project,
        lid,
        MoveVertex(vertex_id=corner, to=Point2(x=4, y=3.2)),
        actor="pipeline:test",
    )
    assert only_level(after).vertices[corner].source is level.vertices[corner].source


def test_set_wall_length(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    w2 = by_name(only_level(rect_project), "W2", Wall)
    after = apply_op(rect_project, lid, SetWallLength(wall_id=w2.id, length=2.5, anchor="end"))
    assert WallGraph.from_level(only_level(after)).wall_length(w2.id) == pytest.approx(2.5)
    assert after.history[-1].label == "Régler la longueur d'un mur"


def test_add_opening_and_validation(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    w2 = by_name(only_level(rect_project), "W2", Wall)
    after = apply_op(
        rect_project,
        lid,
        AddOpening(host_wall=w2.id, kind=OpeningKind.WINDOW, offset=1.0, width=0.8, height=1.0),
    )
    assert sum(isinstance(e, Opening) for e in only_level(after).elements.values()) == 3
    with pytest.raises(OpError, match="mur hôte inconnu"):
        apply_op(
            rect_project,
            lid,
            AddOpening(host_wall=uuid4(), kind=OpeningKind.DOOR, offset=0, width=1, height=2),
        )


def test_update_element_validates_and_protects_identity(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    w2 = by_name(only_level(rect_project), "W2", Wall)
    after = apply_op(
        rect_project,
        lid,
        UpdateElement(element_id=w2.id, fields={"thickness": 0.3, "locked": True}),
    )
    updated = by_name(only_level(after), "W2", Wall)
    assert updated.thickness == 0.3 and updated.locked
    with pytest.raises(OpError, match="non modifiables"):
        apply_op(rect_project, lid, UpdateElement(element_id=w2.id, fields={"type": "room"}))
    with pytest.raises(OpError):
        apply_op(rect_project, lid, UpdateElement(element_id=w2.id, fields={"thickness": -1}))
    with pytest.raises(OpError, match="élément inconnu"):
        apply_op(rect_project, lid, UpdateElement(element_id=uuid4(), fields={}))


def test_update_vertex(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    vid = next(iter(only_level(rect_project).vertices))
    after = apply_op(rect_project, lid, UpdateVertex(vertex_id=vid, fields={"locked": True}))
    assert only_level(after).vertices[vid].locked
    with pytest.raises(OpError, match="non modifiables"):
        apply_op(rect_project, lid, UpdateVertex(vertex_id=vid, fields={"id": str(uuid4())}))
    with pytest.raises(OpError, match="sommet inconnu"):
        apply_op(rect_project, lid, UpdateVertex(vertex_id=uuid4(), fields={}))


def test_remove_wall_cascades_to_openings_rooms_and_surfaces(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    level = only_level(rect_project)
    w1 = by_name(level, "W1", Wall)  # porte la porte, fait partie de R1
    after = apply_op(rect_project, lid, RemoveElement(element_id=w1.id))
    remaining = only_level(after).elements
    assert w1.id not in remaining
    assert not any(e.name == "door" for e in remaining.values())
    assert not any(isinstance(e, Room | Floor) for e in remaining.values())
    assert any(e.name == "window" for e in remaining.values()), "fenêtre sur W3 conservée"
    with pytest.raises(OpError, match="élément inconnu"):
        apply_op(rect_project, lid, RemoveElement(element_id=uuid4()))


def test_remove_vertex_cascades_to_walls(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    level = only_level(rect_project)
    w1 = by_name(level, "W1", Wall)
    after = apply_op(rect_project, lid, RemoveVertex(vertex_id=w1.start_vertex))
    new_level = only_level(after)
    assert len(new_level.vertices) == 3
    assert sum(isinstance(e, Wall) for e in new_level.elements.values()) == 2
    with pytest.raises(OpError, match="sommet inconnu"):
        apply_op(rect_project, lid, RemoveVertex(vertex_id=uuid4()))


def test_update_level(rect_project: Project) -> None:
    lid = _level_id(rect_project)
    after = apply_op(
        rect_project, lid, UpdateLevel(fields={"height": 2.7, "height_source": "measured"})
    )
    assert only_level(after).height == 2.7
    with pytest.raises(OpError, match="non modifiables"):
        apply_op(rect_project, lid, UpdateLevel(fields={"vertices": {}}))
    with pytest.raises(OpError):
        apply_op(rect_project, lid, UpdateLevel(fields={"height": -1}))
