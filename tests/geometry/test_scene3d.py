"""Scène 3D : découpe des murs, bake, volumes trimesh, GLB relu."""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
import trimesh

from openplan.geom.wallgraph import GeometryError
from openplan.pipeline.scene3d import (
    SLAB,
    WallSegment,
    bake_level,
    build_scene,
    export_bake,
    export_glb,
    glb_bytes,
    scene_volume,
    wall_segments,
)
from openplan.schemas import Floor, Opening, OpeningKind, Project, Source, Wall
from tests.factories import by_name, only_level

T = 0.15
H = 2.5


def _opening(offset: float, width: float, height: float, sill: float = 0.0) -> Opening:
    return Opening(
        kind=OpeningKind.WINDOW if sill > 0 else OpeningKind.DOOR,
        host_wall=uuid4(),
        offset=offset,
        width=width,
        height=height,
        sill_height=sill,
        source=Source.USER_DEFINED,
        confidence=1.0,
    )


def test_wall_segments_without_openings() -> None:
    assert wall_segments(4.0, H, []) == [WallSegment(0.0, 4.0, 0.0, H)]


def test_wall_segments_door_and_window() -> None:
    door = _opening(0.5, 0.83, 2.04)
    segs = wall_segments(4.0, H, [door])
    assert segs == [
        WallSegment(0.0, 0.5, 0.0, H),
        WallSegment(0.5, 1.33, 2.04, H),
        WallSegment(1.33, 4.0, 0.0, H),
    ]
    window = _opening(1.2, 1.2, 1.25, sill=0.9)
    segs = wall_segments(4.0, H, [window])
    assert segs == [
        WallSegment(0.0, 1.2, 0.0, H),
        WallSegment(1.2, 2.4, 0.0, 0.9),
        WallSegment(1.2, 2.4, 2.15, H),
        WallSegment(2.4, 4.0, 0.0, H),
    ]


def test_wall_segments_overlap_and_out_of_range() -> None:
    a, b = _opening(0.5, 1.0, 2.0), _opening(1.0, 1.0, 2.0)  # chevauchement 1.0–1.5
    segs = wall_segments(4.0, H, [a, b])
    assert [(s.s0, s.s1) for s in segs] == [(0.0, 0.5), (0.5, 1.5), (1.5, 2.0), (2.0, 4.0)]
    beyond = _opening(5.0, 1.0, 2.0)
    assert wall_segments(4.0, H, [beyond]) == [WallSegment(0.0, 4.0, 0.0, H)]
    tall = _opening(1.0, 1.0, 5.0)  # plus haut que le mur : pas de linteau
    assert wall_segments(4.0, H, [tall]) == [
        WallSegment(0.0, 1.0, 0.0, H),
        WallSegment(2.0, 4.0, 0.0, H),
    ]


def test_bake_level_counts_and_bounds(rect_project: Project) -> None:
    level = only_level(rect_project)
    bake = bake_level(rect_project, level.id)
    walls = [p for p in bake.prisms if p.kind == "wall"]
    floors = [p for p in bake.prisms if p.kind == "floor"]
    # W1 : porte → 3 morceaux ; W3 : fenêtre → 4 morceaux ; W2, W4 : 1 chacun.
    assert len(walls) == 3 + 1 + 4 + 1
    assert len(floors) == 1 and bake.prisms and bake.level_id == level.id
    assert bake.bounds_min is not None and bake.bounds_max is not None
    assert bake.bounds_min.z == pytest.approx(-SLAB) and bake.bounds_max.z == pytest.approx(H)
    w1 = by_name(level, "W1", Wall)
    assert {p.name for p in walls if p.element_id == w1.id} == {
        str(w1.id),
        f"{w1.id}#1",
        f"{w1.id}#2",
    }
    assert all(p.color == (46, 125, 50) for p in walls if p.element_id == w1.id), "mesuré = vert"
    with pytest.raises(GeometryError, match="niveau inconnu"):
        bake_level(rect_project, uuid4())


def test_build_scene_volumes_match_analytic(rect_project: Project) -> None:
    level = only_level(rect_project)
    scene = build_scene(bake_level(rect_project, level.id))
    w1, w3 = by_name(level, "W1", Wall), by_name(level, "W3", Wall)
    door, window = by_name(level, "door", Opening), by_name(level, "window", Opening)
    expected_w1 = 4.0 * T * H - door.width * door.height * T
    expected_w3 = 4.0 * T * H - window.width * window.height * T
    assert abs(scene.geometry[str(w1.id)].volume) == pytest.approx(expected_w1, rel=1e-6)
    assert abs(scene.geometry[str(w3.id)].volume) == pytest.approx(expected_w3, rel=1e-6)
    floor = by_name(level, "floor", Floor)
    assert abs(scene.geometry[str(floor.id)].volume) == pytest.approx(
        (4 - T) * (3 - T) * SLAB, rel=1e-6
    )
    assert set(scene.graph.nodes_geometry) >= {str(w1.id), str(w3.id), str(floor.id)}
    assert scene_volume(scene) > 0


def test_glb_round_trip_keeps_node_names(rect_project: Project, tmp_path: Path) -> None:
    level = only_level(rect_project)
    data = glb_bytes(rect_project, level.id)
    assert data[:4] == b"glTF"
    loaded = trimesh.load(io.BytesIO(data), file_type="glb")
    w1 = by_name(level, "W1", Wall)
    assert str(w1.id) in loaded.graph.nodes_geometry
    assert scene_volume(loaded) == pytest.approx(
        scene_volume(build_scene(bake_level(rect_project, level.id))), rel=1e-4
    )
    # glTF est Y vers le haut : la hauteur (dalle de sol −0,02 → haut des murs 2,5, la
    # fabrique n'a pas de plafond) est sur Y, la profondeur du plan (3 m + épaisseur) sur Z.
    # Sans cette conversion, un viewer affiche la pièce couchée (constaté dans Chromium).
    extent = loaded.bounds[1] - loaded.bounds[0]
    assert extent[0] == pytest.approx(4.0 + T)
    assert extent[1] == pytest.approx(H + SLAB)
    assert extent[2] == pytest.approx(3.0 + T)
    artifact = export_glb(rect_project, level.id, tmp_path / "out" / "scene.glb")
    assert artifact.path.stat().st_size == len(data)
    bake = export_bake(rect_project, level.id, tmp_path / "out" / "bake.json")
    assert bake.path.is_file()


def test_pierced_slab_becomes_polyhedron(
    rect_project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dalle percée (trémie d'escalier, îlot) : elle sort en polyèdre, jamais en prisme à trous
    (le script Blender ne sait pas extruder une face à trous), volume = aire × épaisseur."""
    from shapely.geometry import Polygon

    from openplan.pipeline import scene3d

    level = only_level(rect_project)
    pierced = Polygon(
        [(0.075, 0.075), (3.925, 0.075), (3.925, 2.925), (0.075, 2.925)],
        [[(1.5, 1.0), (2.5, 1.0), (2.5, 2.0), (1.5, 2.0)]],
    )
    monkeypatch.setattr(scene3d, "_slab_polygon", lambda *_: pierced)
    bake = bake_level(rect_project, level.id)
    floor = by_name(level, "floor", Floor)
    assert not any(p.element_id == floor.id for p in bake.prisms), (
        "la dalle percée n'est plus un prisme"
    )
    assert all(not p.holes for p in bake.prisms), "aucun prisme à trous n'atteint Blender"
    slab = next(p for p in bake.polyhedra if p.element_id == floor.id)
    assert slab.kind == "slab" and slab.finish == "wood_floor" and len(slab.faces) >= 16
    scene = build_scene(bake)
    assert abs(scene.geometry[str(floor.id)].volume) == pytest.approx(
        (pierced.area) * SLAB, rel=1e-6
    )
    assert pierced.area == pytest.approx((4 - T) * (3 - T) - 1.0)


def test_detached_floor_polygon_and_invisible_elements(rect_project: Project) -> None:
    level = only_level(rect_project)
    floor = by_name(level, "floor", Floor)
    w2 = by_name(level, "W2", Wall)
    from openplan.schemas import Point2

    custom = floor.model_copy(
        update={
            "derived": False,
            "polygon": [Point2(x=0, y=0), Point2(x=1, y=0), Point2(x=1, y=1), Point2(x=0, y=1)],
        }
    )
    hidden = w2.model_copy(update={"visible": False})
    new_level = level.model_copy(
        update={"elements": {**level.elements, floor.id: custom, w2.id: hidden}}
    )
    project = rect_project.model_copy(update={"levels": {level.id: new_level}})
    bake = bake_level(project, level.id)
    assert not any(p.element_id == w2.id for p in bake.prisms)
    scene = build_scene(bake)
    assert abs(scene.geometry[str(floor.id)].volume) == pytest.approx(1.0 * SLAB, rel=1e-6)
