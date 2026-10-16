"""Graphe de murs : longueurs, emprises, pièces, boucles, édition paramétrique."""

from __future__ import annotations

import math

import pytest

from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas import Level, Point2, Project, Room, Source, Vertex, Wall
from tests.factories import by_name, only_level, rect_room_project

T = 0.15  # épaisseur par défaut de la fixture


@pytest.fixture
def graph(rect_project: Project) -> WallGraph:
    return WallGraph.from_level(only_level(rect_project))


def _wall(graph: WallGraph, name: str) -> Wall:
    return by_name(graph.level, name, Wall)


def test_wall_lengths(graph: WallGraph) -> None:
    assert graph.wall_length(_wall(graph, "W1").id) == pytest.approx(4.0)
    assert graph.wall_length(_wall(graph, "W2").id) == pytest.approx(3.0)


def test_wall_direction_is_unit(graph: WallGraph) -> None:
    assert graph.wall_direction(_wall(graph, "W1").id) == pytest.approx((1.0, 0.0))
    assert graph.wall_direction(_wall(graph, "W2").id) == pytest.approx((0.0, 1.0))


def test_wall_polygon_area_is_length_times_thickness(graph: WallGraph) -> None:
    assert graph.wall_polygon(_wall(graph, "W1").id).area == pytest.approx(4.0 * T)


def test_wall_polygon_offset_sides() -> None:
    project = rect_room_project()
    level = only_level(project)
    w1 = by_name(level, "W1", Wall)  # axe y=0, orienté +x ; normale gauche = +y
    for offset, (ymin, ymax) in {
        "center": (-T / 2, T / 2),
        "left": (0.0, T),
        "right": (-T, 0.0),
    }.items():
        shifted = level.model_copy(
            update={"elements": {**level.elements, w1.id: w1.model_copy(update={"offset": offset})}}
        )
        bounds = WallGraph.from_level(shifted).wall_polygon(w1.id).bounds
        assert bounds[1] == pytest.approx(ymin) and bounds[3] == pytest.approx(ymax)


def test_room_axis_area_and_interior_area(graph: WallGraph) -> None:
    room = by_name(graph.level, "R1", Room)
    assert graph.room_axis_polygon(room.id).area == pytest.approx(12.0)
    assert graph.room_area(room.id) == pytest.approx((4.0 - T) * (3.0 - T))


def test_loop_vertices_are_ordered_and_closed(graph: WallGraph) -> None:
    room = by_name(graph.level, "R1", Room)
    ordered = graph.loop_vertices(room.boundary_walls)
    assert len(ordered) == 4
    positions = [graph.vertex(v).position for v in ordered]
    assert {(p.x, p.y) for p in positions} == {(0, 0), (4, 0), (4, 3), (0, 3)}


def test_loop_vertices_rejects_open_chain(graph: WallGraph) -> None:
    w1, w2, w3 = (_wall(graph, f"W{i}").id for i in (1, 2, 3))
    with pytest.raises(GeometryError, match="referme"):
        graph.loop_vertices([w1, w2, w3])


def test_closed_loops_finds_the_single_room(graph: WallGraph) -> None:
    loops = graph.closed_loops()
    assert len(loops) == 1
    assert set(loops[0]) == set(graph.walls)


def _split_rect_level() -> Level:
    """Rectangle 4 × 3 coupé par un mur médian vertical : deux pièces de 6 m² d'axe."""
    pts = {
        "A": (0, 0),
        "M1": (2, 0),
        "B": (4, 0),
        "C": (4, 3),
        "M2": (2, 3),
        "D": (0, 3),
    }
    vertices = {
        k: Vertex(position=Point2(x=x, y=y), source=Source.USER_DEFINED, confidence=1.0)
        for k, (x, y) in pts.items()
    }
    pairs = [
        ("A", "M1"),
        ("M1", "B"),
        ("B", "C"),
        ("C", "M2"),
        ("M2", "D"),
        ("D", "A"),
        ("M1", "M2"),
    ]
    walls = [
        Wall(
            name=f"{a}-{b}",
            start_vertex=vertices[a].id,
            end_vertex=vertices[b].id,
            source=Source.USER_DEFINED,
            confidence=1.0,
        )
        for a, b in pairs
    ]
    return Level(vertices={v.id: v for v in vertices.values()}, elements={w.id: w for w in walls})


def test_closed_loops_finds_two_rooms_sharing_a_wall() -> None:
    graph = WallGraph.from_level(_split_rect_level())
    loops = graph.closed_loops()
    assert len(loops) == 2
    areas = sorted(abs(_axis_area(graph, loop)) for loop in loops)
    assert areas == pytest.approx([6.0, 6.0])
    shared = by_name(graph.level, "M1-M2", Wall).id
    assert all(shared in loop for loop in loops)


def _axis_area(graph: WallGraph, loop: list) -> float:
    from openplan.geom.polygon import signed_area

    vertex_ids = graph.loop_vertices(loop)
    return signed_area([graph.vertex(v).position for v in vertex_ids])


def test_closed_loops_ignores_dangling_wall(graph: WallGraph) -> None:
    level = graph.level
    a = next(iter(level.vertices.values()))
    tip = Vertex(position=Point2(x=-1, y=-1), source=Source.ESTIMATED, confidence=0.5)
    stub = Wall(start_vertex=a.id, end_vertex=tip.id, source=Source.ESTIMATED, confidence=0.5)
    with_stub = level.model_copy(
        update={
            "vertices": {**level.vertices, tip.id: tip},
            "elements": {**level.elements, stub.id: stub},
        }
    )
    loops = WallGraph.from_level(with_stub).closed_loops()
    assert len(loops) == 1
    assert stub.id not in loops[0]


def test_move_vertex_updates_connected_walls(graph: WallGraph) -> None:
    w1, w2 = _wall(graph, "W1"), _wall(graph, "W2")
    corner = w1.end_vertex  # partagé avec W2
    moved = WallGraph.from_level(graph.move_vertex(corner, Point2(x=5.0, y=0.0)))
    assert moved.wall_length(w1.id) == pytest.approx(5.0)
    assert moved.wall_length(w2.id) == pytest.approx(math.hypot(1.0, 3.0))
    assert graph.wall_length(w1.id) == pytest.approx(4.0), "le graphe d'origine est intact"
    assert moved.vertex(corner).source is graph.vertex(corner).source


@pytest.mark.parametrize(
    ("anchor", "expected_start", "expected_end"),
    [
        ("start", (0.0, 0.0), (3.1, 0.0)),
        ("end", (0.9, 0.0), (4.0, 0.0)),
        ("center", (0.45, 0.0), (3.55, 0.0)),
    ],
)
def test_set_wall_length_with_anchor(
    graph: WallGraph,
    anchor: str,
    expected_start: tuple[float, float],
    expected_end: tuple[float, float],
) -> None:
    w1 = _wall(graph, "W1")
    new = WallGraph.from_level(graph.set_wall_length(w1.id, 3.1, anchor))  # type: ignore[arg-type]
    a, b = new.wall_axis(w1.id)
    assert (a.x, a.y) == pytest.approx(expected_start)
    assert (b.x, b.y) == pytest.approx(expected_end)
    assert new.wall_length(w1.id) == pytest.approx(3.1)


def test_set_wall_length_rejects_non_positive(graph: WallGraph) -> None:
    with pytest.raises(GeometryError, match="positive"):
        graph.set_wall_length(_wall(graph, "W1").id, 0.0)


def test_unknown_references_raise(graph: WallGraph) -> None:
    from uuid import uuid4

    with pytest.raises(GeometryError, match="mur inconnu"):
        graph.wall_length(uuid4())
    with pytest.raises(GeometryError, match="sommet inconnu"):
        graph.vertex(uuid4())
    with pytest.raises(GeometryError, match="pièce inconnue"):
        graph.room_area(uuid4())


def test_degenerate_wall_has_no_direction() -> None:
    a = Vertex(position=Point2(x=1, y=1), source=Source.ESTIMATED, confidence=0.5)
    b = Vertex(position=Point2(x=1, y=1), source=Source.ESTIMATED, confidence=0.5)
    wall = Wall(start_vertex=a.id, end_vertex=b.id, source=Source.ESTIMATED, confidence=0.5)
    level = Level(vertices={a.id: a, b.id: b}, elements={wall.id: wall})
    with pytest.raises(GeometryError, match="nulle"):
        WallGraph.from_level(level).wall_direction(wall.id)
