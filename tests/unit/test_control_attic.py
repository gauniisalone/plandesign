"""KAN-27 : la scène de contrôle des combles tient sa vérité de référence (sans GPU), et ce que
l'estimation des combles en tire aujourd'hui sur sa profondeur apprise (fixture réduite)."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from openplan.pipeline.attic import AtticEstimate, analyse_attic, rescale
from openplan.pipeline.reference import segment_distance
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
    """KAN-27 puis KAN-46 : les jambettes (≈ 1 % de l'image chacune) ne sortent pas de la
    recherche de plans, elles sont déduites du bord du sol vu (deux murs latéraux, égouts non
    nuls) ; la profondeur apprise voit toujours la caméra trop basse (≈ 0,85 m pour 1,45) et le
    faîtage, recalé sur la hauteur saisie, est faux (KAN-72). Si ce test change, l'estimation a
    changé : revoir KAN-46 et KAN-72."""
    raw, scaled, kinds = _estimate()
    assert kinds.count("side_wall") == 2
    assert len(scaled.eave_heights) == 2 and all(e.value > 0.5 for e in scaled.eave_heights)
    assert raw.camera_height is not None
    assert raw.camera_height.value == pytest.approx(0.85, abs=0.05)
    assert scaled.ridge_height is not None and scaled.ridge_height.value > RIDGE + 1.0


def _with_reference(depth: np.ndarray | None = None) -> tuple[AtticEstimate, float]:
    """Estimation brute et échelle d'une référence exacte : les deux coins du pignon du fond
    au sol (5,00 m), comme une mesure posée par l'utilisateur (KAN-66)."""
    data = np.load(DEPTH)
    if depth is None:
        depth = data["depth"].astype(np.float32)
    focal = float(data["focal_px"])
    pp = (depth.shape[1] / 2.0, depth.shape[0] / 2.0)

    def pixel(y: float, z: float) -> tuple[float, float]:
        return (
            pp[0] + focal * (WIDTH / 2.0 - y) / GABLE_DISTANCE,
            pp[1] + focal * (CAMERA_HEIGHT - z) / GABLE_DISTANCE,
        )

    raw, *_ = analyse_attic(depth, focal, pp)
    scale = WIDTH / segment_distance(depth, focal, pp, pixel(0.0, 0.02), pixel(WIDTH, 0.02))
    return raw, scale


def _check_with_reference(raw: AtticEstimate, scale: float) -> None:
    assert raw.width is not None
    assert raw.width.value * scale == pytest.approx(WIDTH, rel=0.10)
    assert len(raw.eave_heights) == 2
    assert all(0.85 <= e.value * scale <= 1.30 for e in raw.eave_heights)


def test_control_with_reference_width_between_knee_walls() -> None:
    """KAN-46 (critère révisé par le PO le 20 oct. 2026) : avec une référence exacte, largeur
    entre jambettes à ±10 % (4,86 m pour 5,00), deux égouts trouvés, chacun entre 0,85 et 1,30 m
    (1,05 et 1,24 m pour 1,00 ; avant KAN-46 : 0 et 0, largeur 7,50 m prise au sol)."""
    _check_with_reference(*_with_reference())


def test_knee_walls_resist_aberrant_depth_points() -> None:
    """Le bord du sol est la médiane des points les plus latéraux : 30 pixels de profondeur
    aberrante (× 0,3 à × 3) ne déplacent pas les jambettes."""
    depth = np.load(DEPTH)["depth"].astype(np.float32)
    rng = np.random.default_rng(0)
    ys, xs = rng.integers(0, depth.shape[0], 30), rng.integers(0, depth.shape[1], 30)
    depth[ys, xs] *= rng.uniform(0.3, 3.0, 30).astype(np.float32)
    _check_with_reference(*_with_reference(depth))


@pytest.mark.xfail(
    strict=True,
    reason="KAN-72 : la profondeur apprise étire les hauteurs (caméra × 0,72 avec la référence, "
    "faîtage +16 %, un égout à +24 %) ; le jour où ce test passe, il le signale",
)
def test_control_eave_and_ridge_within_10_percent_with_reference() -> None:
    raw, scale = _with_reference()
    assert raw.ridge_height is not None
    assert raw.ridge_height.value * scale == pytest.approx(RIDGE, rel=0.10)
    assert all(e.value * scale == pytest.approx(EAVE, rel=0.10) for e in raw.eave_heights)
