"""KAN-154 (étape 2, squelette) : le contrôle de fidélité d'un rendu à la géométrie exacte de sa
vue, ses mesures enfichables, la boucle de relance du poste (drapeau OPENPLAN_FIDELITY_CHECK).
Cas synthétiques : un rendu qui suit exactement la scène est conforme ; zoomé de 5 %, non."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from openplan.adapters.protocols import DetectedElement, ImageArray
from openplan.pipeline import fidelity
from openplan.pipeline.guides import OPENING_KINDS, ViewGuides, view_guides
from openplan.pipeline.scene3d import bake_level
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from openplan.schemas.fidelity import FidelityVerdict, MeasureResult
from tests.factories import only_level, rect_room_project

W, H = 512, 384


def _guides() -> ViewGuides:
    project = rect_room_project()
    bake = bake_level(project, only_level(project).id, "realistic")
    camera = RenderCamera(
        position=Point3(x=0.3, y=0.3, z=1.5),
        yaw=0.64,
        pitch=-0.2,
        focal_px=0.5 * W,
        width_px=W,
        height_px=H,
    )
    return view_guides(bake, camera, (W, H))


def _exact_render(guides: ViewGuides) -> np.ndarray:
    """Un rendu « exact » : chaque élément d'une teinte, les arêtes de la scène tracées."""
    shade = (np.where(guides.element >= 0, guides.element, 0) * 47 % 160 + 60).astype(np.uint8)
    img = np.stack([shade, shade, shade], axis=-1)
    img[guides.edges > 0] = 15
    return img


def _zoomed(img: np.ndarray, factor: float) -> np.ndarray:
    """Le même rendu, agrandi de `factor` autour du centre (un rendu qui grossit la pièce)."""
    h, w = img.shape[:2]
    cw, ch = round(w / factor), round(h / factor)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    crop = Image.fromarray(img).crop((x0, y0, x0 + cw, y0 + ch))
    return np.asarray(crop.resize((w, h), Image.Resampling.BILINEAR))


def test_an_exact_render_is_compliant() -> None:
    guides = _guides()
    verdict = fidelity.check_fidelity(_exact_render(guides), guides)
    measures = {m.name: m for m in verdict.measures}
    assert verdict.ok, verdict
    assert measures["edge_f1"].value is not None and measures["edge_f1"].value > 0.8
    assert measures["edge_scale"].value is not None and measures["edge_scale"].value < 0.01
    assert measures["openings_iou"].ok is None  # pas de détecteur : non mesurée


def test_a_render_zoomed_by_5_percent_is_not() -> None:
    guides = _guides()
    verdict = fidelity.check_fidelity(_zoomed(_exact_render(guides), 1.05), guides)
    scale = next(m for m in verdict.measures if m.name == "edge_scale")
    assert not verdict.ok and "edge_scale" in verdict.failed
    assert scale.value == pytest.approx(0.05, abs=0.01)


class _Detector:
    model_id = "faux-owlv2"

    def __init__(self, boxes: list[tuple[str, tuple[int, int, int, int]]]) -> None:
        self.boxes = boxes

    def detect(self, image: ImageArray) -> list[DetectedElement]:
        return [
            DetectedElement(category=k, bbox_px=b, score=0.9, model_id=self.model_id)  # type: ignore[arg-type]
            for k, b in self.boxes
        ]


def test_openings_are_compared_to_their_projected_rectangles() -> None:
    guides = _guides()
    projected = fidelity.projected_openings(guides)
    assert projected and {k for k, _ in projected} <= OPENING_KINDS
    render = _exact_render(guides)
    good = fidelity.OpeningsIoU(_Detector(projected)).measure(render, guides)
    assert good.ok is True and good.value == pytest.approx(1.0)
    shifted = [(k, (x0 + 60, y0, x1 + 60, y1)) for k, (x0, y0, x1, y1) in projected]
    bad = fidelity.OpeningsIoU(_Detector(shifted)).measure(render, guides)
    assert bad.ok is False


def test_a_measure_is_pluggable() -> None:
    class Always:
        name = "par_element"

        def measure(self, render: np.ndarray, guides: ViewGuides) -> MeasureResult:
            return MeasureResult(name=self.name, value=0.2, threshold=0.05, ok=False)

    guides = _guides()
    verdict = fidelity.check_fidelity(_exact_render(guides), guides, [fidelity.EdgeF1(), Always()])
    assert not verdict.ok and verdict.failed == ["par_element"]


def test_the_render_must_be_at_the_size_of_the_guides() -> None:
    guides = _guides()
    with pytest.raises(ValueError, match="même taille"):
        fidelity.check_fidelity(np.zeros((10, 10, 3), np.uint8), guides)


def _verdict(ok: bool, attempts: int) -> FidelityVerdict:
    return FidelityVerdict(ok=ok, failed=[] if ok else ["edge_scale"], attempts=attempts)


@pytest.mark.parametrize(
    ("results", "renders", "ok"),
    [([True], 1, True), ([False, False, True], 3, True), ([False, False, False], 3, False)],
)
def test_the_worker_retries_twice_at_most(results: list[bool], renders: int, ok: bool) -> None:
    seeds: list[int] = []
    out = fidelity.render_checked(seeds.append, lambda n: _verdict(results[n - 1], n), 1234)
    assert len(seeds) == renders and len(set(seeds)) == renders  # une autre graine à chaque fois
    assert out.verdict.ok is ok and out.verdict.attempts == renders
    assert out.seed == seeds[-1]


def test_the_engine_checks_and_retries_only_with_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le moteur du poste, avec un faux module Z-Image : rendu zoomé (non conforme) puis exact."""
    from openplan.cli.render_worker import engine
    from openplan.pipeline.zimage_render import guides_path

    guides = _guides()
    calls: list[Any] = []

    def render_scene(bake: Path, camera: Path, out_png: Path, **kw: Any) -> Any:
        calls.append(kw.get("seed"))
        img = _exact_render(guides)
        Image.fromarray(img if len(calls) > 1 else _zoomed(img, 1.06)).save(out_png)
        guides.save(guides_path(out_png))
        return types.SimpleNamespace(seconds=1.0, vram_peak_mib=0, width=W, height=H)

    fake = types.ModuleType("fake_zimage_kan154")
    fake.render_scene = render_scene  # type: ignore[attr-defined]
    fake.DEFAULT_SEED = 1234  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_zimage_kan154", fake)
    monkeypatch.setattr(engine, "ZIMAGE_MODULE", "fake_zimage_kan154")
    monkeypatch.setattr(engine, "_detector", lambda root: None)
    out = tmp_path / "out.png"
    off = engine.zimage_render(tmp_path / "p", tmp_path / "b", tmp_path / "c", out)
    assert off.fidelity is None and len(calls) == 1  # drapeau éteint : comme avant
    calls.clear()
    monkeypatch.setenv(fidelity.FLAG, "1")
    on = engine.zimage_render(tmp_path / "p", tmp_path / "b", tmp_path / "c", out)
    assert on.fidelity is not None
    verdict = FidelityVerdict.model_validate(on.fidelity)
    assert verdict.ok and verdict.attempts == 2
    assert calls == [1234, 1234 + fidelity.SEED_STEP]
