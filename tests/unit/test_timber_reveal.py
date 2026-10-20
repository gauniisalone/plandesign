"""KAN-28 : la charpente ne passe plus devant les velux. Sous la toiture, le caisson d'un velux
(H2) s'évase vers la pièce (linteau horizontal, appui vertical, jusqu'à REVEAL_DEPTH_M) ; aucune
pièce de charpente (chevron, panne, faîtière) ne doit traverser ce jour. Vérifié sur la
géométrie du rendu (bake), sans GPU, par intersection de solides convexes."""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.optimize import linprog
from scipy.spatial import ConvexHull

from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.attic import Estimate
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.scene3d import (
    PURLIN,
    PURLIN_EVERY_M,
    RAFTER,
    TIMBER_CLEARANCE_M,
    bake_level,
    reveal_solids,
    reveal_span,
    roof_panels,
    timber_rects,
    timber_settings,
    timber_solids,
    window_holes,
)
from openplan.scene.factory import new_project
from openplan.scene.ops import AddRoofWindow, apply_op
from openplan.schemas import RoofPlane, Source
from openplan.schemas.scene import Project
from tests.control_attic import control_attic
from tests.factories import only_level

TOLERANCE_M = 0.005
"""Rayon de la plus grande boule tenant dans l'intersection : au-delà, la pièce traverse."""


def overlap(a: np.ndarray, b: np.ndarray) -> float:
    """Rayon de la plus grande boule dans l'intersection de deux convexes (≤ 0 si disjoints)."""
    ha, hb = ConvexHull(a), ConvexHull(b)
    eq = np.vstack([ha.equations, hb.equations])  # n·x + c ≤ 0, n unitaire
    a_ub = np.hstack([eq[:, :3], np.linalg.norm(eq[:, :3], axis=1)[:, None]])
    res = linprog(
        [0.0, 0.0, 0.0, -1.0],
        A_ub=a_ub,
        b_ub=-eq[:, 3],
        bounds=[(None, None)] * 3 + [(None, 1.0)],
    )
    return float(res.x[3]) if res.success else -1.0


def _attic(pitch_deg: float) -> Project:
    """Combles 6 × 5 m, égout 1 m, pente donnée."""

    def est(v: float) -> Estimate:
        return Estimate(value=v, sigma=0.0, source=Source.USER_DEFINED)

    ridge = 1.0 + 2.5 * math.tan(math.radians(pitch_deg))
    project = new_project(f"Combles {pitch_deg}°")
    level = only_level(project)
    geometry = AtticGeometry(
        length=est(6.0), width=est(5.0), ridge_height=est(ridge), eave_height=est(1.0)
    )
    after, _ = apply_attic(project, level.id, geometry)
    return after


def _with_windows(project: Project) -> Project:
    """Sur chaque pente : un velux à cheval sur un chevron (celui de u = 2,1 m), et un velux
    posé juste au-dessus d'une panne, dans la zone où le caisson s'évase."""
    level = only_level(project)
    for roof in [e for e in level.elements.values() if isinstance(e, RoofPlane)]:
        slope = 2.5 / math.cos(roof.pitch)
        project = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=1.8, distance=0.6))
        purlins = max(1, round(slope / PURLIN_EVERY_M))
        v = slope * 1 / (purlins + 1)
        project = apply_op(
            project,
            level.id,
            AddRoofWindow(roof=roof.id, offset=3.6, distance=v + PURLIN[0] / 2 + 0.05),
        )
    return project


def _crossings(project: Project) -> list[tuple[str, float]]:
    """(pente, profondeur) de chaque pièce de charpente du rendu qui traverse un jour."""
    level = only_level(project)
    graph = WallGraph(level)
    timber = [
        np.array([[v.x, v.y, v.z] for v in p.vertices])
        for p in bake_level(project, level.id, style="realistic").polyhedra
        if p.kind == "timber"
    ]
    out = []
    for panel in roof_panels(level, graph):
        for _w, rect in window_holes(level, panel):
            lintel, sill, _c0, _c1 = reveal_solids(panel, rect)
            shaft = np.array([*lintel[:4], *sill[:4]])
            out += [(panel.roof.name, d) for t in timber if (d := overlap(t, shaft)) > TOLERANCE_M]
    return out


def test_control_attic_timber_stays_out_of_the_velux() -> None:
    """Must have 1 : scène de contrôle C2 (pente 45,5°, un velux par versant). Avant le
    correctif, le chevron au droit de chaque velux traversait le jour à ses deux bouts."""
    project = control_attic()
    assert (
        len(
            window_holes(
                only_level(project),
                roof_panels(only_level(project), WallGraph(only_level(project)))[0],
            )
        )
        == 1
    )
    assert _crossings(project) == []


@pytest.mark.parametrize("pitch_deg", [30, 45, 60])
def test_no_timber_crosses_a_velux_opening(pitch_deg: float) -> None:
    """Must have 1 : pentes de 30°, 45° et 60°, velux à cheval sur un chevron et au-dessus
    d'une panne : aucune pièce (chevron, panne, faîtière) ne traverse le jour."""
    project = _with_windows(_attic(pitch_deg))
    level = only_level(project)
    holes = sum(len(window_holes(level, p)) for p in roof_panels(level, WallGraph(level)))
    assert holes == 4, "deux velux par versant"
    assert _crossings(project) == []


@pytest.mark.parametrize("pitch_deg", [30, 45, 60])
def test_timber_is_cut_no_more_than_the_opening(pitch_deg: float) -> None:
    """Must have 2 : un chevron au droit d'un velux n'est coupé que sur l'étendue du jour à sa
    profondeur (plus le jeu), et rien ne change sur une pente sans velux."""
    pitch = math.radians(pitch_deg)
    length, slope = 6.0, 2.5 / math.cos(pitch)
    hole = (1.8, 2.58, 0.6, 1.58)
    rafters = [
        r for r, t0, _ in timber_rects(length, slope, 0.6, [hole], pitch) if t0 == -RAFTER[1]
    ]
    pieces = sorted((r[2], r[3]) for r in rafters if abs((r[0] + r[1]) / 2 - 2.1) < 1e-6)
    assert len(pieces) == 2, "le chevron de u = 2,1 m est coupé en deux"
    a, b = reveal_span(hole, RAFTER[1], pitch)
    assert pieces[0][1] == pytest.approx(a - TIMBER_CLEARANCE_M)
    assert pieces[1][0] == pytest.approx(b + TIMBER_CLEARANCE_M)
    assert all(v1 - v0 > 0.05 for r in rafters for v0, v1 in [(r[2], r[3])])
    assert timber_rects(length, slope, 0.6, [], pitch) == timber_rects(length, slope, 0.6, [])


def test_render_and_view_measure_share_the_same_timber() -> None:
    """Must have 3 : la mesure du cadrage des vues (`timber_solids`) compte exactement la
    charpente que le rendu bâtit."""
    project = _with_windows(_attic(45))
    level = only_level(project)
    measured = timber_solids(level, WallGraph(level))
    baked = [
        np.array([[v.x, v.y, v.z] for v in p.vertices])
        for p in bake_level(project, level.id, style="realistic").polyhedra
        if p.kind == "timber"
    ]
    assert len(measured) == len(baked)

    def key(points: np.ndarray) -> tuple[float, ...]:
        return tuple(np.round(np.sort(points, axis=0), 4).ravel())

    assert sorted(map(key, measured)) == sorted(
        map(key, (b[ConvexHull(b).vertices] for b in baked))
    )
    assert timber_settings(next(e for e in level.elements.values() if isinstance(e, RoofPlane)))[0]
