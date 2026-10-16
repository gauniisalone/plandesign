"""Le bake décrit les baies (habillage de rendu) sans toucher aux prismes ni aux volumes."""

from __future__ import annotations

import pytest

from openplan.pipeline.scene3d import bake_level, build_scene, scene_volume
from openplan.schemas import Project
from tests.factories import only_level


def test_bake_lists_door_and_window_on_their_wall_axis(rect_project: Project) -> None:
    level = only_level(rect_project)
    bake = bake_level(rect_project, level.id)
    kinds = sorted(o.kind for o in bake.openings)
    assert kinds == ["door", "window"]
    door = next(o for o in bake.openings if o.kind == "door")
    window = next(o for o in bake.openings if o.kind == "window")
    # Porte sur W1 (y = 0, de (0,0) vers (4,0)) : offset 0,5, largeur 0,83 → centre x = 0,915.
    assert door.center.x == pytest.approx(0.915) and door.center.y == pytest.approx(0.0)
    assert door.center.z == 0.0 and door.width == 0.83 and door.height == 2.04
    assert door.direction == pytest.approx((1.0, 0.0)) and door.wall_thickness == 0.15
    # Fenêtre sur W3 (y = 3, de (4,3) vers (0,3)) : offset 1,2, largeur 1,2 → x = 4 − 1,8 = 2,2.
    assert window.center.x == pytest.approx(2.2) and window.center.y == pytest.approx(3.0)
    assert window.center.z == pytest.approx(0.9) and window.height == 1.25
    # Les volumes trimesh ne changent pas : l'habillage n'est pas un solide du bake.
    without = bake.model_copy(update={"openings": []})
    assert scene_volume(build_scene(bake)) == pytest.approx(scene_volume(build_scene(without)))
