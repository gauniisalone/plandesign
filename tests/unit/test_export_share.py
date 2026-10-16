"""Export JSON à partager : sans historique ni chemins d'images, projet toujours valide."""

import json
from pathlib import Path

from openplan.pipeline.export import export_json, shareable
from openplan.scene import history
from openplan.scene.history import USER_ACTOR
from openplan.scene.migrate import load_project
from openplan.schemas import Project, Wall
from openplan.schemas.scene import ImageRef
from tests.factories import by_name, only_level


def _with_image(project: Project) -> Project:
    """Projet avec une image référencée et une entrée d'historique (un geste utilisateur)."""
    ref = ImageRef(
        path="images/photo.jpg",
        sha256="a" * 64,
        width_px=640,
        height_px=480,
        exif_stripped=True,
        focal_35mm=28.0,
    )
    with_image = project.model_copy(update={"images": {ref.id: ref}})
    level = only_level(with_image)
    wall = by_name(level, "W2", Wall)
    new_level = level.model_copy(
        update={"elements": {**level.elements, wall.id: wall.model_copy(update={"thickness": 0.3})}}
    )
    edited = with_image.model_copy(update={"levels": {level.id: new_level}})
    return history.commit(with_image, edited, actor=USER_ACTOR, label="Épaisseur W2")


def test_shareable_strips_history_and_paths(rect_project: Project) -> None:
    project = _with_image(rect_project)
    assert project.history and project.history_cursor > 0
    shared = shareable(project)
    assert shared.history == [] and shared.history_cursor == 0
    (ref,) = shared.images.values()
    assert ref.path == "" and ref.sha256 == "a" * 64 and ref.focal_35mm == 28.0
    # Le contenu métier est intact et le fichier reste un projet valide.
    assert shared.levels == project.levels and shared.id == project.id
    assert load_project(json.loads(shared.model_dump_json())).levels == project.levels
    # L'original n'est pas touché.
    assert project.history and next(iter(project.images.values())).path == "images/photo.jpg"


def test_export_json_share_flag(rect_project: Project, tmp_path: Path) -> None:
    project = _with_image(rect_project)
    full = export_json(project, tmp_path / "full.json")
    shared = export_json(project, tmp_path / "share.json", share=True)
    assert "sans historique" in shared.description and "complet" in full.description
    full_data = json.loads(full.path.read_text(encoding="utf-8"))
    shared_data = json.loads(shared.path.read_text(encoding="utf-8"))
    assert full_data["history"] and "images/photo.jpg" in full.path.read_text(encoding="utf-8")
    assert shared_data["history"] == [] and "images/photo.jpg" not in shared.path.read_text(
        encoding="utf-8"
    )
