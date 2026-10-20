"""KAN-63 : sous une charpente apparente, le caisson d'un velux descend jusqu'au bas des pannes,
comme un chevêtre. Le bout d'une panne coupée au droit de la joue, plus bas que l'ancien caisson
(22 cm), masquait un coin de la vitre en vue rasante. Vérifié sans GPU : rayons depuis des
caméras de la pièce jusqu'à la vitre (Möller–Trumbore du cadrage), profondeur du caisson, et
aucune collision du caisson avec les meubles d'un aménagement généré sous les velux."""

from __future__ import annotations

import math

import numpy as np
import pytest
import trimesh

from openplan.geom.framing import _hit_triangles
from openplan.geom.roof import derived_roofs
from openplan.geom.velux import (
    CHEVETRE_DEPTH_M,
    FURNITURE_GAP_M,
    REVEAL_DEPTH_M,
    ROOF_THICKNESS,
    ROOF_WINDOW_FRAME_M,
    SILL_FLOOR_GAP_M,
    reveal_solids,
    roof_panels,
    window_holes,
)
from openplan.geom.wallgraph import WallGraph
from openplan.pipeline.layouts import LayoutBody, apply_layout, generate_layouts
from openplan.pipeline.scene3d import (
    bake_level,
)
from openplan.scene.ops import AddFurniture, AddRoofWindow, UpdateElement, apply_op
from openplan.schemas import Furniture, RoofPlane
from openplan.schemas.common import Point2
from openplan.schemas.layout import BriefItem, LayoutBrief
from openplan.schemas.scene import Project
from openplan.validation.layout import check_layout, clearance_at
from tests.control_attic import control_attic
from tests.factories import only_level
from tests.unit.test_timber_reveal import _attic, _with_windows, overlap

MAX_OBLIQUE_DEG = 60.0
HEADROOM_M = CHEVETRE_DEPTH_M + 0.05
"""Une caméra se tient sous la charpente (qui pend jusqu'à 38 cm sous la pente) : plus haut,
elle serait dans les chevrons et verrait le bois autour d'elle, pas devant la vitre (le
cadrage des vues refuse déjà ces positions, `Shares.near`)."""
IN_TIMBER_M = 0.30
"""La charpente pend perpendiculairement à la pente (54 cm à la verticale à 45°) : une caméra
dont un rayon touche du bois à moins de 30 cm est logée dans la charpente, écartée."""


def _scenes() -> list[tuple[str, Project]]:
    return [("C2", control_attic())] + [(f"{p}°", _with_windows(_attic(p))) for p in (30, 45, 60)]


RIDGE_PART = 1999
"""Numéro de la demi-faîtière dans le bake (chevrons et pannes : 2000 et plus)."""
RIDGE_BASELINE = {"C2": 0, "30°": 226, "45°": 0, "60°": 0}
"""Rayons que la faîtière arrête au 20 oct. 2026 (limite physique, décision du PO) : un velux
près du faîtage, vu de l'autre côté de la pièce, a le haut de sa vitre caché par la faîtière qui
pend 41 cm sous le faîtage. Consigné : ce nombre ne doit pas monter."""


def _triangles(project: Project, kind: str, ridge: bool | None = None) -> np.ndarray:
    """Triangles des polyèdres `kind` du bake ; pour la charpente, `ridge` choisit la faîtière
    (True), chevrons et pannes (False) ou tout (None)."""
    level = only_level(project)
    tris = [
        trimesh.convex.convex_hull(np.array([[v.x, v.y, v.z] for v in p.vertices])).triangles
        for p in bake_level(project, level.id, style="realistic").polyhedra
        if p.kind == kind and (ridge is None or (p.part == RIDGE_PART) == ridge)
    ]
    return np.concatenate(tris) if tris else np.zeros((0, 3, 3))


def _panes(project: Project) -> list[tuple[np.ndarray, np.ndarray]]:
    """Par velux : grille 5 × 5 de points de la vitre, et la normale extérieure de la pente."""
    level = only_level(project)
    out = []
    for panel in roof_panels(level, WallGraph(level)):
        base = panel.vertices[:4]
        u = (base[1] - base[0]) / np.linalg.norm(base[1] - base[0])
        v = (base[3] - base[0]) / np.linalg.norm(base[3] - base[0])
        f = ROOF_WINDOW_FRAME_M + 0.01
        for _w, (u0, u1, v0, v1) in window_holes(level, panel):
            points = np.array(
                [
                    base[0] + u * a + v * b + panel.normal * 0.5 * ROOF_THICKNESS
                    for a in np.linspace(u0 + f, u1 - f, 5)
                    for b in np.linspace(v0 + f, v1 - f, 5)
                ]
            )
            out.append((points, np.asarray(panel.normal)))
    return out


def _cameras(project: Project, center: np.ndarray, normal: np.ndarray) -> list[np.ndarray]:
    """Caméras de la pièce (grille de 40 cm, à 1,2 / 1,4 / 1,6 m), sous la pente, qui voient
    le centre de la vitre sous un angle jusqu'à MAX_OBLIQUE_DEG de sa normale."""
    level = only_level(project)
    roofs = list(derived_roofs(level, WallGraph(level)).values())
    cams = []
    for x in np.arange(0.3, 6.0, 0.4):
        for y in np.arange(0.3, 5.0, 0.4):
            free = clearance_at((float(x), float(y)), roofs, level)
            for z in (1.2, 1.4, 1.6):
                if z > free - HEADROOM_M:
                    continue
                cam = np.array([x, y, z])
                to_pane = center - cam
                cos = float(to_pane @ normal) / float(np.linalg.norm(to_pane))
                if cos >= math.cos(math.radians(MAX_OBLIQUE_DEG)):
                    cams.append(cam)
    return cams


def _hidden_rays(project: Project) -> tuple[int, int, int]:
    """(rayons que seuls chevrons et pannes empêchent d'atteindre la vitre, rayons que seule
    la faîtière en empêche, rayons lancés). Un rayon qui touche une planche du caisson ne
    verrait pas la vitre même sans charpente (l'ébrasement la cache sous cet angle) : il ne
    compte pas."""
    timber = _triangles(project, "timber")
    beams = _triangles(project, "timber", ridge=False)
    ridge = _triangles(project, "timber", ridge=True)
    casing = _triangles(project, "reveal")
    hidden = ridged = total = 0
    for points, normal in _panes(project):
        kept = 0
        for cam in _cameras(project, points.mean(axis=0), normal):
            d = points - cam
            dist = np.linalg.norm(d, axis=1)
            unit = d / dist[:, None]
            if float(np.min(_hit_triangles(timber, cam, unit))) < IN_TIMBER_M:
                continue  # caméra logée dans la charpente (sous l'autre versant)
            clear = _hit_triangles(casing, cam, unit) >= dist - 1e-3
            hidden += int(np.sum(clear & (_hit_triangles(beams, cam, unit) < dist - 1e-3)))
            ridged += int(np.sum(clear & (_hit_triangles(ridge, cam, unit) < dist - 1e-3)))
            total += len(points)
            kept += 1
        assert kept >= 20, "assez de points de vue obliques"
    return hidden, ridged, total


@pytest.mark.parametrize("index", range(4))
def test_no_rafter_or_purlin_hides_the_glass_from_the_room(index: int) -> None:
    """Must have 1 : depuis la pièce, jusqu'à 60° d'obliquité, aucun rayon vers la vitre n'est
    arrêté par un chevron ou une panne (scène C2, pentes de 30°, 45° et 60°). La faîtière est
    une limite physique, consignée à part (RIDGE_BASELINE) pour qu'une régression se voie."""
    name, project = _scenes()[index]
    hidden, ridged, total = _hidden_rays(project)
    assert total > 1000
    assert hidden == 0, f"{name} : {hidden} rayons sur {total} arrêtés par chevrons ou pannes"
    print(f"KAN-63 {name} : faîtière {ridged} rayons sur {total}")
    assert ridged <= RIDGE_BASELINE[name], f"{name} : faîtière {ridged} > {RIDGE_BASELINE[name]}"


def _depth_below_roof(panel, solid: np.ndarray) -> float:  # type: ignore[no-untyped-def]
    base = panel.vertices[:4]
    return float(np.max((base[0] - solid) @ panel.normal))


def test_the_casing_goes_down_to_the_purlins_only_under_visible_timber() -> None:
    """Must have 2 : caisson jusqu'au bas des pannes (0,40 m) sous une charpente apparente,
    l'ébrasement de H2 (0,22 m) sans charpente."""
    project = _with_windows(_attic(45))
    level = only_level(project)
    panel = roof_panels(level, WallGraph(level))[0]
    _w, rect = window_holes(level, panel)[0]
    _l, _s, c0, c1 = reveal_solids(panel, rect)
    assert _depth_below_roof(panel, np.array([*c0, *c1])) == pytest.approx(
        CHEVETRE_DEPTH_M, abs=1e-6
    )
    for roof in [e for e in level.elements.values() if isinstance(e, RoofPlane)]:
        project = apply_op(
            project,
            level.id,
            UpdateElement(element_id=roof.id, fields={"metadata": {"timber": {"enabled": False}}}),
        )
    level = only_level(project)
    panel = roof_panels(level, WallGraph(level))[0]
    _w, rect = window_holes(level, panel)[0]
    _l, _s, c0, c1 = reveal_solids(panel, rect)
    assert _depth_below_roof(panel, np.array([*c0, *c1])) == pytest.approx(REVEAL_DEPTH_M, abs=1e-6)


def test_a_deep_sill_stops_above_the_floor() -> None:
    """Must have 2 : à 60°, un velux posé au ras de l'égout bas : l'appui vertical (0,80 m sous
    une charpente apparente) s'arrête à 10 cm au-dessus du plancher."""
    project = _attic(60)
    level = only_level(project)
    roof = next(e for e in level.elements.values() if isinstance(e, RoofPlane))
    project = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=2.0, distance=0.05))
    level = only_level(project)
    for panel in roof_panels(level, WallGraph(level)):
        for _w, rect in window_holes(level, panel):
            _l, sill, _c0, _c1 = reveal_solids(panel, rect)
            floor_z = float(panel.vertices[0][2]) - panel.roof.eave_height
            assert float(np.min(sill[:, 2])) >= floor_z + SILL_FLOOR_GAP_M - 1e-6


DESK_UNDER_VELUX = LayoutBrief(
    items=[
        BriefItem(category="desk", near=["window"]),
        BriefItem(category="bed_single"),
        BriefItem(category="low_storage", near=["low_wall"]),
        BriefItem(category="armchair", near=["window"], priority="nice_to_have"),
    ]
)
BUILT_IN = LayoutBrief(
    items=[
        BriefItem(category="bed_builtin", near=["low_wall"]),
        BriefItem(category="desk", near=["window"]),
    ]
)


@pytest.mark.parametrize("pitch_deg", [45, 60])
@pytest.mark.parametrize("brief", [DESK_UNDER_VELUX, BUILT_IN], ids=["bureau", "lit_encastre"])
def test_the_deeper_casing_hits_no_furniture(pitch_deg: float, brief: LayoutBrief) -> None:
    """Must have 4 : aménagements générés sous les velux (bureau et fauteuil près de la
    fenêtre, lit encastré contre la jambette) : aucune planche du caisson n'entre dans un
    meuble, et aucune règle dure d'aménagement n'est violée."""
    project = _with_windows(_attic(pitch_deg))
    level_id = only_level(project).id
    run = generate_layouts(project, level_id, LayoutBody(brief=brief, variants=3, seed=4))
    assert run.proposals, run.result.warnings
    for proposal in run.proposals:
        trial = apply_layout(project, level_id, proposal)
        assert not [v for v in check_layout(trial) if v.severity == "high"]
        bake = bake_level(trial, level_id, style="realistic")
        boards = [
            np.array([[v.x, v.y, v.z] for v in p.vertices])
            for p in bake.polyhedra
            if p.kind == "reveal"
        ]
        assert boards
        furniture = [
            np.array([[c.x, c.y, z] for c in p.exterior for z in (p.z0, p.z1)])
            for p in bake.prisms
            if p.kind == "furniture"
        ]
        assert furniture
        clashes = [(round(overlap(b, f), 3)) for b in boards for f in furniture]
        assert max(clashes) <= 0.005, max(clashes)


def _sill_bottom(project: Project) -> list[float]:
    level = only_level(project)
    bake = bake_level(project, level.id, style="realistic")
    return [
        min(v.z for v in p.vertices) for p in bake.polyhedra if p.kind == "reveal" and p.part == 11
    ]


def test_a_tall_chest_under_a_velux_stops_the_casing_above_it() -> None:
    """Must have 4 : une commode posée à la main contre l'égout, sous le velux bas (60°) :
    l'appui s'arrête 2 cm au-dessus d'elle, sans remonter plus haut que l'ébrasement de H2 ;
    sans meuble dessous, il garde toute sa profondeur (40 cm sous la toiture)."""
    project = _attic(60)
    level = only_level(project)
    roof = min(
        (e for e in level.elements.values() if isinstance(e, RoofPlane)),
        key=lambda r: min(r.eave_start.y, r.eave_end.y),
    )
    project = apply_op(project, level.id, AddRoofWindow(roof=roof.id, offset=1.8, distance=0.6))
    free = _sill_bottom(project)
    assert len(free) == 1
    window_low_z = 1.0 + 0.6 * math.sin(math.radians(60))
    assert free[0] == pytest.approx(
        window_low_z - CHEVETRE_DEPTH_M / math.cos(math.radians(60)), abs=0.03
    )
    chest = apply_op(
        project,
        level.id,
        AddFurniture(asset_id="chest_of_drawers", position=Point2(x=2.2, y=0.35), yaw=0.0),
    )
    piece = next(e for e in only_level(chest).elements.values() if isinstance(e, Furniture))
    bake = bake_level(chest, level.id, style="realistic")
    top = max(p.z1 for p in bake.prisms if p.element_id == piece.id)
    stopped = _sill_bottom(chest)[0]
    assert stopped == pytest.approx(
        # 2 cm au-dessus de la commode, pas plus bas que toute la profondeur, et jamais plus
        # haut que l'ébrasement de H2 (22 cm sous la toiture).
        min(
            max(
                top + FURNITURE_GAP_M,
                window_low_z - CHEVETRE_DEPTH_M / math.cos(math.radians(60)),
            ),
            window_low_z - REVEAL_DEPTH_M / math.cos(math.radians(60)),
        ),
        abs=1e-6,
    )
    boards = [
        np.array([[v.x, v.y, v.z] for v in p.vertices])
        for p in bake.polyhedra
        if p.kind == "reveal"
    ]
    boxes = [
        np.array([[c.x, c.y, z] for c in p.exterior for z in (p.z0, p.z1)])
        for p in bake.prisms
        if p.element_id == piece.id
    ]
    assert max(overlap(b, f) for b in boards for f in boxes) <= 0.005
