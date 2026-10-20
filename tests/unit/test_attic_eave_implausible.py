"""KAN-73 : un égout à moins de 0,5 m sous le faîtage, ou une largeur entre égouts sous 1,5 m,
n'est pas retenu ; l'égout est alors inconnu, comme dans KAN-61. Seuils en mètres, après le
recalage sur la hauteur de prise de vue. Sans GPU : estimations construites."""

from __future__ import annotations

from uuid import uuid4

import pytest

from openplan.pipeline.attic import (
    EAVE_MIN_BELOW_RIDGE_M,
    EAVE_MIN_WIDTH_M,
    AtticEstimate,
    Estimate,
    plausible_eaves,
    rescale,
)
from openplan.pipeline.autoplan import attic_geometry, attic_notices
from openplan.schemas.common import Source


def _e(value: float, source: Source = Source.ESTIMATED) -> Estimate:
    return Estimate(value=value, sigma=0.1, source=source)


def _attic(
    *, camera: float, ridge: float, eave: float, width: float, mirrored: bool = True
) -> AtticEstimate:
    eaves = [_e(eave), _e(eave, Source.INFERRED if mirrored else Source.ESTIMATED)]
    return AtticEstimate(
        camera_height=_e(camera),
        ridge_height=_e(ridge),
        eave_heights=eaves,
        width=_e(width),
        usable_width=_e(max(width - 1.0, 0.5)),
        gable_distance=_e(4.0),
    )


def test_thresholds() -> None:
    assert EAVE_MIN_BELOW_RIDGE_M == 0.5 and EAVE_MIN_WIDTH_M == 1.5


def test_eave_just_under_the_ridge_is_unknown() -> None:
    """Le cas du vrai grenier : égout 2,10 m pour un faîtage à 2,32 m, largeur 0,59 m."""
    out = plausible_eaves(_attic(camera=1.5, ridge=2.32, eave=2.10, width=0.59))
    assert out.eave_heights == [] and out.width is None
    assert out.eave_rejected is not None
    assert "0,22 m sous le faîtage" in out.eave_rejected
    assert out.ridge_height is not None and out.ridge_height.value == pytest.approx(2.32)


def test_narrow_width_alone_is_unknown() -> None:
    out = plausible_eaves(_attic(camera=1.5, ridge=3.5, eave=1.0, width=1.2, mirrored=False))
    assert out.eave_heights == [] and out.width is None
    assert out.eave_rejected is not None and "1,20 m" in out.eave_rejected


def test_plausible_attic_is_kept() -> None:
    """La scène de contrôle (égout 1,00, faîtage 3,54, largeur 5,00) passe telle quelle."""
    before = _attic(camera=1.45, ridge=3.54, eave=1.0, width=5.0, mirrored=False)
    assert plausible_eaves(before) == before


def test_thresholds_apply_after_rescale() -> None:
    """À l'échelle du modèle (hauteur vue 4,72 m), l'égout est 0,67 m sous le faîtage et la
    largeur 1,87 m : rien n'est écarté. Recalé sur 1,50 m : 0,21 m et 0,59 m, écarté."""
    raw = _attic(camera=4.72, ridge=7.29, eave=6.62, width=1.87)
    assert plausible_eaves(raw) == raw
    scaled = rescale(raw, 1.5, entered=False)
    assert scaled.eave_heights == [] and scaled.width is None and scaled.eave_rejected


def test_geometry_and_notice_say_the_eave_is_unknown() -> None:
    est = plausible_eaves(_attic(camera=1.5, ridge=2.32, eave=2.10, width=0.59))
    geometry = attic_geometry(est, uuid4(), uuid4())
    assert geometry is not None
    assert geometry.eave_height.value == 0.0
    assert geometry.eave_height.source is Source.INFERRED
    assert geometry.eave_height.note is not None and "non retenu" in geometry.eave_height.note
    assert geometry.width.source is Source.INFERRED
    notices = attic_notices(est, geometry)
    assert len(notices) == 1 and notices[0].startswith("Hauteur d'égout inconnue")
