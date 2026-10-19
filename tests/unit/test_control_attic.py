"""KAN-27 : la scène de contrôle des combles tient sa vérité de référence (sans GPU), et ce que
l'estimation des combles en tire aujourd'hui sur sa profondeur apprise (fixture réduite)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openplan.pipeline.attic import AtticEstimate, analyse_attic, rescale
from openplan.schemas import RoofPlane
from openplan.schemas.elements import Camera, RoofWindow
from openplan.validation.invariants import validate_invariants
from tests.control_attic import (
    CAMERA_HEIGHT,
    EAVE,
    GABLE_DISTANCE,
    LENGTH,
    RIDGE,
    VELUX_ALONG,
    VELUX_CENTER_Z,
    VELUX_DISTANCE,
    WIDTH,
    control_attic,
)
from tests.factories import only_level


def test_the_control_attic_holds_its_reference_truth() -> None:
    project = control_attic()
    level = only_level(project)
    assert validate_invariants(project).valid
    assert pytest.approx(3.543, abs=0.001) == RIDGE
    roofs = {e.id: e for e in level.elements.values() if isinstance(e, RoofPlane)}
    assert len(roofs) == 2
    for roof in roofs.values():
        assert roof.eave_height == EAVE
        assert EAVE + (WIDTH / 2) * math.tan(roof.pitch) == pytest.approx(RIDGE)
    windows = [e for e in level.elements.values() if isinstance(e, RoofWindow)]
    assert len(windows) == 2, "un velux par versant"
    for w in windows:
        roof = roofs[w.roof]
        assert w.distance == VELUX_DISTANCE
        center = EAVE + (w.distance + w.length / 2) * math.sin(roof.pitch)
        assert center == pytest.approx(VELUX_CENTER_Z, abs=1e-6)
        # Centre à 75 % de la longueur, compté depuis le pignon x = 0.
        x = w.offset + w.width / 2 if roof.eave_start.x == 0.0 else LENGTH - w.offset - w.width / 2
        assert x == pytest.approx(VELUX_ALONG * LENGTH)
    camera = next(e for e in level.elements.values() if isinstance(e, Camera))
    assert camera.position.z == CAMERA_HEIGHT
    assert camera.position.x == pytest.approx(LENGTH - GABLE_DISTANCE)
    assert camera.position.y == pytest.approx(WIDTH / 2)


# Profondeur apprise (Depth Anything V2 metric indoor small) du rendu Blender de la scène depuis
# la caméra « Photo », réduite de 1280 × 960 à 320 × 240 (moyenne 4 × 4, float16) ; focale du
# rendu 1024 px → 256 px. Calculée une fois sur créneau GPU (19 oct. 2026, 17:44).
DEPTH = Path(__file__).parents[1] / "fixtures" / "control_attic_depth.npz"


def _estimate() -> tuple[AtticEstimate, AtticEstimate, list[str]]:
    data = np.load(DEPTH)
    depth = data["depth"].astype(np.float32)
    focal = float(data["focal_px"])
    est, _planes, cls, _pixels, _warnings = analyse_attic(
        depth, focal, (depth.shape[1] / 2.0, depth.shape[0] / 2.0)
    )
    assert cls is not None
    kinds: list[str] = sorted(cls.kinds.values())
    # Recalé sur la hauteur de prise de vue, comme dans l'app.
    return est, rescale(est, CAMERA_HEIGHT), kinds


def test_control_depth_documents_todays_estimate() -> None:
    """État au 19 oct. 2026 (KAN-27, clos avec écart) : les jambettes (≈ 1 % de l'image chacune)
    ne sont pas extraites comme plans, donc l'égout tombe au sol ; la profondeur apprise voit la
    caméra trop basse (≈ 0,85 m pour 1,45) et le faîtage, une fois recalé, est faux. Si ce test
    change, l'estimation a changé : revoir KAN-27 et son xfail ci-dessous."""
    raw, scaled, kinds = _estimate()
    assert "side_wall" not in kinds
    assert [e.value for e in scaled.eave_heights] == [0.0, 0.0]
    assert raw.camera_height is not None
    assert raw.camera_height.value == pytest.approx(0.85, abs=0.05)
    assert scaled.ridge_height is not None and scaled.ridge_height.value > RIDGE + 1.0


@pytest.mark.xfail(
    strict=True,
    reason="KAN-27, écart sur le Must have 1 : jambettes non extraites et profondeur apprise "
    "déformée (caméra × 0,59, pentes à 49°) ; voir KAN-46 et le chantier KAN-33",
)
def test_control_depth_eave_within_the_must_have() -> None:
    """Must have 1 de KAN-27 : sur le contrôle, l'égout estimé entre 0,85 et 1,15 m (vérité
    1,00). Attendu en échec ; strict : le jour où il passe, le test le signale."""
    _raw, scaled, _kinds = _estimate()
    assert scaled.eave_heights
    assert all(0.85 <= e.value <= 1.15 for e in scaled.eave_heights)
