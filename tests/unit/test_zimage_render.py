"""KAN-88 : arêtes de guidage depuis la scène, et point d'entrée `render_scene` du worker de
rendu (KAN-89), sans carte graphique (le moteur est remplacé par un faux)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar
from uuid import uuid4

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.neural import zimage
from openplan.adapters.protocols import FinishRequest, GeneratedImage
from openplan.pipeline import zimage_render
from openplan.pipeline.finish import FinishOptions, edges_of, finish_render
from openplan.pipeline.guides import view_guides
from openplan.pipeline.render import camera_view
from openplan.pipeline.scene3d import bake_level
from openplan.pipeline.views import AXIS_KEY, RIDGE, apply_auto_views
from openplan.pipeline.zimage_render import (
    ZImageResult,
    ZImageUnavailable,
    ZImageVramGuard,
    render_scene,
)
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import Point3
from openplan.schemas.elements import Camera
from openplan.schemas.scene import Project
from tests.factories import attic_project, only_level


def _ridge_scene() -> tuple[Project, Bake, RenderCamera]:
    project = attic_project()
    level = only_level(project)
    project, _ = apply_auto_views(project, level.id, actor="user:test", ridge=True)
    cam = next(
        e
        for e in project.levels[level.id].elements.values()
        if isinstance(e, Camera) and e.metadata.get(AXIS_KEY) == RIDGE
    )
    _, rc = camera_view(project, level.id, cam.id)
    return project, bake_level(project, level.id, style="realistic"), rc


def test_guides_draw_the_geometry_of_the_view() -> None:
    _, bake, camera = _ridge_scene()
    guides = view_guides(bake, camera, (816, 1024))
    assert guides.edges.shape == (1024, 816) and guides.element.shape == (1024, 816)
    share = float((guides.edges == 255).mean())
    assert 0.003 < share < 0.25, share
    seen = {guides.labels[i] for i in np.unique(guides.element) if i >= 0}
    walls = {str(p.element_id) for p in [*bake.prisms, *bake.polyhedra] if p.kind == "wall"}
    assert seen & walls, "les pignons sont dans la vue"


def test_guides_are_empty_when_looking_away() -> None:
    _, bake, camera = _ridge_scene()
    away = camera.model_copy(update={"position": Point3(x=60.0, y=60.0, z=1.6)})
    guides = view_guides(bake, away, (408, 512))
    assert not guides.edges.any() and (guides.element == -1).all()


class FakeZ:
    model_ids: ClassVar[list[str]] = list(zimage.Z_MODEL_IDS)
    prompt_engine = "zimage"
    last: ClassVar[FinishRequest | None] = None

    def __init__(self, _root: Path, **_kw: object) -> None:
        pass

    def finish(self, request: FinishRequest) -> GeneratedImage:
        FakeZ.last = request
        pixels = np.full((*request.image.shape[:2], 3), 120, np.uint8)
        return GeneratedImage(
            pixels=pixels,
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=1.0,
            peak_vram_mb=4700 * 1.048576,
        )


def _files(tmp: Path) -> tuple[Path, Path, Path]:
    project, bake, camera = _ridge_scene()
    (tmp / "bake.json").write_text(bake.model_dump_json(), encoding="utf-8")
    (tmp / "camera.json").write_text(camera.model_dump_json(), encoding="utf-8")
    (tmp / "project.json").write_text(project.model_dump_json(), encoding="utf-8")
    return tmp / "bake.json", tmp / "camera.json", tmp / "project.json"


def test_render_scene_follows_the_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bake, camera, project = _files(tmp_path)
    monkeypatch.setattr(zimage_render, "ZImageFinisher", FakeZ)
    out = tmp_path / "out" / "vue.png"
    result = render_scene(bake, camera, out, project_path=project)
    assert isinstance(result, ZImageResult)
    assert (result.width, result.height, result.engine) == (816, 1024, "zimage")
    assert result.vram_peak_mib == 4700
    with Image.open(out) as img:
        assert img.size == (816, 1024)
    request = FakeZ.last
    assert request is not None and request.edges is not None and request.edges.any()
    assert "attic" in request.prompt, "prompt dérivé de la scène (KAN-86)"
    render_scene(bake, camera, out, prompt="a cosy attic bedroom")
    assert FakeZ.last is not None and FakeZ.last.prompt == "a cosy attic bedroom"


def test_render_scene_needs_a_prompt_or_the_project(tmp_path: Path) -> None:
    bake, camera, _ = _files(tmp_path)
    with pytest.raises(ValueError, match="prompt"):
        render_scene(bake, camera, tmp_path / "x.png")


def test_render_scene_without_weights(tmp_path: Path) -> None:
    bake, camera, project = _files(tmp_path)
    with pytest.raises(ZImageUnavailable, match="models pull"):
        render_scene(bake, camera, tmp_path / "x.png", project_path=project, weights_root=tmp_path)
    assert issubclass(ZImageVramGuard, zimage.AdapterUnavailableError)


def test_weights_are_found_from_another_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le poste de rendu calcule depuis le dossier de la demande : les poids restent ceux de la
    copie du code (ou d'OPENPLAN_MODELS_DIR)."""
    import openplan

    monkeypatch.delenv("OPENPLAN_MODELS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    root = Path(openplan.__file__).resolve().parents[1]
    assert zimage_render.models_root() == root / "models" / "weights"
    monkeypatch.setenv("OPENPLAN_MODELS_DIR", str(tmp_path / "ailleurs"))
    assert zimage_render.models_root() == tmp_path / "ailleurs"


def test_app_finish_uses_the_geometric_edges_with_zimage(tmp_path: Path) -> None:
    project, bake, camera = _ridge_scene()
    folder = tmp_path / "blender"
    folder.mkdir()
    pixels = np.random.default_rng(0).integers(0, 255, (128, 102, 3), dtype=np.uint8)
    Image.fromarray(pixels).save(folder / "preview.png")
    (folder / "render.json").write_text(json.dumps({"level_id": str(bake.level_id)}))
    (folder / "bake.json").write_text(bake.model_dump_json(), encoding="utf-8")
    (folder / "camera.json").write_text(camera.model_dump_json(), encoding="utf-8")
    fake = FakeZ(tmp_path)
    finish_render(project, folder, uuid4(), FinishOptions(), finisher=fake)
    request = FakeZ.last
    assert request is not None and request.edges is not None
    expected = view_guides(bake, camera, (102, 128)).edges
    assert np.array_equal(request.edges, expected), "géométrie, pas le Canny du bruit"
    assert not np.array_equal(request.edges, edges_of(pixels))
