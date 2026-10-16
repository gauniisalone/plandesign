"""Étape profondeur avec un estimateur simulé, poids locaux, registre, CLI models."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openplan.adapters.neural import weights
from openplan.adapters.protocols import AdapterUnavailableError, DepthMap, ImageArray
from openplan.adapters.registry import load_registry, registry_entry
from openplan.cli.main import main
from openplan.pipeline.depth import DEPTH_CAP_M, depth_stats, depth_to_image, run_depth
from openplan.pipeline.ingestion import import_image
from openplan.scene.factory import new_project
from tests.unit.test_ingestion import jpeg_with_exif


class GradientDepth:
    """Profondeur simulée : 1 m en bas de l'image, `far` en haut, avec des NaN."""

    model_id = "fake_depth@0"

    def __init__(self, far: float = 6.0, metric: bool = True) -> None:
        self.far = far
        self.metric = metric

    def predict(self, image: ImageArray) -> DepthMap:
        rows = np.linspace(self.far, 1.0, image.height, dtype=np.float32)
        if self.far >= DEPTH_CAP_M:
            rows[: image.height // 10] = DEPTH_CAP_M  # 10 % de pixels au plafond
        depth = np.repeat(rows[:, None], image.width, axis=1)
        depth[0, 0] = np.nan
        return DepthMap(depth=depth, metric=self.metric, model_id=self.model_id)


def test_run_depth_writes_artifacts_and_stats(tmp_path: Path) -> None:
    project = new_project("p")
    project, ref, _ = import_image(project, tmp_path, jpeg_with_exif(rotate=False))
    run = run_depth(project, tmp_path, ref.id, tmp_path / "runs", estimator=GradientDepth())
    assert run.result.step == "depth.metric" and run.result.model_ids == ["fake_depth@0"]
    assert {a.name for a in run.result.artifacts} == {"depth.npy", "depth.png"}
    saved = np.load(run.result.artifacts[0].path)
    assert saved.shape == (ref.height_px, ref.width_px) and saved.dtype == np.float32
    assert run.stats.min_m == pytest.approx(1.0) and run.stats.max_m == pytest.approx(6.0)
    assert 1.0 < run.stats.median_m < 6.0 and run.stats.saturated_fraction == 0.0
    assert run.result.warnings == [] and run.result.confidence == 0.5
    assert any("estimation" in a.text for a in run.result.assumptions)


def test_run_depth_warns_on_saturation_and_relative(tmp_path: Path) -> None:
    project = new_project("p")
    project, ref, _ = import_image(project, tmp_path, jpeg_with_exif(rotate=False))
    saturated = run_depth(
        project, tmp_path, ref.id, tmp_path / "runs", estimator=GradientDepth(far=DEPTH_CAP_M)
    )
    assert any("plafond du modèle" in w for w in saturated.result.warnings)
    relative = run_depth(
        project, tmp_path, ref.id, tmp_path / "runs", estimator=GradientDepth(metric=False)
    )
    assert any("relative" in w for w in relative.result.warnings)
    assert relative.result.confidence == 0.2


def test_depth_helpers() -> None:
    empty = np.full((4, 4), np.nan, dtype=np.float32)
    assert depth_stats(empty).saturated_fraction == 1.0
    assert depth_to_image(empty).max() == 0
    ramp = np.linspace(1, 5, 100, dtype=np.float32).reshape(10, 10)
    image = depth_to_image(ramp)
    assert image[0, 0] > image[-1, -1], "proche = clair"


def test_weights_helpers_and_pull_with_fake_downloader(tmp_path: Path) -> None:
    model_id = "some_model@1.0"
    assert weights.weights_dir(model_id, tmp_path) == tmp_path / "some_model_1.0"
    assert not weights.is_installed(model_id, tmp_path)
    with pytest.raises(AdapterUnavailableError, match="openplan models pull some_model"):
        weights.require_installed(model_id, tmp_path)

    def incomplete(repo: str, target: Path, patterns: list[str] | None = None) -> None:
        (target / "config.json").write_text("{}")

    with pytest.raises(AdapterUnavailableError, match="incomplet"):
        weights.pull(model_id, "org/repo", tmp_path, downloader=incomplete)
    assert "huggingface_hub" not in Path(weights.__file__).read_text(encoding="utf-8"), (
        "le module des poids n'importe aucun client réseau"
    )

    def complete(repo: str, target: Path, patterns: list[str] | None = None) -> None:
        assert repo == "org/repo" and patterns is None
        (target / weights.SENTINEL).write_bytes(b"\0")

    assert weights.pull(model_id, "org/repo", tmp_path, downloader=complete).is_dir()
    assert weights.is_installed(model_id, tmp_path)


def test_registry_loader_and_depth_entry() -> None:
    entries = load_registry()
    ids = {e.id for e in entries}
    assert "depth_anything_v2_metric_indoor_small@1.0" in ids
    entry = registry_entry("depth_anything_v2_metric_indoor_small@1.0")
    assert entry.kind == "weights" and entry.hf_repo and entry.license == "Apache-2.0"
    assert entry.module == "openplan.adapters.neural.depth_anything"
    with pytest.raises(KeyError):
        registry_entry("nope@0")


def test_depth_adapter_requires_weights(tmp_path: Path) -> None:
    from openplan.adapters.neural.depth_anything import DepthAnythingMetric

    with pytest.raises(AdapterUnavailableError, match="models pull"):
        DepthAnythingMetric(tmp_path)


def test_models_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert main(["models", "list"]) == 0
    out = capsys.readouterr().out
    assert "depth_anything_v2_metric_indoor_small@1.0" in out and "à télécharger" in out
    assert "heuristic_box@0.1" in out and "consentement" in out

    calls: list[tuple[str, Path]] = []

    def fake_pull(model_id: str, repo_id: str, root: Path, **_: object) -> Path:
        calls.append((repo_id, root))
        return root / "x"

    monkeypatch.setattr("openplan.adapters.neural.weights.pull", fake_pull)
    code = main(
        [
            "models",
            "pull",
            "depth_anything_v2_metric_indoor_small@1.0",
            "--models-dir",
            str(tmp_path),
        ]
    )
    assert code == 0 and calls == [
        ("depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf", tmp_path)
    ]
    assert main(["models", "pull", "heuristic_box@0.1"]) == 2
    with pytest.raises(KeyError):
        main(["models", "pull", "nope@0"])
