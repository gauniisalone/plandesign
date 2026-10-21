"""KAN-29 : murs obliques (pan coupé) — pièces, ouvertures, jonctions 3D, exports."""

from __future__ import annotations

import io
import math
from uuid import UUID

import ezdxf
import pytest
import trimesh
from shapely.geometry import Polygon
from shapely.ops import unary_union

from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.assistant_openings import _misfit
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.editing import InvalidEditError, edit
from openplan.pipeline.export import render_dxf, render_pdf, render_svg
from openplan.pipeline.scene3d import bake_level, build_scene, glb_bytes, scene_volume
from openplan.scene.factory import new_project
from openplan.scene.ops import AddOpening, AddWall, DetectRooms, MoveVertex, apply_op
from openplan.schemas import Opening, OpeningKind, Point2, Project, Room, Source, Wall
from tests.factories import CUT_CORNER, by_name, cut_corner_project, only_level

T = 0.15
H = T / 2
ROOT2 = math.sqrt(2.0)


def _ring(points: list[tuple[float, float]], half: float = H) -> Polygon:
    """Emprise idéale d'une boucle de murs centrés : contour extérieur moins contour intérieur,
    angles en onglet (sans trou ni recouvrement)."""
    axis = Polygon(points)
    outer = axis.buffer(half, join_style="mitre", mitre_limit=10.0)
    inner = axis.buffer(-half, join_style="mitre", mitre_limit=10.0)
    return outer.difference(inner)


def _walls(project: Project) -> dict[str, Wall]:
    level = only_level(project)
    return {w.name: w for w in level.elements.values() if isinstance(w, Wall) and w.name}


# ---------------------------------------------------------------------- MH3 : pièces
def test_detect_rooms_finds_the_cut_corner_room_with_exact_area() -> None:
    project = cut_corner_project(window=False)
    level = only_level(project)
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    assert len(rooms) == 1 and len(rooms[0].boundary_walls) == 5
    # Surface intérieure exacte : polygone des axes (12 − 0,5 m²) rentré d'une demi-épaisseur,
    # A' = A − P·d + d²·Σ tan(θ/2), θ = angles de virage (3 × 90°, 2 × 45°).
    perimeter = 4 + 2 + ROOT2 + 3 + 3
    turns = 3 * math.tan(math.radians(45)) + 2 * math.tan(math.radians(22.5))
    expected = 11.5 - perimeter * H + H * H * turns
    area = compute_derived(level).rooms[rooms[0].id].area_m2
    assert area == pytest.approx(expected, rel=0.01)
    assert area == pytest.approx(expected, rel=1e-9), "au-delà du 1 % demandé : exacte"


# ---------------------------------------------------------------------- MH2 : édition
def test_moving_an_end_off_axis_drags_both_connected_walls() -> None:
    project = cut_corner_project(window=False)
    lid = only_level(project).id
    walls = _walls(project)
    corner = walls["C3"].end_vertex  # (3, 3), commun à C3 et C4
    to = Point2(x=3.3, y=2.6)
    after, report = edit(project, lid, MoveVertex(vertex_id=corner, to=to))
    assert report.valid
    graph = WallGraph.from_level(only_level(after))
    c3, c4 = walls["C3"].id, walls["C4"].id
    assert graph.wall_axis(c3)[1] == to and graph.wall_axis(c4)[0] == to
    assert graph.wall_length(c3) == pytest.approx(math.hypot(0.7, 0.6))
    assert graph.wall_length(c4) == pytest.approx(math.hypot(3.3, 0.4))
    # La pièce suit : sa surface est recalculée sur les nouveaux axes.
    room = next(e for e in only_level(after).elements.values() if isinstance(e, Room))
    moved = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (3.3, 2.6), (0.0, 3.0)]
    inner = Polygon(moved).buffer(-H, join_style="mitre", mitre_limit=10.0)
    assert compute_derived(only_level(after)).rooms[room.id].area_m2 == pytest.approx(
        inner.area, rel=1e-6
    )


# ---------------------------------------------------------------------- MH4 : ouvertures
def test_window_on_the_oblique_wall_stays_in_its_wall() -> None:
    project = cut_corner_project()
    level = only_level(project)
    c3 = _walls(project)["C3"]
    window = next(e for e in level.elements.values() if isinstance(e, Opening))
    assert window.host_wall == c3.id
    bake = bake_level(project, level.id)
    baked = next(o for o in bake.openings if o.element_id == window.id)
    # Au milieu du mur oblique, dans son axe (de (4, 2) vers (3, 3)).
    assert (baked.center.x, baked.center.y) == pytest.approx((3.5, 2.5))
    assert baked.direction == pytest.approx((-1 / ROOT2, 1 / ROOT2))
    footprint = WallGraph.from_level(level).wall_footprint(c3.id)
    pieces = [p for p in bake.prisms if p.element_id == c3.id]
    assert len(pieces) == 4, "avant, allège, linteau, après"
    for piece in pieces:
        shape = Polygon([(q.x, q.y) for q in piece.exterior])
        assert footprint.buffer(1e-9).contains(shape)


def test_opening_beyond_the_oblique_wall_is_refused() -> None:
    project = cut_corner_project(window=False)
    lid = only_level(project).id
    c3 = _walls(project)["C3"]
    ok, report = edit(
        project,
        lid,
        AddOpening(host_wall=c3.id, kind=OpeningKind.DOOR, offset=0.2, width=0.9, height=2.0),
    )
    assert report.valid
    assert any(isinstance(e, Opening) for e in only_level(ok).elements.values())
    with pytest.raises(InvalidEditError, match="dépasse le mur"):
        edit(
            project,
            lid,
            AddOpening(host_wall=c3.id, kind=OpeningKind.DOOR, offset=0.6, width=0.9, height=2.0),
        )


def _triangle() -> Project:
    """Triangle rectangle isocèle : deux angles aigus de 45°."""
    project = new_project("Triangle")
    lid = only_level(project).id
    pts = [Point2(x=0, y=0), Point2(x=4, y=0), Point2(x=0, y=4)]
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        project = apply_op(project, lid, AddWall(start=a, end=b, thickness=T))
    return apply_op(project, lid, DetectRooms())


def test_corner_reach_follows_the_angle() -> None:
    cut = cut_corner_project(window=False)
    graph = WallGraph.from_level(only_level(cut))
    walls = _walls(cut)
    assert graph.corner_reach(walls["C1"].id, at_start=True) == pytest.approx(H), "angle droit"
    assert graph.corner_reach(walls["C3"].id, at_start=True) == pytest.approx(
        H * math.tan(math.radians(22.5))
    ), "pan coupé à 135° : l'angle mange moins"
    tri = _triangle()
    tgraph = WallGraph.from_level(only_level(tri))
    base = next(
        w.id
        for w in only_level(tri).elements.values()
        if isinstance(w, Wall)
        and only_level(tri).vertices[w.start_vertex].position == Point2(x=4, y=0)
    )
    assert tgraph.corner_reach(base, at_start=True) == pytest.approx(
        H / math.tan(math.radians(22.5))
    ), "angle aigu de 45° : l'angle mange plus"


def test_assistant_keeps_openings_out_of_an_acute_corner() -> None:
    """Contrainte existante des ouvertures (jambage d'angle) réutilisée : à 45°, la marge vaut
    ce que l'angle mange (0,18 m) plus le jeu, et non plus la seule demi-épaisseur."""
    tri = _triangle()
    level = only_level(tri)
    hyp = next(
        w
        for w in level.elements.values()
        if isinstance(w, Wall) and level.vertices[w.start_vertex].position == Point2(x=4, y=0)
    )
    o = Opening(
        kind=OpeningKind.WINDOW,
        host_wall=hyp.id,
        offset=1.0,
        width=0.8,
        height=1.0,
        sill_height=1.0,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )
    graph = WallGraph.from_level(level)
    assert _misfit(graph, level, o, 0.15, 0.8) is not None, "mord sur l'angle aigu"
    assert _misfit(graph, level, o, 0.25, 0.8) is None


# ---------------------------------------------------------------------- MH5 : 3D
def _wall_shapes(project: Project) -> list[Polygon]:
    bake = bake_level(project, only_level(project).id)
    return [Polygon([(q.x, q.y) for q in p.exterior]) for p in bake.prisms if p.kind == "wall"]


@pytest.mark.parametrize(
    "points",
    [
        CUT_CORNER,
        [(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)],
        [(0.0, 0.0), (4.0, 0.0), (0.0, 4.0)],
        [(0.0, 0.0), (5.0, 0.0), (5.0, 3.0), (2.0, 4.2), (0.0, 3.0)],
    ],
    ids=["pan-coupe", "rectangle", "triangle-aigu", "pentagone"],
)
def test_bake_joints_are_closed_without_overlap(points: list[tuple[float, float]]) -> None:
    project = new_project("Jonctions")
    lid = only_level(project).id
    pts = [Point2(x=x, y=y) for x, y in points]
    for a, b in zip(pts, pts[1:] + pts[:1], strict=True):
        project = apply_op(project, lid, AddWall(start=a, end=b, thickness=T))
    shapes = _wall_shapes(project)
    union = unary_union(shapes)
    ideal = _ring(points)
    assert sum(s.area for s in shapes) == pytest.approx(union.area, abs=1e-9), "chevauchement"
    assert union.symmetric_difference(ideal).area == pytest.approx(0.0, abs=1e-9), "trou"
    assert isinstance(union, Polygon) and len(union.interiors) == 1


def test_t_junction_on_an_oblique_wall_is_closed() -> None:
    """Trois murs à un sommet (un oblique arrive au milieu d'un mur droit) : le sommet ferme
    le moyeu, rien ne manque ni ne se recouvre."""
    project = new_project("Té")
    lid = only_level(project).id
    a, m, b, c = Point2(x=0, y=0), Point2(x=2, y=0), Point2(x=4, y=0), Point2(x=3, y=2)
    for start, end in ((a, m), (m, b), (m, c)):
        project = apply_op(project, lid, AddWall(start=start, end=end, thickness=T))
    shapes = _wall_shapes(project)
    union = unary_union(shapes)
    assert sum(s.area for s in shapes) == pytest.approx(union.area, abs=1e-9)
    from shapely.geometry import LineString

    # Idéal : le mur traversant entier, plus la branche (son bout droit au sommet est caché
    # dans l'épaisseur du mur traversant).
    through = LineString([(0, 0), (4, 0)]).buffer(H, cap_style="flat")
    branch = LineString([(2, 0), (3, 2)]).buffer(H, cap_style="flat")
    expected = unary_union([through, branch])
    assert union.symmetric_difference(expected).area == pytest.approx(0.0, abs=1e-9)


def test_glb_volume_of_the_cut_corner_room_is_exact() -> None:
    project = cut_corner_project()
    level = only_level(project)
    window = next(e for e in level.elements.values() if isinstance(e, Opening))
    expected = _ring(CUT_CORNER).area * level.height - window.width * window.height * T
    scene = build_scene(bake_level(project, level.id))
    walls = sum(
        abs(scene.geometry[str(w.id)].volume)
        for w in level.elements.values()
        if isinstance(w, Wall)
    )
    assert walls == pytest.approx(expected, rel=1e-6)
    loaded = trimesh.load(io.BytesIO(glb_bytes(project, level.id)), file_type="glb")
    assert scene_volume(loaded) == pytest.approx(scene_volume(scene), rel=1e-4)


# ---------------------------------------------------------------------- MH6 : exports
def test_exports_draw_the_oblique_wall_with_its_dimension_along_it() -> None:
    project = cut_corner_project()
    level = only_level(project)
    c3 = _walls(project)["C3"]
    svg = render_svg(project, level.id)
    assert svg.count('class="wall"') == 5
    assert ">1,41 m</text>" in svg and 'transform="rotate(45.0 ' in svg
    assert svg.count("transform=") == 1, "les murs droits gardent leur cote horizontale"
    pdf = render_pdf(project, level.id)
    assert b"(1,41 m)" in pdf and b"(4,00 m)" in pdf
    doc = ezdxf.read(io.StringIO(render_dxf(project, level.id)))
    texts = {e.dxf.text: e for e in doc.modelspace().query('TEXT[layer=="COTES"]')}
    # KAN-71 : le DXF, format technique, garde le point décimal.
    assert texts["1.41 m"].dxf.rotation == pytest.approx(-45.0)
    walls = list(doc.modelspace().query('LWPOLYLINE[layer=="MURS"]'))
    assert len(walls) == 5
    oblique = WallGraph.from_level(level).wall_footprint(c3.id)
    drawn = [Polygon([(x, y) for x, y, *_ in w.get_points()]) for w in walls]
    assert any(d.symmetric_difference(oblique).area < 1e-9 for d in drawn)


# ---------------------------------------------------------------------- MH7 : existant
def test_orthogonal_walls_keep_their_area_and_volume(rect_project: Project) -> None:
    """Angle droit : l'onglet ne change ni l'aire (L × e) ni le volume d'un mur."""
    level = only_level(rect_project)
    graph = WallGraph.from_level(level)
    footprints = graph.wall_footprints()
    for wid in graph.walls:
        assert footprints[wid].area == pytest.approx(graph.wall_polygon(wid).area)
    ids: set[UUID] = set(graph.walls)
    assert set(footprints) == ids
    w1 = by_name(level, "W1", Wall)
    assert graph.corner_reach(w1.id, at_start=True) == pytest.approx(H)
