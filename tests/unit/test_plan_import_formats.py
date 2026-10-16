"""Import de plan (U1b) : PDF vectoriel, SVG, image de plan vectorisée."""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.ingestion import import_image
from openplan.pipeline.plan_import import (
    PT_TO_M,
    PlanApplyBody,
    apply_segments,
    guess_paper_scale,
    paper_scale_from_text,
    preview_image,
    preview_pdf,
    preview_svg,
)
from openplan.scene.factory import new_project
from openplan.scene.ops import UpdateLevel, apply_op
from openplan.schemas import Source
from openplan.schemas.elements import Wall
from tests.factories import only_level

W_PT = 4.0 / 50 / PT_TO_M  # 4 m à 1:50 = 8 cm = 226,8 pt
H_PT = 3.0 / 50 / PT_TO_M


def _pdf(draw: Callable[[Any], None]) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(500, 400))
    draw(c)
    c.showPage()
    c.save()
    return buf.getvalue()


def _lengths(preview: Any, layer: str | None = None) -> list[float]:
    return sorted(round(s.length, 2) for s in preview.segments if layer is None or s.layer == layer)


def test_pdf_reads_the_written_scale_and_ticks_thick_strokes() -> None:
    def draw(c: Any) -> None:
        c.setLineWidth(3)
        c.rect(50, 50, W_PT, H_PT, stroke=1, fill=0)
        c.setLineWidth(0.5)
        c.line(50, 30, 50 + W_PT, 30)  # cote
        c.drawString(60, 15, "Plan RDC — Échelle 1:50")

    preview = preview_pdf(_pdf(draw))
    assert preview.kind == "pdf" and preview.paper_scale == 50
    assert "lue sur le plan" in preview.units and not preview.warnings
    assert _lengths(preview, "trait 3.0 pt") == [3.0, 3.0, 4.0, 4.0]
    assert _lengths(preview, "trait 0.5 pt") == [4.0]
    selected = {layer.name: layer.selected for layer in preview.layers}
    assert selected == {"trait 3.0 pt": True, "trait 0.5 pt": False}
    # Un trait diagonal garde son sens (Y vers le haut).
    diag = preview_pdf(_pdf(lambda c: c.line(100, 100, 300, 200)))
    seg = diag.segments[0]
    assert (seg.end.y > seg.start.y) == (seg.end.x > seg.start.x)


def test_pdf_without_scale_guesses_and_can_be_overridden() -> None:
    def draw(c: Any) -> None:
        c.setLineWidth(2)
        c.rect(50, 50, W_PT, H_PT, stroke=1, fill=0)

    guessed = preview_pdf(_pdf(draw))
    assert guessed.paper_scale == 50 and "supposée" in guessed.units and guessed.warnings
    assert _lengths(guessed) == [3.0, 3.0, 4.0, 4.0]
    chosen = preview_pdf(_pdf(draw), paper_scale=100)
    assert chosen.paper_scale == 100 and "(choisie)" in chosen.units
    assert _lengths(chosen) == [6.0, 6.0, 8.0, 8.0]
    assert paper_scale_from_text("ech. 1 / 100") == 100
    assert paper_scale_from_text("sans rien") is None
    assert guess_paper_scale(0.08)[0] == 50 and guess_paper_scale(0.02)[0] == 200


def test_pdf_filled_rectangles_are_walls_with_their_thickness() -> None:
    thick_pt = 0.15 / 50 / PT_TO_M  # 15 cm à 1:50

    def draw(c: Any) -> None:
        c.rect(50, 50, W_PT, thick_pt, stroke=0, fill=1)
        c.rect(50, 50, thick_pt, H_PT, stroke=0, fill=1)
        c.drawString(60, 15, "1:50")

    preview = preview_pdf(_pdf(draw))
    assert [s.layer for s in preview.segments] == ["murs pleins", "murs pleins"]
    assert all(s.thickness == pytest.approx(0.15, abs=0.002) for s in preview.segments)
    # Axes raccordés à l'angle : chaque axe perd une demi-épaisseur à son bout commun.
    assert _lengths(preview) == pytest.approx([3.0, 4.0], abs=0.1)


def test_pdf_refusals() -> None:
    with pytest.raises(DetectionError, match="illisible"):
        preview_pdf(b"%PDF-1.4 pas vraiment")
    empty = _pdf(lambda c: c.drawString(10, 10, "texte seul"))
    with pytest.raises(DetectionError, match="aucun tracé vectoriel"):
        preview_pdf(empty)


SVG_PAPER = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg" xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     width="210mm" height="297mm" viewBox="0 0 210 297">
  <text x="10" y="290">Échelle 1:50</text>
  <g id="Murs">
    <rect x="10" y="10" width="80" height="60" fill="none" stroke="black"/>
  </g>
  <g inkscape:label="Cotes" transform="translate(0 5)">
    <path d="M 10 100 h 80 v 20"/>
    <line x1="10" y1="140" x2="90" y2="140"/>
  </g>
</svg>
"""


def test_svg_in_millimetres_with_scale_layers_and_transforms() -> None:
    preview = preview_svg(SVG_PAPER.encode("utf-8"))
    assert preview.kind == "svg" and preview.paper_scale == 50
    assert _lengths(preview, "Murs") == [3.0, 3.0, 4.0, 4.0]
    assert _lengths(preview, "Cotes") == [1.0, 4.0, 4.0]
    selected = {layer.name: layer.selected for layer in preview.layers}
    assert selected == {"Murs": True, "Cotes": False}
    # Y retourné : le bord haut du rectangle SVG (y = 10) est le plus haut dans le plan.
    murs = [s for s in preview.segments if s.layer == "Murs"]
    assert max(max(s.start.y, s.end.y) for s in murs) == pytest.approx(
        max(max(s.start.y, s.end.y) for s in preview.segments)
    )
    chosen = preview_svg(SVG_PAPER.encode("utf-8"), paper_scale=100)
    assert _lengths(chosen, "Murs") == [6.0, 6.0, 8.0, 8.0]


def test_svg_without_units_guesses_like_a_dxf_and_refuses_junk() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 5000 4000">'
        '<polygon points="0,0 4000,0 4000,3000 0,3000"/></svg>'
    )
    preview = preview_svg(svg.encode("utf-8"))
    assert preview.paper_scale is None and "millimètres" in preview.units
    assert _lengths(preview) == [3.0, 3.0, 4.0, 4.0]
    with pytest.raises(DetectionError, match="illisible"):
        preview_svg(b"<svg")
    with pytest.raises(DetectionError, match="aucun tracé"):
        preview_svg(b'<svg xmlns="http://www.w3.org/2000/svg"><text>rien</text></svg>')


def _plan_png() -> bytes:
    import cv2
    from PIL import Image

    img = np.full((600, 800), 255, dtype=np.uint8)
    cv2.rectangle(img, (100, 100), (700, 500), 0, thickness=10)  # murs épais
    cv2.line(img, (100, 550), (700, 550), 0, 1)  # cote fine, à ignorer
    cv2.putText(img, "3.00 m", (350, 580), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 1)
    buf = io.BytesIO()
    Image.fromarray(img).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def test_image_plan_thick_strokes_become_estimated_walls(tmp_path: Path) -> None:
    project = new_project("scan")
    project_dir = tmp_path / "p"
    project_dir.mkdir()
    project, ref, _ = import_image(project, project_dir, _plan_png(), original_name="plan.png")
    level_id = only_level(project).id
    with pytest.raises(DetectionError, match="fond du plan"):
        preview_image(project, project_dir, level_id, ref.id)
    # Fond calé : 5 mm par pixel (outil Cote), posé en (1 ; 2).
    project = apply_op(
        project,
        level_id,
        UpdateLevel(
            fields={
                "background_image": str(ref.id),
                "background_transform": {"scale": 0.005, "tx": 1.0, "ty": 2.0},
            }
        ),
    )
    preview = preview_image(project, project_dir, level_id, ref.id)
    assert preview.kind == "image" and preview.merged_pairs == 4
    assert len(preview.segments) == 4, [s.model_dump() for s in preview.segments]
    lengths = _lengths(preview)
    assert lengths[:2] == pytest.approx([2.0, 2.0], abs=0.15)
    assert lengths[2:] == pytest.approx([3.0, 3.0], abs=0.15)
    assert all(s.thickness == pytest.approx(0.05, abs=0.02) for s in preview.segments)
    assert preview.bounds is not None
    assert preview.bounds.min.x == pytest.approx(1.5, abs=0.1)
    assert preview.bounds.min.y == pytest.approx(2.5, abs=0.1)
    assert any("heuristique" in w for w in preview.warnings)
    after, result = apply_segments(
        project,
        level_id,
        PlanApplyBody(segments=preview.segments, kind="image", source="estimated"),
    )
    walls = [e for e in only_level(after).elements.values() if isinstance(e, Wall)]
    assert len(walls) == 4 and all(w.source is Source.ESTIMATED for w in walls)
    assert result.step == "plan_import.image" and "image de plan" in after.history[-1].label
