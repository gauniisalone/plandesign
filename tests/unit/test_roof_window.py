"""D1 : velux dans une pente (schéma 0.2.0) : pose, cuisson percée, vitrage, plan, migration."""

from __future__ import annotations

import json
import math

import pytest

from openplan.pipeline.export import render_pdf, render_svg
from openplan.pipeline.scene3d import bake_level, split_panel
from openplan.scene.migrate import load_project
from openplan.scene.ops import AddRoofWindow, OpError, RemoveElement, apply_op
from openplan.schemas.common import SCHEMA_VERSION
from openplan.schemas.elements import RoofPlane, RoofWindow
from openplan.schemas.scene import Project
from openplan.validation.invariants import validate_invariants
from tests.factories import attic_project, only_level


def _with_two_windows() -> tuple[Project, list[RoofPlane]]:
    project = attic_project()
    level_id = only_level(project).id
    roofs = [e for e in only_level(project).elements.values() if isinstance(e, RoofPlane)]
    for roof in roofs:
        project = apply_op(project, level_id, AddRoofWindow(roof=roof.id, offset=1.0, distance=0.6))
    return project, roofs


def test_split_panel_leaves_exactly_the_holes() -> None:
    hole = (1.0, 1.78, 0.6, 1.58)
    pieces = split_panel(4.0, 3.0, [hole])
    assert sum((u1 - u0) * (v1 - v0) for u0, u1, v0, v1 in pieces) == pytest.approx(
        12.0 - 0.78 * 0.98
    )
    for u0, u1, v0, v1 in pieces:
        outside = (
            u1 <= hole[0] + 1e-9
            or u0 >= hole[1] - 1e-9
            or v1 <= hole[2] + 1e-9
            or v0 >= hole[3] - 1e-9
        )
        assert outside, "aucun morceau de toiture ne recouvre le velux"
    assert split_panel(4.0, 3.0, []) == [(0.0, 4.0, 0.0, 3.0)]


def test_roof_windows_are_baked_as_holes_with_frame_and_glass() -> None:
    project, roofs = _with_two_windows()
    level = only_level(project)
    windows = [e for e in level.elements.values() if isinstance(e, RoofWindow)]
    assert sorted(w.name for w in windows) == ["Velux 1", "Velux 2"]
    assert validate_invariants(project).valid
    bake = bake_level(project, level.id, style="realistic")
    glass = [p for p in bake.polyhedra if p.kind == "roof_window"]
    assert len(glass) == 2 and all(p.finish == "glass" for p in glass)
    roof_ids = {r.id for r in roofs}
    assert len([p for p in bake.polyhedra if p.kind == "roof" and p.element_id in roof_ids]) >= 8
    frames = [
        p for p in bake.polyhedra if p.element_id in {w.id for w in windows} and p.kind == "roof"
    ]
    assert len(frames) == 8, "quatre montants de cadre par velux"
    svg = render_svg(project, level.id)
    assert svg.count('class="roof-window"') == 2
    assert render_pdf(project, level.id).startswith(b"%PDF-")


def test_a_window_must_fit_its_roof_and_leaves_with_it() -> None:
    project = attic_project()
    level_id = only_level(project).id
    roof = next(e for e in only_level(project).elements.values() if isinstance(e, RoofPlane))
    with pytest.raises(OpError, match="dépasse de la pente"):
        apply_op(project, level_id, AddRoofWindow(roof=roof.id, offset=0.0, distance=40.0))
    with pytest.raises(OpError, match="pente inconnue"):
        apply_op(project, level_id, AddRoofWindow(roof=level_id, offset=0.0, distance=0.5))
    project, roofs = _with_two_windows()
    level_id = only_level(project).id
    after = apply_op(project, level_id, RemoveElement(element_id=roofs[0].id))
    left = [e for e in only_level(after).elements.values() if isinstance(e, RoofWindow)]
    assert len(left) == 1 and left[0].roof == roofs[1].id


def test_projects_0_1_0_still_open() -> None:
    data = json.loads(attic_project().model_dump_json())
    data["schema_version"] = "0.1.0"
    assert load_project(data).schema_version == SCHEMA_VERSION == "0.16.0"


def test_every_roof_dips_under_its_eave_to_close_the_gap() -> None:
    """Retour PO (4 oct.) : avec un égout à 0, un filet de ciel passait entre pente et plancher ;
    chaque pente se prolonge sous l'égout, sous le niveau du plancher."""
    from openplan.pipeline.scene3d import ROOF_SKIRT_M

    for project in (attic_project(), _with_two_windows()[0]):
        level = only_level(project)
        bake = bake_level(project, level.id, style="realistic")
        roofs = {e.id for e in level.elements.values() if isinstance(e, RoofPlane)}
        for roof_id in roofs:
            parts = [p for p in bake.polyhedra if p.element_id == roof_id and p.kind == "roof"]
            lowest = min(v.z for p in parts for v in p.vertices)
            eave = next(e for e in level.elements.values() if e.id == roof_id)
            assert isinstance(eave, RoofPlane)
            assert lowest < eave.eave_height - 0.9 * ROOF_SKIRT_M * math.tan(eave.pitch)


def test_the_eave_sill_stays_under_the_floor_even_with_knee_walls() -> None:
    """La semelle qui ferme le dessous du plancher reste sous le plancher, même avec un muret
    d'égout de 1 m (sinon elle forme une corniche dans la pièce)."""
    project = attic_project()  # égouts à 1 m
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    sills = [p for p in bake.polyhedra if p.kind == "slab" and p.part == 1001]
    assert sills and all(max(v.z for v in p.vertices) <= level.elevation - 0.019 for p in sills)
