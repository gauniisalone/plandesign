"""KAN-45 : photo de combles prise en levant l'appareil. Scène de C2 (tests.control_attic),
profondeur exacte, sans GPU. Le sol sort du champ vers +8° (demi-champ vertical de 30°) : la
verticale est alors prise entre les deux pentes, le sol posé à la hauteur de prise de vue, et
les jambettes cherchées de chaque côté du faîtage."""

from __future__ import annotations

import math

import pytest

from openplan.pipeline.attic import AtticEstimate, analyse_attic, rescale
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from tests.control_attic import CAMERA_HEIGHT, EAVE, GABLE_DISTANCE, LENGTH, RIDGE, WIDTH
from tests.synthetic_depth import SyntheticCamera, attic_faces, render_depth

FALLBACK = "Sol hors champ"


def _camera(pitch_deg: float) -> SyntheticCamera:
    return SyntheticCamera(
        position=(LENGTH - GABLE_DISTANCE, 0.0, CAMERA_HEIGHT),
        yaw=0.0,
        pitch=math.radians(pitch_deg),
        focal_px=420.0,
        width_px=640,
        height_px=480,
    )


def _estimate(pitch_deg: float, camera_height_m: float | None) -> tuple[AtticEstimate, list[str]]:
    cam = _camera(pitch_deg)
    faces = attic_faces(length=LENGTH, width=WIDTH, ridge_height=RIDGE, eave_height=EAVE)
    depth = render_depth(faces, cam)
    est, _planes, cls, _pixels, warnings = analyse_attic(
        depth, cam.focal_px, cam.principal_point, camera_height_m=camera_height_m
    )
    assert cls is not None, warnings
    # Comme run_attic : la hauteur saisie recale l'échelle (sans effet si le sol est supposé).
    if camera_height_m is not None:
        est = rescale(est, camera_height_m)
    return est, warnings


def _check(est: AtticEstimate, rel: float = 0.10) -> None:
    assert est.ridge_height is not None
    assert est.ridge_height.value == pytest.approx(RIDGE, rel=rel)
    assert len(est.eave_heights) == 2
    assert all(e.value == pytest.approx(EAVE, rel=rel) for e in est.eave_heights)


@pytest.mark.parametrize("pitch_deg", [-15, -10, -5, 0, 5, 8])
def test_floor_in_view_takes_the_usual_path(pitch_deg: float) -> None:
    """Must have 2 : sol vu (horizontale ou regard baissé, jusqu'à +8°), chemin habituel."""
    est, warnings = _estimate(pitch_deg, CAMERA_HEIGHT)
    assert not any(FALLBACK in w for w in warnings)
    _check(est)


@pytest.mark.parametrize("pitch_deg", [10, 12, 15])
def test_raised_photo_still_estimates_the_attic(pitch_deg: float) -> None:
    """Must have 1 : regard levé (15° au ticket), égout et faîtage à ±10 %."""
    est, warnings = _estimate(pitch_deg, CAMERA_HEIGHT)
    assert any(FALLBACK in w and "hauteur saisie" in w for w in warnings)
    _check(est)
    assert est.camera_height is not None
    assert est.camera_height.value == pytest.approx(CAMERA_HEIGHT)
    assert est.camera_height.source == "user_defined"


def test_raised_photo_without_entered_height_assumes_the_default() -> None:
    """Sans hauteur saisie : sol supposé à la hauteur par défaut (1,50 m pour 1,45), signalé ;
    l'erreur ne porte que sur ce décalage, dans les ±10 %."""
    est, warnings = _estimate(15, None)
    assert any(FALLBACK in w and "hauteur supposée" in w for w in warnings)
    _check(est)
    assert est.camera_height is not None
    assert est.camera_height.value == DEFAULT_CAMERA_HEIGHT_M
    assert est.camera_height.source == "inferred"
    assert est.camera_height.sigma > 0.0


@pytest.mark.parametrize(("pitch_deg", "highest"), [(20, 1.10), (25, 1.30), (30, 1.60)])
def test_beyond_the_side_walls_the_eave_is_unknown(pitch_deg: float, highest: float) -> None:
    """KAN-61 (Must have 1) : vers +20°, les jambettes sortent du champ. Le faîtage tient ;
    l'égout est inconnu (jamais 0), avec une borne « au plus » tirée du bas de pente vu : exacte
    tant que ce bas est dans le champ (1,01 m pour 1,00), une majoration au-delà."""
    est, warnings = _estimate(pitch_deg, CAMERA_HEIGHT)
    assert any(FALLBACK in w for w in warnings)
    assert est.ridge_height is not None
    assert est.ridge_height.value == pytest.approx(RIDGE, rel=0.10)
    assert est.floor_assumed
    assert est.eave_heights == [], "égout inconnu, pas 0"
    assert est.width is None, "pas de largeur entre des égouts non vus"
    assert est.eave_bound is not None and est.eave_bound.source == "inferred"
    assert EAVE - 0.02 <= est.eave_bound.value <= highest
    assert any("Hauteur d'égout non visible sur la photo : au plus" in w for w in warnings)


@pytest.mark.parametrize("pitch_deg", [-15, 0, 8, 10, 15])
def test_seen_eaves_carry_no_bound(pitch_deg: float) -> None:
    """KAN-61 (Must have 4) : jambettes vues (sol vu ou supposé), rien ne change."""
    est, warnings = _estimate(pitch_deg, CAMERA_HEIGHT)
    assert est.eave_bound is None
    assert len(est.eave_heights) == 2
    assert not any("Hauteur d'égout non visible" in w for w in warnings)
