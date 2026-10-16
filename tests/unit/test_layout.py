"""Hauteur utile sous pente : surface utilisable par pièce et meubles qui ne tiennent pas."""

from __future__ import annotations

import math

import pytest

from openplan.geom.roof import height_under_slope
from openplan.pipeline.derived import compute_derived
from openplan.pipeline.export import render_pdf, render_svg
from openplan.schemas import (
    BBox3,
    Constraint,
    ConstraintKind,
    Furniture,
    Point3,
    Source,
    Transform3,
)
from openplan.validation.invariants import validate_invariants
from openplan.validation.layout import check_layout, footprint_corners
from tests.factories import ATTIC, attic_project, only_level

L, W, RIDGE, EAVE = ATTIC["length"], ATTIC["width"], ATTIC["ridge_height"], ATTIC["eave_height"]
T = 0.15
SLOPE = (RIDGE - EAVE) / (W / 2)


def test_height_under_slope() -> None:
    eave = ((0.0, 0.0), (6.0, 0.0))
    kwargs = {
        "inward": (0.0, 1.0),
        "eave_height": 1.0,
        "pitch": math.atan(SLOPE),
        "top_height": 3.2,
    }
    assert height_under_slope((3.0, 0.0), *eave, **kwargs) == pytest.approx(1.0)
    assert height_under_slope((3.0, 1.0), *eave, **kwargs) == pytest.approx(1.0 + SLOPE)
    assert height_under_slope((3.0, 2.5), *eave, **kwargs) == pytest.approx(3.2)
    assert height_under_slope((3.0, 4.0), *eave, **kwargs) == pytest.approx(3.2), "plafonné"
    assert height_under_slope((3.0, -0.5), *eave, **kwargs) == pytest.approx(1.0), "derrière"


def test_usable_area_of_the_attic_room() -> None:
    project = attic_project()
    level = only_level(project)
    room = next(iter(compute_derived(level).rooms.values()))
    # Bande basse par versant : de l'égout (mur intérieur à T/2) à la ligne 1,80 m.
    strip = ((1.8 - EAVE) / SLOPE - T / 2) * (L - T)
    expected = room.area_m2 - 2 * strip
    assert room.usable_area_m2 == pytest.approx(expected, rel=1e-3)
    assert 0 < expected < room.area_m2
    svg = render_svg(project, level.id)
    assert f"{expected:.2f} m² à plus de 1,80 m" in svg
    assert f"{expected:.2f} m" in render_pdf(project, level.id).decode("latin-1")


def furniture(x: float, y: float, height: float, yaw: float = 0.0) -> Furniture:
    return Furniture(
        name="Armoire",
        asset_id="test",
        category="storage",
        transform=Transform3(position=Point3(x=x, y=y, z=0.0), yaw=yaw),
        bbox=BBox3(width=1.0, depth=0.6, height=height),
        source=Source.USER_DEFINED,
        confidence=1.0,
    )


def test_furniture_under_slope_is_flagged() -> None:
    project = attic_project()
    level = only_level(project)
    near_eave = furniture(3.0, 0.5, 2.0)  # hauteur libre ≈ 1,0 + 0,88 × 0,2 = 1,18 m au bord
    centre = furniture(3.0, 2.5, 2.0)  # sous le faîtage : 3,2 m
    elements = {**level.elements, near_eave.id: near_eave, centre.id: centre}
    project = project.model_copy(
        update={"levels": {level.id: level.model_copy(update={"elements": elements})}}
    )
    violations = check_layout(project)
    assert [v.element_ids for v in violations] == [[near_eave.id]]
    assert violations[0].severity == "high" and "reculer" in violations[0].message
    assert not validate_invariants(project).valid
    # Contrainte souple : sévérité moyenne, projet valide ; désactivée : rien.
    soft = Constraint(
        kind=ConstraintKind.MAX_HEIGHT_UNDER_SLOPE, element_ids=[near_eave.id], hard=False
    )
    project_soft = project.model_copy(update={"constraints": {soft.id: soft}})
    assert check_layout(project_soft)[0].severity == "medium"
    assert validate_invariants(project_soft).valid
    off = soft.model_copy(update={"enabled": False})
    assert check_layout(project.model_copy(update={"constraints": {off.id: off}})) == []
    # Un objet tourné de 90° près de l'égout dépasse aussi par ses coins.
    turned = furniture(3.0, 0.9, 1.9, yaw=math.pi / 2)  # emprise 0,6 × 1,0 → coin à y = 0,4
    corners = footprint_corners(3.0, 0.9, math.pi / 2, 1.0, 0.6)
    assert min(y for _, y in corners) == pytest.approx(0.4)
    project_turned = project.model_copy(
        update={
            "levels": {
                level.id: level.model_copy(
                    update={"elements": {**level.elements, turned.id: turned}}
                )
            }
        }
    )
    assert [v.element_ids for v in check_layout(project_turned)] == [[turned.id]]
