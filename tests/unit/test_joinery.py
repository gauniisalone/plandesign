"""E5b : menuiseries des ouvertures en 3D (chambranle, vantaux, vitrages, rail)."""

from __future__ import annotations

import pytest

from openplan.pipeline.scene3d import bake_level, build_scene
from openplan.scene.ops import AddOpening, apply_op
from openplan.schemas.bake import Prism
from openplan.schemas.elements import Opening, OpeningKind, Swing, Wall
from openplan.schemas.scene import Project
from tests.factories import only_level, rect_room_project


def _with(kind: OpeningKind, width: float = 1.0, **extra: object) -> tuple[Project, Opening]:
    project = rect_room_project(6.0, 4.0)
    level = only_level(project)
    elements = {k: e for k, e in level.elements.items() if not isinstance(e, Opening)}
    project = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    wall = next(
        e for e in only_level(project).elements.values() if isinstance(e, Wall) and e.name == "W1"
    )
    glass = (OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW)
    project = apply_op(
        project,
        level.id,
        AddOpening(
            host_wall=wall.id,
            kind=kind,
            offset=2.0,
            width=width,
            height=1.25 if kind is OpeningKind.WINDOW else 2.10,
            sill_height=0.9 if kind is OpeningKind.WINDOW else 0.0,
            swing=None if kind in glass else Swing(hinge="left", direction="inward"),
            **extra,  # type: ignore[arg-type]
        ),
    )
    opening = next(e for e in only_level(project).elements.values() if isinstance(e, Opening))
    return project, opening


def _parts(project: Project, opening: Opening) -> list[Prism]:
    bake = bake_level(project, only_level(project).id, style="realistic")
    return [p for p in bake.prisms if p.kind == "joinery" and p.element_id == opening.id]


def _finishes(parts: list[Prism]) -> list[str]:
    return [p.finish for p in parts]


def test_door_has_frame_and_one_leaf_in_the_bay() -> None:
    project, door = _with(OpeningKind.DOOR, 0.83)
    parts = _parts(project, door)
    assert _finishes(parts).count("plaster") == 3, "deux montants et une traverse"
    leaves = [p for p in parts if p.finish == "wood_dark"]
    assert len(leaves) == 1
    xs = [q.x for q in leaves[0].exterior]
    assert min(xs) > 2.0 and max(xs) < 2.83, "dans la baie"
    assert max(p.z1 for p in parts) == pytest.approx(2.10)


@pytest.mark.parametrize(
    ("kind", "extra", "leaves", "glass"),
    [
        (OpeningKind.DOUBLE_DOOR, {}, 2, 0),
        (OpeningKind.FOLDING_DOOR, {}, 4, 0),
        (OpeningKind.POCKET_DOOR, {}, 1, 0),
        (OpeningKind.FRENCH_WINDOW, {}, 0, 1),
        (OpeningKind.FRENCH_WINDOW, {"leaves": 2}, 0, 2),
        (OpeningKind.SLIDING_WINDOW, {}, 0, 2),
        (OpeningKind.WINDOW, {}, 0, 1),
    ],
)
def test_each_kind_gets_its_leaves_and_glass(
    kind: OpeningKind, extra: dict[str, object], leaves: int, glass: int
) -> None:
    project, opening = _with(kind, 1.4, **extra)
    finishes = _finishes(_parts(project, opening))
    assert finishes.count("wood_dark") == leaves
    assert finishes.count("glass") == glass


def test_sliding_door_leaf_on_the_wall_face_and_rail_over_the_stack() -> None:
    project, door = _with(OpeningKind.SLIDING_DOOR, 0.83)
    parts = _parts(project, door)
    leaf = next(p for p in parts if p.finish == "wood_dark")
    rail = next(p for p in parts if p.finish == "metal")
    assert min(q.y for q in leaf.exterior) > 0.075, "contre la face intérieure (y > 7,5 cm)"
    xs = [q.x for q in rail.exterior]
    assert min(xs) < 2.0 - 0.8 and max(xs) > 2.83, "rail sur la baie et le tronçon d'effacement"
    assert rail.z0 > 2.10


def test_joinery_reaches_the_glb_under_the_opening_id() -> None:
    project, door = _with(OpeningKind.DOUBLE_DOOR, 1.4)
    scene = build_scene(bake_level(project, only_level(project).id))
    assert str(door.id) in scene.geometry
