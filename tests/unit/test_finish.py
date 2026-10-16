"""D5 : finition IA d'un rendu, à force bornée, guidée, marquée `generated`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.protocols import FinishRequest, GeneratedImage
from openplan.pipeline.finish import (
    FINISH_SIDECAR,
    FINISHED,
    FinishOptions,
    finish_prompt,
    finish_render,
    read_finish,
)
from openplan.schemas.common import Source
from tests.factories import attic_project, rect_room_project


class FakeFinisher:
    model_ids: ClassVar[list[str]] = [
        "stable_diffusion_1_5@1.5",
        "controlnet_depth_sd15@1.1",
        "controlnet_canny_sd15@1.1",
    ]

    def __init__(self) -> None:
        self.request: FinishRequest | None = None

    def finish(self, request: FinishRequest) -> GeneratedImage:
        self.request = request
        out = np.clip(request.image.astype(int) + 10, 0, 255).astype(np.uint8)
        return GeneratedImage(
            pixels=out,
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=1.0,
            peak_vram_mb=3700.0,
        )


def _render_dir(tmp: Path) -> Path:
    folder = tmp / "blender"
    folder.mkdir()
    img = np.zeros((96, 128, 3), np.uint8)
    img[30:60, 40:90] = 200
    Image.fromarray(img).save(folder / "preview.png")
    np.save(
        folder / "depth_z.npy", np.linspace(1.0, 6.0, 96 * 128, dtype=np.float32).reshape(96, 128)
    )
    return folder


def test_strength_is_bounded_and_defaults_to_light() -> None:
    assert FinishOptions().strength == 0.35
    FinishOptions(strength=0.55)
    with pytest.raises(ValueError):
        FinishOptions(strength=0.6)


def test_prompt_follows_room_type_level_and_style() -> None:
    attic = finish_prompt(attic_project(), "chalet")
    assert "attic" in attic and "chalet" in attic
    assert "bedroom" in finish_prompt(rect_room_project(), "scandinave")


def test_finish_keeps_the_blender_image_and_marks_the_result_generated(tmp_path: Path) -> None:
    from uuid import uuid4

    folder = _render_dir(tmp_path)
    finisher = FakeFinisher()
    entry = finish_render(
        attic_project(),
        folder,
        uuid4(),
        FinishOptions(strength=0.45, style="chalet"),
        finisher=finisher,
    )
    assert finisher.request is not None
    assert finisher.request.strength == 0.45 and finisher.request.depth is not None
    assert finisher.request.edges is not None and finisher.request.edges.max() == 255
    assert (folder / "preview.png").is_file() and (folder / FINISHED).is_file()
    assert entry.source is Source.GENERATED and entry.guided_by == ["profondeur", "arêtes"]
    saved = json.loads((folder / FINISH_SIDECAR).read_text(encoding="utf-8"))
    assert saved["source"] == "generated" and saved["strength"] == 0.45
    assert read_finish(folder) == entry
    with Image.open(folder / FINISHED) as done:
        assert done.size == (128, 96)
