"""KAN-25 : lit encastré lisible comme tel — niche qui suit la pente, tête sous le rampant, pied
vers la partie haute, tiroirs en façade ; aucune collision avec la charpente ni le caisson."""

from __future__ import annotations

import numpy as np
import pytest

from openplan.geom.built_in_bed import (
    BUILT_IN_BED,
    MATTRESS_TOP_M,
    MIN_HEADROOM_M,
    NICHE_GAP_M,
    NICHE_TOP_M,
    STANDING_M,
    standing_at,
)
from openplan.geom.convex import hull_halfspaces, penetration
from openplan.geom.furniture_symbols import symbol_for
from openplan.geom.roof import derived_roofs
from openplan.geom.velux import (
    roof_panels,
    velux_casings,
)
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.layouts import LayoutBody, apply_layout, generate_layouts
from openplan.pipeline.scene3d import (
    built_in_niches,
    niche_planes,
    timber_solids,
)
from openplan.schemas.elements import Furniture
from openplan.schemas.scene import Project
from openplan.validation.layout import check_layout
from tests.factories import only_level
from tests.unit.test_timber_reveal import _attic, _with_windows
from tests.unit.test_velux_chevetre import BUILT_IN

PITCHES = (30, 45, 60)


def _furnished(pitch: float, windows: bool) -> list[Project]:
    project = _attic(pitch)
    if windows:
        project = _with_windows(project)
    lid = only_level(project).id
    run = generate_layouts(
        project, lid, LayoutBody(brief=BUILT_IN, variants=3, seed=4, budget_s=30)
    )
    assert run.proposals, run.result.warnings
    return [apply_layout(project, lid, p) for p in run.proposals]


def _beds(project: Project) -> list[Furniture]:
    level = only_level(project)
    return [
        e
        for e in level.elements.values()
        if isinstance(e, Furniture) and e.category == BUILT_IN_BED
    ]


@pytest.mark.parametrize("pitch", PITCHES)
def test_niche_has_two_sides_a_back_following_the_slope_and_a_shelf(pitch: float) -> None:
    for project in _furnished(pitch, windows=False):
        level = only_level(project)
        graph = WallGraph.from_level(level)
        planes = niche_planes(roof_panels(level, graph))
        for bed in _beds(project):
            parts = built_in_niches(level, graph)[bed.id]
            names = [n for n, _ in parts]
            assert "back" in names and "shelf" in names
            t = bed.transform
            c, s = np.cos(t.yaw), np.sin(t.yaw)
            # Deux joues, une de chaque côté du lit.
            sides = [p for n, p in parts if n == "side"]
            u = [
                float(np.mean((p[:, 0] - t.position.x) * c + (p[:, 1] - t.position.y) * s))
                for p in sides
            ]
            assert min(u) < -0.6 * bed.bbox.width / 2 and max(u) > 0.6 * bed.bbox.width / 2
            # Le dessus suit la pente : chaque sommet d'une joue est au sol, sur l'un des plans
            # de la niche (sous les chevrons, jeu compris) ou à son plafond (étagère, panne) ; le
            # fond, à la tête, reste sous la pente.
            on_plane = 0
            for p in sides:
                for point in p:
                    g = min(abs(float(point @ row[:3] + row[3])) for row in planes)
                    on_plane += g < 1e-6 and point[2] > t.position.z + 1e-6
                    floor = abs(point[2] - t.position.z) < 1e-6
                    cap = abs(point[2] - p[:, 2].max()) < 1e-6
                    assert g < 1e-6 or floor or cap
            assert on_plane >= 4, "les joues suivent la pente à la tête"
            back = next(p for n, p in parts if n == "back")
            assert back[:, 2].max() < t.position.z + NICHE_TOP_M - 0.05
            # L'étagère laisse 0,90 m libres au-dessus du matelas.
            shelf = next(p for n, p in parts if n == "shelf")
            assert shelf[:, 2].min() >= MATTRESS_TOP_M + MIN_HEADROOM_M - 0.03


@pytest.mark.parametrize("pitch", PITCHES)
def test_head_low_and_room_to_stand_in_front_of_the_foot(pitch: float) -> None:
    for project in _furnished(pitch, windows=True):
        level = only_level(project)
        roofs = list(derived_roofs(level, WallGraph.from_level(level)).values())
        assert not [v for v in check_layout(project) if v.type == "bed_access"]
        for bed in _beds(project):
            t = bed.transform
            assert (
                standing_at(roofs, level.height, t.position.x, t.position.y, t.yaw, bed.bbox.depth)
                >= STANDING_M - 1e-6
            )


def test_a_bed_turned_toward_the_low_wall_gets_a_soft_remark() -> None:
    project = _furnished(45, windows=False)[0]
    level = only_level(project)
    bed = _beds(project)[0]
    turned = bed.model_copy(deep=True)
    turned.transform.yaw += np.pi  # pied vers la jambette
    level.elements[bed.id] = turned
    remarks = [v for v in check_layout(project) if v.type == "bed_access"]
    assert remarks and remarks[0].severity == "medium"
    assert "1,80 m" in remarks[0].message and "pied" in remarks[0].message


@pytest.mark.parametrize("pitch", PITCHES)
def test_niche_never_touches_timber_nor_velux_casing(pitch: float) -> None:
    for project in _furnished(pitch, windows=True):
        level = only_level(project)
        graph = WallGraph.from_level(level)
        solids = timber_solids(level, graph)
        solids += [board for _, boards in velux_casings(level, graph) for board in boards]
        niches = built_in_niches(level, graph)
        assert niches
        for parts in niches.values():
            for _, part in parts:
                for other in solids:
                    a, b = hull_halfspaces(part), hull_halfspaces(other)
                    assert a is not None and b is not None
                    normals = np.vstack([a[:, :3], b[:, :3]])
                    assert penetration(part, other, normals) <= -NICHE_GAP_M + 1e-6


def test_symbol_shows_the_niche_and_drawers_inside_the_box() -> None:
    symbol = symbol_for(BUILT_IN_BED)
    assert symbol.outline == "rect"
    points = [p for path in symbol.paths for p in path.points]
    assert all(-0.5 <= u <= 0.5 and -0.5 <= v <= 0.5 for u, v in points)
    # Les deux joues : un trait fermé le long de chaque grand côté, du fond au pied.
    closed = [path for path in symbol.paths if path.closed]
    long_sides = [
        path
        for path in closed
        if max(v for _, v in path.points) == 0.5 and min(v for _, v in path.points) < -0.4
    ]
    assert len(long_sides) == 2
