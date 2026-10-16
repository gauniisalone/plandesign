"""Image d'inspiration : licence à accepter, étape avec générateur simulé, CLI, registre."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.neural import weights
from openplan.adapters.neural.inspiration import (
    CONTROLNET_MODEL_ID,
    SD_MODEL_ID,
    DepthGuidedDiffusion,
    control_size,
)
from openplan.adapters.neural.weights import LicenseNotAcceptedError
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    GeneratedImage,
    ImageGenerationRequest,
)
from openplan.cli.main import main
from openplan.pipeline.ingestion import import_image
from openplan.pipeline.inspiration import (
    DEFAULT_PROMPT,
    InspirationOptions,
    run_inspiration,
)
from openplan.scene.factory import new_project
from tests.unit.test_depth_step import GradientDepth
from tests.unit.test_ingestion import jpeg_with_exif


class FakeGenerator:
    def __init__(self) -> None:
        self.model_ids = ["fake_sd@0", "fake_controlnet@0"]
        self.requests: list[ImageGenerationRequest] = []

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.requests.append(request)
        h, w = request.control.shape
        pixels = np.zeros((h, w, 3), dtype=np.uint8)
        pixels[..., 0] = request.control  # la structure se retrouve dans l'image
        return GeneratedImage(
            pixels=pixels,
            seed=request.seed,
            model_ids=self.model_ids,
            duration_s=0.5,
            peak_vram_mb=1234.0,
        )


def test_run_inspiration_writes_generated_artifacts(tmp_path: Path) -> None:
    project = new_project("p")
    project, ref, _ = import_image(project, tmp_path, jpeg_with_exif(rotate=False))
    generator = FakeGenerator()
    run = run_inspiration(
        project,
        tmp_path,
        ref.id,
        tmp_path / "runs",
        estimator=GradientDepth(),
        generator=generator,
        options=InspirationOptions(prompt="chambre sous les toits, parquet", seed=7, steps=4),
    )
    assert run.source == "generated" and run.seed == 7 and run.peak_vram_mb == 1234.0
    assert run.prompt.startswith(DEFAULT_PROMPT) and run.prompt.endswith("parquet")
    assert run.result.step == "inspiration.generate" and run.result.confidence == 0.2
    assert run.result.model_ids == ["fake_depth@0", "fake_sd@0", "fake_controlnet@0"]
    names = {a.name for a in run.result.artifacts}
    assert names == {"inspiration.png", "control_depth.png", "prompt.json"}
    (request,) = generator.requests
    assert request.steps == 4 and request.control.shape == (ref.height_px, ref.width_px)
    assert request.control[-1, 0] > request.control[0, 0], "proche (bas) = clair"
    meta = json.loads((run.result.artifacts[2].path).read_text(encoding="utf-8"))
    assert meta["source"] == "generated" and meta["seed"] == 7
    assert any("pas une photo" in a.text for a in run.result.assumptions)
    # Sans texte : consigne par défaut seule ; profondeur relative : avertissement.
    relative = run_inspiration(
        project,
        tmp_path,
        ref.id,
        tmp_path / "runs",
        estimator=GradientDepth(metric=False),
        generator=generator,
    )
    assert relative.prompt == DEFAULT_PROMPT
    assert any("relative" in w for w in relative.result.warnings)


def test_license_must_be_accepted_before_download(tmp_path: Path) -> None:
    calls: list[tuple[str, Path, list[str] | None]] = []

    def downloader(repo: str, target: Path, patterns: list[str] | None) -> None:
        calls.append((repo, target, patterns))
        (target / "unet").mkdir(parents=True, exist_ok=True)
        (target / "unet" / "diffusion_pytorch_model.fp16.safetensors").write_bytes(b"\0")

    with pytest.raises(LicenseNotAcceptedError, match="--accept-license"):
        weights.pull(
            SD_MODEL_ID,
            "org/sd",
            tmp_path,
            downloader=downloader,
            sentinel="unet/diffusion_pytorch_model.fp16.safetensors",
            license_name="CreativeML OpenRAIL-M",
        )
    assert calls == [], "rien n'est téléchargé sans acceptation"
    target = weights.pull(
        SD_MODEL_ID,
        "org/sd",
        tmp_path,
        downloader=downloader,
        sentinel="unet/diffusion_pytorch_model.fp16.safetensors",
        allow_patterns=["unet/*"],
        license_name="CreativeML OpenRAIL-M",
        accept_license=True,
    )
    assert calls[0][2] == ["unet/*"]
    assert weights.license_accepted(SD_MODEL_ID, tmp_path)
    marker = (target / weights.LICENSE_MARKER).read_text(encoding="utf-8")
    assert marker.startswith("CreativeML OpenRAIL-M") and "accepted_at" in marker
    assert weights.is_installed(
        SD_MODEL_ID, tmp_path, "unet/diffusion_pytorch_model.fp16.safetensors"
    )
    # L'adaptateur exige poids et licence pour les deux modèles.
    with pytest.raises(AdapterUnavailableError, match="controlnet_depth_sd15"):
        DepthGuidedDiffusion(tmp_path)
    (target / weights.LICENSE_MARKER).unlink()
    with pytest.raises(LicenseNotAcceptedError, match="licence non acceptée"):
        DepthGuidedDiffusion(tmp_path)


def test_control_size_is_multiple_of_eight() -> None:
    assert control_size(1200, 1600, 640) == (480, 640)
    assert control_size(1280, 960, 640) == (640, 480)
    assert control_size(300, 200, 640) == (304, 200)
    assert all(v % 8 == 0 for v in control_size(1000, 333, 512))


def test_models_pull_requires_license_flag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = main(["models", "pull", CONTROLNET_MODEL_ID, "--models-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 3 and "OpenRAIL-M" in out and "--accept-license" in out
    seen: dict[str, object] = {}

    def fake_pull(model_id: str, repo_id: str, root: Path, **kwargs: object) -> Path:
        seen.update(kwargs, model_id=model_id, repo_id=repo_id)
        return root / "x"

    monkeypatch.setattr("openplan.adapters.neural.weights.pull", fake_pull)
    code = main(
        ["models", "pull", CONTROLNET_MODEL_ID, "--models-dir", str(tmp_path), "--accept-license"]
    )
    assert code == 0
    assert seen["repo_id"] == "lllyasviel/control_v11f1p_sd15_depth"
    assert seen["accept_license"] is True and seen["license_name"] == "CreativeML OpenRAIL-M"
    assert seen["sentinel"] == "diffusion_pytorch_model.fp16.safetensors"
    assert "fp16" in str(seen["allow_patterns"])
    assert main(["models", "list", "--models-dir", str(tmp_path)]) == 0
    assert "licence à accepter" in capsys.readouterr().out
