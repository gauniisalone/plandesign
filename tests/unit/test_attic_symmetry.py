"""KAN-72 : verticale corrigée par le pignon (72a) et toit supposé symétrique (72b), sur des
profondeurs synthétiques exactes. Un toit vraiment symétrique est retrouvé sans correction
inutile ; un toit dissymétrique (35° et 50°) n'est jamais forcé : ses deux pentes sont gardées."""

from __future__ import annotations

import math

import pytest

from openplan.pipeline.attic import SYMMETRY_MAX_DIFF_DEG, analyse_attic
from openplan.pipeline.autoplan import attic_notices
from tests.synthetic_depth import Face, SyntheticCamera, attic_faces, render_depth

CAMERA = SyntheticCamera(
    position=(0.6, 0.0, 1.45), yaw=0.0, pitch=-0.12, focal_px=260.0, width_px=640, height_px=480
)
"""Au milieu, grand angle : les deux jambettes sont vues (la symétrie n'est supposée qu'à
cette condition ; avec une seule, l'autre égout est recopié et rien n'est supposé de plus)."""
EAVE, HALF, LENGTH = 1.0, 2.5, 6.0


def _asymmetric(left_deg: float, right_deg: float) -> tuple[list[Face], float]:
    """Combles à deux pentes différentes : à gauche (y < faîtage) `left_deg`, à droite
    `right_deg`, égouts à 1,00 m sur des jambettes en y = ±2,5 m. Renvoie les faces et la
    hauteur vraie du faîtage."""
    tl, tr = math.tan(math.radians(left_deg)), math.tan(math.radians(right_deg))
    ridge_y = HALF * (tr - tl) / (tl + tr)
    ridge = EAVE + tl * (ridge_y + HALF)
    return [
        Face((0.0, 0.0, -1.0), 0.0),
        Face((-1.0, 0.0, 0.0), 0.0),
        Face((1.0, 0.0, 0.0), LENGTH),
        Face((0.0, 1.0, 0.0), HALF),
        Face((0.0, -1.0, 0.0), HALF),
        Face((0.0, -tl, 1.0), EAVE + HALF * tl),
        Face((0.0, tr, 1.0), EAVE + HALF * tr),
    ], ridge


def test_a_symmetric_roof_is_found_again_without_needless_correction() -> None:
    depth = render_depth(
        attic_faces(length=LENGTH, width=2 * HALF, ridge_height=3.2, eave_height=EAVE), CAMERA
    )
    estimate, _planes, _cls, _px, warnings = analyse_attic(
        depth, CAMERA.focal_px, CAMERA.principal_point
    )
    assert not any("verticale corrigée" in w for w in warnings), "la vraie verticale reste"
    assert estimate.symmetric_roof
    assert estimate.ridge_height is not None and estimate.width is not None
    assert estimate.ridge_height.value == pytest.approx(3.2, rel=0.03)
    assert estimate.width.value == pytest.approx(2 * HALF, rel=0.03)
    assert all(e.value == pytest.approx(EAVE, abs=0.05) for e in estimate.eave_heights)
    assert "toit supposé symétrique" in (estimate.ridge_height.note or "")


def test_an_asymmetric_roof_is_never_forced() -> None:
    faces, ridge = _asymmetric(35.0, 50.0)
    assert abs(35.0 - 50.0) > SYMMETRY_MAX_DIFF_DEG
    depth = render_depth(faces, CAMERA)
    estimate, *_ = analyse_attic(depth, CAMERA.focal_px, CAMERA.principal_point)
    assert not estimate.symmetric_roof
    assert sorted(estimate.slope_pitch_deg) == pytest.approx([35.0, 50.0], abs=2.0)
    assert estimate.ridge_height is not None
    assert estimate.ridge_height.value == pytest.approx(ridge, rel=0.05)
    assert "symétrique" not in (estimate.ridge_height.note or "")


def test_the_screen_says_the_roof_is_assumed_symmetric() -> None:
    from openplan.pipeline.autoplan import attic_geometry

    depth = render_depth(
        attic_faces(length=LENGTH, width=2 * HALF, ridge_height=3.2, eave_height=EAVE), CAMERA
    )
    estimate, *_ = analyse_attic(depth, CAMERA.focal_px, CAMERA.principal_point)
    from uuid import uuid4

    geometry = attic_geometry(estimate, uuid4(), uuid4())
    assert geometry is not None
    notices = attic_notices(estimate, geometry)
    assert notices and notices[0].startswith("Toit supposé symétrique : pente commune")
