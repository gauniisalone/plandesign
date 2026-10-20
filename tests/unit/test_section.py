"""Coupe verticale cotée (E3) : profil tranché de la scène et PDF."""

from __future__ import annotations

import pytest

from openplan.pipeline.render import section_keeps_above, section_label
from openplan.pipeline.section import render_section_pdf, section_profile
from openplan.schemas.bake import RenderCamera
from openplan.schemas.common import Point3
from tests.factories import attic_project, only_level, rect_room_project


def test_profile_of_a_straight_room() -> None:
    """Pièce 4 × 3 m, hauteur 2,5 m, coupée en x = 2 : les deux murs et le sol."""
    project = rect_room_project()
    level = only_level(project)
    profile = section_profile(project, level.id, "x", 2.0)
    assert profile.structure and not profile.has_roof
    bounds = profile.bounds
    assert bounds is not None
    umin, _, umax, vmax = bounds
    assert umin == pytest.approx(-0.075, abs=0.05) and umax == pytest.approx(3.075, abs=0.05)
    assert vmax == pytest.approx(2.5, abs=0.15)
    assert profile.floor_span is not None
    assert profile.floor_span[1] - profile.floor_span[0] == pytest.approx(3.0, abs=0.2)


def test_profile_of_the_attic_reaches_the_ridge() -> None:
    project = attic_project()
    level = only_level(project)
    across = section_profile(project, level.id, "x", 3.0)
    along = section_profile(project, level.id, "y", 2.5)
    assert across.has_roof or along.has_roof
    tops = [p.bounds[3] for p in (across, along) if p.bounds is not None]
    assert max(tops) == pytest.approx(3.2, abs=0.35)


def test_section_pdf_is_dimensioned() -> None:
    project = rect_room_project()
    level = only_level(project)
    pdf = render_section_pdf(project, level.id, "y", 1.5)
    assert pdf.startswith(b"%PDF-")
    assert b"hors tout" in pdf and b"rieur" in pdf  # intérieur (accent encodé)
    assert b"Coupe y = 1,50 m" in pdf


def test_section_pdf_outside_the_building_says_so() -> None:
    project = rect_room_project()
    level = only_level(project)
    assert b"ne traverse rien" in render_section_pdf(project, level.id, "x", 50.0)


def test_render_removes_the_camera_side() -> None:
    camera = RenderCamera(
        position=Point3(x=1.0, y=4.0, z=1.5),
        yaw=0.0,
        pitch=0.0,
        focal_px=900.0,
        width_px=640,
        height_px=480,
    )
    assert section_keeps_above(("x", 3.0), camera), "caméra en x = 1 : on garde x > 3"
    assert not section_keeps_above(("y", 3.0), camera), "caméra en y = 4 : on garde y < 3"
    assert not section_keeps_above(("z", 1.0), camera), "coupe horizontale : le dessous"
    assert not section_keeps_above(None, camera)


def test_section_view_faces_the_cut_and_frames_it() -> None:
    """E3b : perpendiculaire au plan, du côté retiré, assez loin pour toute la coupe."""
    import math

    from openplan.pipeline.render import NoCameraError, section_view

    project = rect_room_project()
    level = only_level(project)
    across = section_view(project, level.id, "x", 2.0)
    assert across.position.x > 2.0 and across.yaw == pytest.approx(math.pi, abs=1e-3)
    assert across.position.y == pytest.approx(1.5, abs=0.1)
    assert across.name == "Coupe x = 2,00 m" and across.pitch == 0.0
    # Toute la largeur coupée (3,15 m) tient dans les 1280 px.
    half_fov = math.atan(640.0 / (across.focal_px or 1.0))
    assert math.tan(half_fov) * (across.position.x - 2.0) >= 3.15 / 2
    along = section_view(project, level.id, "y", 1.0)
    assert along.position.y > 1.0 and along.yaw == pytest.approx(-math.pi / 2, abs=1e-3)
    with pytest.raises(NoCameraError):
        section_view(project, level.id, "z", 1.0)
    with pytest.raises(NoCameraError):
        section_view(project, level.id, "x", 50.0)


def test_section_label() -> None:
    assert section_label(None) is None
    assert section_label(("z", 1.2)) == "horizontale à 1,20 m"
    assert section_label(("x", 3.0)) == "verticale x = 3,00 m"
