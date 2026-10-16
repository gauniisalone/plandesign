"""Vignette d'une variante (3.7) : point de vue de vignette dans la pièce, entrée de galerie."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from shapely.geometry import Point

from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.layouts import LayoutBody, generate_layouts
from openplan.pipeline.render import (
    RENDER_SIDECAR,
    VARIANT_SIZE,
    NoCameraError,
    RenderEntry,
    list_renders,
    overview_camera,
)
from openplan.schemas.elements import Room
from openplan.schemas.scene import Level
from tests.factories import attic_project, only_level, rect_room_project
from tests.unit.test_layouts import BEDROOM


def _room_polygon(level: Level):  # type: ignore[no-untyped-def]
    graph = WallGraph.from_level(level)
    room = next(e for e in level.elements.values() if isinstance(e, Room))
    return graph.room_polygon(room.id)


def test_overview_camera_is_inside_the_room_under_the_ceiling_and_looks_at_its_centre() -> None:
    project = rect_room_project()
    level = only_level(project)
    run = generate_layouts(project, level.id, LayoutBody(brief=BEDROOM, variants=1, seed=1))
    camera = overview_camera(level, run.proposals[0].furniture)
    polygon = _room_polygon(level)
    assert polygon.contains(Point(camera.position.x, camera.position.y)), "dans la pièce"
    assert 1.0 <= camera.position.z <= level.height - 0.3
    assert camera.pitch < 0, "regarde vers le bas"
    furniture = run.proposals[0].furniture
    fx = sum(f.transform.position.x for f in furniture) / len(furniture)
    fy = sum(f.transform.position.y for f in furniture) / len(furniture)
    expected_yaw = math.atan2(fy - camera.position.y, fx - camera.position.x)
    assert abs(camera.yaw - expected_yaw) < 1e-6, "vise les meubles"
    minx, miny, maxx, maxy = polygon.bounds
    corners = [
        (minx + 0.45, miny + 0.45),
        (maxx - 0.45, miny + 0.45),
        (minx + 0.45, maxy - 0.45),
        (maxx - 0.45, maxy - 0.45),
    ]
    farthest = max(corners, key=lambda c: math.hypot(c[0] - fx, c[1] - fy))
    assert (camera.position.x, camera.position.y) == farthest, "coin le plus loin des meubles"
    assert (camera.width_px, camera.height_px) == VARIANT_SIZE
    assert abs(camera.focal_px - VARIANT_SIZE[0] / 2) < 1e-9, "champ horizontal de 90°"
    assert camera.checker_floor is False


def test_overview_camera_in_the_attic_stays_low_and_without_anything_says_so() -> None:
    attic = attic_project()
    level = only_level(attic)
    camera = overview_camera(level, [])
    assert camera.position.z <= 2.2 and camera.pitch < 0
    empty = Level(name="vide")
    with pytest.raises(NoCameraError, match="rien à cadrer"):
        overview_camera(empty, [])


def test_gallery_keeps_the_variant_number(tmp_path: Path) -> None:
    run_dir = tmp_path / "abc" / "blender"
    run_dir.mkdir(parents=True)
    (run_dir / "preview.png").write_bytes(b"png")
    entry = RenderEntry(
        run_id="00000000-0000-0000-0000-000000000001",  # type: ignore[arg-type]
        camera_id=None,
        camera_name="Variante 2",
        samples=32,
        device="cuda",
        render_s=6.0,
        width_px=640,
        height_px=480,
        created_at="2026-09-28T16:00:00Z",  # type: ignore[arg-type]
        variant=2,
    )
    (run_dir / RENDER_SIDECAR).write_text(entry.model_dump_json(), encoding="utf-8")
    listed = list_renders(tmp_path)
    assert len(listed) == 1 and listed[0].variant == 2 and listed[0].camera_id is None
