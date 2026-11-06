"""KAN-154 (étape 1) : la profondeur des guides (rastérisée sur le processeur, sans Blender)
contre la passe Z de Cycles, même scène (le bake.json écrit par le rendu), même caméra.

Tolérance nommée : sur les pixels de surface franche (hors des arêtes des guides, dilatées de
2 px, et hors habillage), l'écart relatif |Δz| / z a une médiane d'au plus 0,5 % et un 95e
centile d'au plus 2 %. Les deux définissent z de la même façon : la distance au plan de la
caméra, le long de son axe."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from openplan.api.settings import Settings
from openplan.pipeline.blender_bridge import find_blender, run_blender
from openplan.pipeline.guides import view_guides
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import Point3
from tests.factories import only_level, rect_room_project

BLENDER = find_blender(Settings.from_env().blender_bin)
pytestmark = [
    pytest.mark.blender,
    pytest.mark.slow,
    pytest.mark.skipif(BLENDER is None, reason="Blender absent"),
]
MEDIAN_MAX = 0.005
P95_MAX = 0.02
EDGE_MARGIN_PX = 2


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


def test_guides_depth_matches_the_cycles_z_pass(tmp_path: Path) -> None:
    project = rect_room_project()
    level_id = only_level(project).id
    camera = RenderCamera(
        position=Point3(x=0.4, y=0.4, z=1.5),
        yaw=0.64,
        pitch=-0.15,
        focal_px=0.6 * 320,
        width_px=320,
        height_px=240,
        checker_floor=False,
    )
    out = tmp_path / "render"
    rendered = run_blender(
        project, level_id, out, blender_bin=str(BLENDER), camera=camera, samples=1, depth=True
    )
    assert (out / "depth_z.npy").is_file(), rendered.warnings
    truth = np.load(out / "depth_z.npy").astype(np.float64)
    bake = Bake.model_validate_json((out / "bake.json").read_text(encoding="utf-8"))
    used = RenderCamera.model_validate_json((out / "camera.json").read_text(encoding="utf-8"))
    guides = view_guides(bake, used, (truth.shape[1], truth.shape[0]))
    ours = guides.depth.astype(np.float64)
    near_edge = _dilate(guides.edges > 0, EDGE_MARGIN_PX)
    keep = (
        np.isfinite(ours)
        & np.isfinite(truth)
        & (truth > 0.05)
        & (truth < 1e3)
        & ~near_edge
        & ~guides.mask({"decor"})
    )
    assert keep.mean() > 0.5, f"trop peu de pixels comparables : {keep.mean():.0%}"
    rel = np.abs(ours[keep] - truth[keep]) / truth[keep]
    median, p95 = float(np.median(rel)), float(np.percentile(rel, 95))
    print(
        f"KAN-154 profondeur guides/Cycles : médiane {median:.3%}, p95 {p95:.3%}, {keep.sum()} px"
    )
    assert median <= MEDIAN_MAX
    assert p95 <= P95_MAX


def test_a_cycles_render_of_the_scene_is_compliant_and_its_5_percent_zoom_is_not(
    tmp_path: Path,
) -> None:
    """KAN-154 (étape 2) : « Blender exact doit être conforme » ; le même rendu zoomé de 5 %
    sort hors du seuil d'échelle (seuils provisoires, à calibrer par le banc KAN-153)."""
    from PIL import Image

    from openplan.pipeline.fidelity import check_fidelity

    project = rect_room_project()
    level_id = only_level(project).id
    camera = RenderCamera(
        position=Point3(x=0.4, y=0.4, z=1.5),
        yaw=0.64,
        pitch=-0.15,
        focal_px=0.6 * 512,
        width_px=512,
        height_px=384,
        checker_floor=False,
    )
    out = tmp_path / "render"
    run_blender(
        project,
        level_id,
        out,
        blender_bin=str(BLENDER),
        camera=camera,
        samples=16,
        denoise=True,
        style="realistic",
    )
    with Image.open(out / "preview.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    bake = Bake.model_validate_json((out / "bake.json").read_text(encoding="utf-8"))
    used = RenderCamera.model_validate_json((out / "camera.json").read_text(encoding="utf-8"))
    guides = view_guides(bake, used, (pixels.shape[1], pixels.shape[0]))
    exact = check_fidelity(pixels, guides)
    print(f"KAN-154 Cycles exact : {[(m.name, m.value) for m in exact.measures]}")
    assert exact.ok, exact
    h, w = pixels.shape[:2]
    cw, ch = round(w / 1.05), round(h / 1.05)
    x0, y0 = (w - cw) // 2, (h - ch) // 2
    zoom = Image.fromarray(pixels).crop((x0, y0, x0 + cw, y0 + ch)).resize((w, h))
    zoomed = check_fidelity(np.asarray(zoom), guides)
    print(f"KAN-154 Cycles zoom 5 % : {[(m.name, m.value) for m in zoomed.measures]}")
    assert not zoomed.ok and "edge_scale" in zoomed.failed
