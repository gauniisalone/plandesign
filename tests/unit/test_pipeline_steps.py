"""Édition validée, calibration de niveau, dérivés, export SVG et JSON."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from openplan.geom.scale import CalibrationError
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.calibration import calibrate_level
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.editing import InvalidEditError, edit
from openplan.pipeline.export import (
    PAGE_SIZES,
    choose_scale,
    export_dxf,
    export_json,
    export_pdf,
    export_svg,
    format_length,
    render_dxf,
    render_pdf,
    render_svg,
)
from openplan.scene.migrate import load_project
from openplan.scene.ops import AddOpening, MoveVertex, UpdateElement
from openplan.schemas import Opening, Project, Room, Source, Wall
from openplan.schemas.common import Point2
from openplan.schemas.elements import OpeningKind
from tests.factories import by_name, only_level


def test_edit_accepts_valid_and_rejects_invalid(rect_project: Project) -> None:
    lid = only_level(rect_project).id
    w2 = by_name(only_level(rect_project), "W2", Wall)
    after, report = edit(
        rect_project, lid, UpdateElement(element_id=w2.id, fields={"thickness": 0.2})
    )
    assert report.valid and len(after.history) == 1
    with pytest.raises(InvalidEditError, match="dépasse le mur") as info:
        edit(
            rect_project,
            lid,
            AddOpening(host_wall=w2.id, kind=OpeningKind.WINDOW, offset=2.8, width=1.0, height=1.0),
        )
    assert info.value.report.violations[0].type == "hosted_element_exceeds_wall"


def test_calibrate_level_scales_and_marks_measured(rect_project: Project) -> None:
    lid = only_level(rect_project).id
    (m,) = rect_project.measurements.values()
    project = rect_project.model_copy(
        update={"measurements": {m.id: m.model_copy(update={"value": 3.1})}}
    )
    _, preview = calibrate_level(project, lid, dry_run=True)
    assert preview.scale_factor == pytest.approx(0.775)

    after, calibration = calibrate_level(project, lid)
    level = only_level(after)
    graph = WallGraph.from_level(level)
    w1, w2 = by_name(level, "W1", Wall), by_name(level, "W2", Wall)
    assert graph.wall_length(w1.id) == pytest.approx(3.1)
    assert graph.wall_length(w2.id) == pytest.approx(3.0 * 0.775)
    assert w1.source is Source.MEASURED and w1.uncertainty.sigma == m.tolerance
    assert w2.source is Source.ESTIMATED
    assert after.calibrations == [calibration]
    assert after.history[-1].actor == "pipeline:calibration"


def test_calibrate_level_errors(rect_project: Project) -> None:
    with pytest.raises(CalibrationError, match="niveau inconnu"):
        calibrate_level(rect_project, uuid4())
    empty = rect_project.model_copy(update={"measurements": {}})
    with pytest.raises(CalibrationError, match="aucune mesure"):
        calibrate_level(empty, only_level(rect_project).id)


def test_compute_derived(rect_project: Project) -> None:
    level = only_level(rect_project)
    derived = compute_derived(level)
    assert len(derived.walls) == 4 and len(derived.rooms) == 1
    room = by_name(level, "R1", Room)
    assert derived.rooms[room.id].area_m2 == pytest.approx(3.85 * 2.85)
    assert derived.bounds is not None and derived.bounds.max == Point2(x=4, y=3)
    assert len(derived.loops) == 1 and set(derived.loops[0]) == set(room.boundary_walls)
    w1 = by_name(level, "W1", Wall)
    assert derived.walls[w1.id].normal == Point2(x=0.0, y=1.0)


def test_compute_derived_empty_level() -> None:
    from openplan.scene.factory import new_project

    derived = compute_derived(only_level(new_project("vide")))
    assert derived.bounds is None and derived.walls == {} and derived.loops == []


def test_render_svg_content(rect_project: Project) -> None:
    level = only_level(rect_project)
    svg = render_svg(rect_project, level.id)
    assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
    assert svg.count('class="wall"') == 1, "seul W1 est mesuré"
    assert svg.count('class="wall uncertain"') == 3
    assert "4.00 m" in svg and "≈ 3.00 m" in svg
    assert 'class="opening window' in svg and 'class="swing"' in svg
    assert "R1" in svg and "10.97 m²" in svg
    assert "relevé certifié" in svg


def test_render_svg_units_and_empty(rect_project: Project) -> None:
    level = only_level(rect_project)
    cm = rect_project.model_copy(update={"units_display": "cm"})
    assert "400 cm" in render_svg(cm, level.id)
    assert format_length(1.234, "mm") == "1234 mm"
    from openplan.scene.factory import new_project

    empty = new_project("vide")
    assert "Niveau vide" in render_svg(empty, only_level(empty).id)


def test_render_pdf_scale_dimensions_and_title_block(rect_project: Project) -> None:
    level = only_level(rect_project)
    pdf = render_pdf(rect_project, level.id)
    assert pdf.startswith(b"%PDF-") and pdf.rstrip().endswith(b"%%EOF")
    # Flux non compressés : les textes sont lisibles dans le fichier (polices standard).
    assert b"(4.00 m)" in pdf and b"(env. 3.00 m)" in pdf, "cote mesurée et cote estimée"
    assert b"(R1)" in pdf and b"10.97 m" in pdf
    assert b"chelle 1:50" in pdf and b"relev" in pdf, "cartouche : échelle et avertissement"
    assert b"Helvetica-Oblique" in pdf, "cotes estimées en italique"
    assert choose_scale(4.0, 3.0, PAGE_SIZES["A4"]) == 50
    assert choose_scale(30.0, 12.0, PAGE_SIZES["A4"]) == 200
    assert choose_scale(300.0, 300.0, PAGE_SIZES["A4"]) == 1000
    big = render_pdf(rect_project, level.id, page_format="A3")
    assert b"chelle 1:50" in big and len(big) > 1000
    from openplan.scene.factory import new_project
    from tests.factories import attic_project

    empty = new_project("vide")
    assert b"(Niveau vide)" in render_pdf(empty, only_level(empty).id)
    attic = attic_project()
    attic_pdf = render_pdf(attic, only_level(attic).id)
    assert attic_pdf.count(b"1,80 m sous pente") == 2 and b"(Combles)" in attic_pdf
    with pytest.raises(Exception, match="niveau inconnu"):
        render_pdf(rect_project, uuid4())


def test_render_dxf_layers_and_entities(rect_project: Project) -> None:
    import io

    import ezdxf

    level = only_level(rect_project)
    doc = ezdxf.read(io.StringIO(render_dxf(rect_project, level.id)))
    assert doc.header["$INSUNITS"] == 6, "mètres"
    msp = doc.modelspace()
    by_layer: dict[str, list[str]] = {}
    for entity in msp:
        by_layer.setdefault(entity.dxf.layer, []).append(entity.dxftype())
    assert by_layer["MURS"] == ["LWPOLYLINE"], "seul W1 est mesuré"
    assert by_layer["MURS_ESTIMES"].count("LWPOLYLINE") == 3
    assert doc.layers.get("MURS_ESTIMES").dxf.linetype == "DASHED"
    assert sorted(by_layer["OUVERTURES"]) == ["ARC", "LINE", "LWPOLYLINE", "LWPOLYLINE"]
    texts = [e.dxf.text for e in msp.query("TEXT")]
    assert "R1" in texts and "10.97 m²" in texts and "4.00 m" in texts and "≈ 3.00 m" in texts
    assert by_layer["COTES"].count("TEXT") == 4
    from openplan.scene.factory import new_project
    from tests.factories import attic_project

    attic = attic_project()
    attic_doc = ezdxf.read(io.StringIO(render_dxf(attic, only_level(attic).id)))
    pentes = list(attic_doc.modelspace().query('*[layer=="PENTES"]'))
    assert [e.dxftype() for e in pentes].count("LINE") == 4 and len(pentes) == 6
    empty = new_project("vide")
    assert "Niveau vide" in render_dxf(empty, only_level(empty).id)


def test_export_files(rect_project: Project, tmp_path: Path) -> None:
    level = only_level(rect_project)
    svg = export_svg(rect_project, level.id, tmp_path / "out" / "plan.svg")
    assert svg.path.is_file() and svg.media_type == "image/svg+xml"
    pdf = export_pdf(rect_project, level.id, tmp_path / "out" / "plan.pdf")
    assert pdf.path.read_bytes().startswith(b"%PDF-") and pdf.media_type == "application/pdf"
    dxf = export_dxf(rect_project, level.id, tmp_path / "out" / "plan.dxf")
    assert dxf.path.read_text(encoding="utf-8").startswith("  0\nSECTION")
    js = export_json(rect_project, tmp_path / "out" / "p.json")
    import json

    assert load_project(json.loads(js.path.read_text())) == rect_project


def test_edit_move_then_svg_reflects_change(rect_project: Project) -> None:
    lid = only_level(rect_project).id
    w1 = by_name(only_level(rect_project), "W1", Wall)
    after, _ = edit(rect_project, lid, MoveVertex(vertex_id=w1.end_vertex, to=Point2(x=5, y=0)))
    assert "5.00 m" in render_svg(after, lid)
    door = by_name(only_level(after), "door", Opening)
    assert door.host_wall == w1.id
