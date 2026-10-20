"""KAN-70 : un plateau (bureau à 0,75 m) plus soutenu que le sol n'est pas pris pour le sol.

Profondeur synthétique exacte, sans GPU : appareil à 1,50 m, incliné vers le bas, un plateau
de 2,0 × 1,2 m à 0,75 m qui occupe le milieu de l'image, le sol visible dessous et devant, un
mur au fond. Le sol peut être « encombré » (bosses d'objets) : il n'est alors plus extrait
comme plan et seule la nappe de points la plus basse le retrouve."""

from __future__ import annotations

import math

import numpy as np
import pytest

from openplan.geom.planes import backproject, fit_planes
from openplan.pipeline.attic import STRIDE, analyse_attic, find_floor
from openplan.pipeline.depthplan import observe_room

W, H, F = 320, 240, 260.0
CAMERA = 1.50
TABLE = 0.75


def _scene(*, clutter: float = 0.0, seed: int = 0, table: bool = True) -> np.ndarray:
    """Profondeur z (repère caméra : x à droite, y vers le bas, z devant)."""
    pitch = math.radians(40.0)  # vers le bas
    vs, us = np.mgrid[0:H, 0:W].astype(np.float64)
    x = (us - W / 2) / F
    y = (vs - H / 2) / F
    # Rayon caméra (x, y, 1) → monde (X à droite, Y devant, Z en haut).
    c, s = math.cos(pitch), math.sin(pitch)
    dx = x
    dy = c * 1.0 - s * y
    dz = -s * 1.0 - c * y
    t = np.full((H, W), np.inf)
    # Sol z = −CAMERA, bosses d'objets si `clutter`.
    rng = np.random.default_rng(seed)
    with np.errstate(divide="ignore", invalid="ignore"):
        t_floor = np.where(dz < 0, -CAMERA / dz, np.inf)
        if clutter:
            bumps = rng.uniform(0.0, clutter, (H // 8 + 1, W // 8 + 1)).repeat(8, 0).repeat(8, 1)
            t_floor = np.where(dz < 0, -(CAMERA - bumps[:H, :W]) / dz, np.inf)
        t = np.minimum(t, t_floor)
        # Plateau z = −(CAMERA − TABLE), 2,0 m de large, de 0,9 à 2,1 m devant.
        t_table = np.where(dz < 0, -(CAMERA - TABLE) / dz, np.inf)
        px, py = dx * t_table, dy * t_table
        on_table = table & (np.abs(px) <= 1.6) & (py >= 0.7) & (py <= 2.4)
        t = np.where(on_table & (t_table < t), t_table, t)
        # Mur du fond y = 2,6.
        t_wall = np.where(dy > 0, 2.6 / dy, np.inf)
        t = np.minimum(t, t_wall)
    return np.asarray(t * 1.0, dtype=np.float32)  # z caméra = t (composante z du rayon = 1)


@pytest.mark.parametrize("clutter", [0.0, 0.3])
def test_the_floor_is_below_the_table(clutter: float) -> None:
    """Sol net : extrait comme plan, moins soutenu que le plateau (règle du plan plus bas). Sol
    encombré de 0 à 30 cm : pas de plan de sol, la nappe de points la plus basse le donne."""
    depth = _scene(clutter=clutter)
    pp = (W / 2, H / 2)
    points, pixels = backproject(depth, F, pp, stride=STRIDE)
    planes = fit_planes(points, pixels, (W, H), seed=0)
    # Le piège : le plan le plus soutenu est le plateau, à 0,75 m sous l'appareil.
    assert max(planes, key=lambda p: p.count).plane.distance == pytest.approx(TABLE, abs=0.05)
    tolerance = 0.05 + clutter / 2
    obs, _ = observe_room(depth, F, pp)
    assert obs is not None
    assert obs.camera_height_m == pytest.approx(CAMERA, abs=tolerance)
    est, *_ = analyse_attic(depth, F, pp)
    assert est.camera_height is not None
    assert est.camera_height.value == pytest.approx(CAMERA, abs=tolerance)


def test_a_plain_floor_is_kept() -> None:
    """Sans plateau, rien ne change : le sol trouvé est le sol."""
    depth = _scene(table=False)
    pp = (W / 2, H / 2)
    points, pixels = backproject(depth, F, pp, stride=STRIDE)
    planes = fit_planes(points, pixels, (W, H), seed=0)
    idx = find_floor(planes)
    assert idx is not None and planes[idx].plane.distance == pytest.approx(CAMERA, abs=0.05)
    obs, _ = observe_room(depth, F, pp)
    assert obs is not None and obs.camera_height_m == pytest.approx(CAMERA, abs=0.05)
