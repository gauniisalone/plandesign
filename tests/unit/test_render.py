"""Rendu depuis la photo : choix de la caméra, taille et focale, erreurs claires ; matières."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.pipeline.blender_bridge import BlenderError
from openplan.pipeline.render import MAX_RENDER_PX, NoCameraError, photo_camera, render_photo_view
from openplan.pipeline.scene3d import FINISH_COLORS, bake_level, finish_of
from openplan.schemas import Project, RoofPlane, Wall
from tests.factories import attic_project, only_level


def test_photo_camera_defaults_without_image() -> None:
    project = attic_project()
    level = only_level(project)
    camera, render = photo_camera(project, level.id)
    assert camera.name == "Photo" and render.width_px == 1280 and render.height_px == 960
    assert render.focal_px == pytest.approx(0.8 * 1280) and render.checker_floor is False
    assert render.position.z == pytest.approx(1.45) and render.yaw == 0.0


def test_photo_camera_uses_the_image_size_and_caps_it(tmp_path: Path) -> None:
    from openplan.pipeline.ingestion import import_image
    from openplan.schemas import Camera
    from tests.unit.test_ingestion import jpeg_with_exif

    project = attic_project()
    level = only_level(project)
    project, ref, _ = import_image(project, tmp_path, jpeg_with_exif(2400, 1800, rotate=False))
    old = next(e for e in level.elements.values() if isinstance(e, Camera))
    linked = old.model_copy(update={"image_id": ref.id, "focal_px": 2000.0})
    level = level.model_copy(update={"elements": {**level.elements, old.id: linked}})
    project = project.model_copy(update={"levels": {level.id: level}})
    _, render = photo_camera(project, level.id)
    assert max(render.width_px, render.height_px) == MAX_RENDER_PX
    assert render.width_px == 1600 and render.height_px == 1200
    assert render.focal_px == pytest.approx(2000.0 * 1600 / 2400)


def test_render_errors_are_explicit(rect_project: Project, tmp_path: Path) -> None:
    level = only_level(rect_project)
    with pytest.raises(NoCameraError, match="aucune caméra"):
        photo_camera(rect_project, level.id)
    with pytest.raises(NoCameraError, match="niveau inconnu"):
        photo_camera(rect_project, level.id.__class__(int=0))
    attic = attic_project()
    with pytest.raises(BlenderError, match="introuvable"):
        render_photo_view(attic, only_level(attic).id, tmp_path, blender_bin="/nonexistent")


def test_finishes_and_realistic_colours() -> None:
    project = attic_project()
    level = only_level(project)
    gable = next(e for e in level.elements.values() if isinstance(e, Wall) and e.name == "Pignon")
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    assert finish_of(gable, "wall") == "stone" and finish_of(roof, "roof") == "wood_boards"
    bad = gable.model_copy(update={"metadata": {"finish": "gold"}})
    assert finish_of(bad, "wall") == "plaster", "matière inconnue : défaut du type"
    provenance = bake_level(project, level.id)
    realistic = bake_level(project, level.id, "realistic")
    assert provenance.style == "provenance" and realistic.style == "realistic"
    by_id = {p.element_id: p for p in realistic.polyhedra if p.kind != "timber"}
    assert by_id[gable.id].finish == "stone" and by_id[gable.id].color == FINISH_COLORS["stone"]
    assert by_id[roof.id].color == FINISH_COLORS["wood_boards"]
    floor = next(p for p in realistic.prisms if p.kind == "floor")
    assert floor.finish == "wood_floor" and floor.color == FINISH_COLORS["wood_floor"]
    assert {p.element_id: p for p in provenance.polyhedra}[gable.id].color == (184, 134, 11)
