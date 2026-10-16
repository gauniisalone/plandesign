"""Import de plan DXF (U1) : unités, calques, double trait, application en murs observés."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

import pytest

from openplan.pipeline.floorplan import DetectionError
from openplan.pipeline.plan_import import (
    ACTOR,
    STEP,
    PlanApplyBody,
    apply_segments,
    merge_double_lines,
    preview_dxf,
)
from openplan.scene.factory import new_project
from openplan.schemas import Source
from openplan.schemas.elements import Room, Wall
from tests.factories import only_level, rect_room_project


def _dxf(draw: Callable[[Any], object], *, insunits: int | None = 4) -> bytes:
    import ezdxf

    doc = ezdxf.new("R2010")
    # ezdxf déclare des mètres par défaut : 0 = « sans unité », comme un export sans en-tête.
    doc.header["$INSUNITS"] = insunits if insunits is not None else 0
    doc.layers.add("MURS")
    doc.layers.add("COTES")
    draw(doc.modelspace())
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


def _rect(msp: Any, w: float, h: float, layer: str, ox: float = 0.0, oy: float = 0.0) -> None:
    msp.add_lwpolyline(
        [(ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)],
        close=True,
        dxfattribs={"layer": layer},
    )


def test_single_line_rectangle_in_millimetres_gives_four_segments_in_metres() -> None:
    def draw(msp: Any) -> None:
        _rect(msp, 4000, 3000, "MURS")
        msp.add_line((0, -500), (4000, -500), dxfattribs={"layer": "COTES"})

    preview = preview_dxf(_dxf(draw))
    assert preview.kind == "dxf" and "millimètres" in preview.units and not preview.warnings
    assert preview.entity_count == 5 and len(preview.segments) == 5
    murs = [s for s in preview.segments if s.layer == "MURS"]
    assert len(murs) == 4 and all(s.thickness is None for s in murs)
    assert {round(s.length, 3) for s in murs} == {4.0, 3.0}
    # Le calque « MURS » est coché, « COTES » non ; l'origine est ramenée au coin bas gauche.
    assert [(layer.name, layer.selected) for layer in preview.layers] == [
        ("COTES", False),
        ("MURS", True),
    ]
    assert preview.bounds is not None
    assert preview.bounds.min.x == 0.0 and preview.bounds.max.x == 4.0


def test_double_line_walls_are_merged_with_their_thickness() -> None:
    def draw(msp: Any) -> None:
        _rect(msp, 4000, 3000, "MURS")  # trait extérieur
        _rect(msp, 3700, 2700, "MURS", 150, 150)  # trait intérieur : murs de 15 cm

    preview = preview_dxf(_dxf(draw))
    assert preview.merged_pairs == 4, [s.model_dump() for s in preview.segments]
    assert len(preview.segments) == 4
    assert all(s.thickness == 0.15 for s in preview.segments)
    lengths = sorted(round(s.length, 2) for s in preview.segments)
    # Axes médians raccordés aux angles : 3,85 × 2,85 m (extérieur moins une épaisseur).
    assert lengths == [2.85, 2.85, 3.85, 3.85]


def test_unitless_file_guesses_millimetres_and_says_so() -> None:
    preview = preview_dxf(_dxf(lambda msp: _rect(msp, 4000, 3000, "0"), insunits=None))
    assert "devinés" in preview.units and preview.warnings
    assert len(preview.layers) == 1 and preview.layers[0].selected


def test_apply_makes_observed_walls_one_room_one_undoable_commit() -> None:
    project = new_project("plan importé")
    level_id = only_level(project).id
    preview = preview_dxf(_dxf(lambda msp: _rect(msp, 4000, 3000, "MURS")))
    after, result = apply_segments(project, level_id, PlanApplyBody(segments=preview.segments))
    level = only_level(after)
    walls = [e for e in level.elements.values() if isinstance(e, Wall)]
    rooms = [e for e in level.elements.values() if isinstance(e, Room)]
    assert len(walls) == 4 and len(rooms) == 1 and len(level.vertices) == 4
    assert all(w.source is Source.OBSERVED and w.provenance.step == STEP for w in walls)
    assert after.history[-1].actor == ACTOR and "DXF" in after.history[-1].label
    assert result.step == STEP and result.assumptions
    # Correction d'échelle par l'utilisateur : facteur appliqué aux positions.
    scaled, _ = apply_segments(
        project, level_id, PlanApplyBody(segments=preview.segments, scale=0.5)
    )
    xs = [v.position.x for v in only_level(scaled).vertices.values()]
    assert max(xs) == pytest.approx(2.0)


def test_refusals_are_explicit() -> None:
    with pytest.raises(DetectionError, match=r"illisible|aucun tracé"):
        preview_dxf(b"pas un dxf")
    project = rect_room_project()
    preview = preview_dxf(_dxf(lambda msp: _rect(msp, 4000, 3000, "MURS")))
    with pytest.raises(DetectionError, match="déjà des murs"):
        apply_segments(project, only_level(project).id, PlanApplyBody(segments=preview.segments))


def test_merge_leaves_unrelated_segments_alone() -> None:
    def draw(msp: Any) -> None:
        msp.add_line((0, 0), (4000, 0))
        msp.add_line((0, 2000), (4000, 2000))

    preview = preview_dxf(_dxf(draw))
    merged, pairs = merge_double_lines(preview.segments)
    assert pairs == 0 and len(merged) == 2
