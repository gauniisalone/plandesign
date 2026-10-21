"""Scène 3D : niveau paramétrique → prismes et polyèdres (`Bake`) → maillage trimesh → GLB.

Pas de CSG : une ouverture découpe son mur en boîtes le long de l'axe (avant, sous,
au-dessus, après) avant extrusion. Les pentes (`RoofPlane`) deviennent des panneaux inclinés
et coupent les murs qui passent au-dessus d'elles (découpe par demi-espace, avec bouchage),
ce qui donne aux pignons leur profil. Chaque nœud du GLB porte l'UUID de son élément.
"""

from __future__ import annotations

import itertools
import json
import math
import struct
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np
import trimesh
from numpy.typing import NDArray
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.polygon import orient
from trimesh.creation import extrude_polygon
from trimesh.visual.color import ColorVisuals

from openplan.geom.built_in_bed import BUILT_IN_BED, NICHE_GAP_M, capping, niche
from openplan.geom.built_in_bed import parts as bed_parts
from openplan.geom.convex import SURE_TOUCH_M, Solid
from openplan.geom.polygon import largest_polygon, to_points
from openplan.geom.stairs import railing_lines, stair_layout
from openplan.geom.velux import (
    PURLIN,
    RAFTER,
    REVEAL_DEPTH_M,
    RIDGE_BEAM,
    ROOF_THICKNESS,
    ROOF_WINDOW_FRAME_M,
    Rect,
    RoofPanel,
    lintel_reach,
    reveal_depth,
    reveal_solids,
    roof_panels,
    timber_settings,
    velux_casings,
    window_holes,
)
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.decor import ambiance, dress_level, palette_of
from openplan.pipeline.joinery import joinery_prisms
from openplan.pipeline.style import CURRENT as CURRENT_STYLE
from openplan.pipeline.style import STYLE_RAILING, style_finishes, surface_of
from openplan.scene.catalog import asset_file, load_catalog
from openplan.schemas.bake import (
    Bake,
    BakedOpening,
    BakeStyle,
    Finish,
    Polyhedron,
    PolyhedronKind,
    Prism,
    PrismKind,
)
from openplan.schemas.common import ElementBase, Point2, Point3, Source
from openplan.schemas.elements import (
    DOOR_KINDS,
    Ceiling,
    Fixture,
    Floor,
    Furniture,
    Opening,
    Room,
    Stairs,
)
from openplan.schemas.results import Artifact
from openplan.schemas.scene import Level, Project
from openplan.validation.layout import footprint_corners, stair_zones

SLAB = 0.02
"""Épaisseur des dalles de sol et plafond (visuelle)."""
Float = NDArray[np.float64]

SOURCE_COLORS: dict[Source, tuple[int, int, int]] = {
    Source.MEASURED: (46, 125, 50),
    Source.USER_DEFINED: (31, 95, 191),
    Source.OBSERVED: (11, 114, 133),
    Source.ESTIMATED: (184, 134, 11),
    Source.INFERRED: (122, 122, 122),
    Source.GENERATED: (123, 63, 191),
}
SLAB_COLORS: dict[str, tuple[int, int, int]] = {
    "floor": (214, 200, 170),
    "ceiling": (235, 235, 230),
}
FINISH_COLORS: dict[Finish, tuple[int, int, int]] = {
    "plaster": (232, 228, 220),
    "wood_floor": (176, 132, 84),
    "wood_dark": (110, 78, 50),
    "wood_boards": (160, 112, 70),
    "stone": (150, 145, 135),
    "stone_rubble": (140, 132, 120),
    "concrete": (170, 170, 168),
    "glass": (196, 222, 240),
    "timber": (112, 82, 54),
    "metal": (40, 40, 42),
    "limewash": (236, 232, 224),
    "concrete_floor": (150, 150, 148),
    "oak_dark": (78, 56, 40),
    "painted_wood": (238, 236, 230),
    "linen": (226, 220, 208),
}
"""Teintes plates par matière : l'aperçu « matières » sans Blender (viewer GLB)."""
DEFAULT_FINISH: dict[str, Finish] = {
    "wall": "plaster",
    "floor": "wood_floor",
    "ceiling": "plaster",
    "roof": "wood_boards",
    "slab": "wood_floor",
    "furniture": "wood_boards",
    "fixture": "concrete",
    "roof_window": "glass",
    "timber": "timber",
    "stairs": "wood_dark",
    "reveal": "wood_boards",
}
FINISHES: frozenset[str] = frozenset(FINISH_COLORS)


def finish_of(element: ElementBase, kind: str) -> Finish:
    """Matière d'un élément (V1, voir `pipeline/style.py`) : choisie par l'utilisateur sur
    l'élément, sinon celle de l'ambiance pour sa surface, sinon celle qu'une étape automatique
    lui a donnée, sinon la matière par défaut du type."""
    wanted = element.metadata.get("finish")
    valid = isinstance(wanted, str) and wanted in FINISHES
    if valid and element.metadata.get("finish_source") == "user_defined":
        return cast(Finish, wanted)
    styled = CURRENT_STYLE.get()
    if styled is not None:
        surface = surface_of(kind, element.id, styled)
        if surface is not None and surface in styled.by_surface:
            return styled.by_surface[surface]
    if valid:
        return cast(Finish, wanted)
    return DEFAULT_FINISH[kind]


def _color(source: Source, finish: Finish, style: BakeStyle) -> tuple[int, int, int]:
    return FINISH_COLORS[finish] if style == "realistic" else SOURCE_COLORS[source]


@dataclass(frozen=True)
class WallSegment:
    s0: float
    s1: float
    z0: float
    z1: float


def wall_segments(length: float, height: float, openings: list[Opening]) -> list[WallSegment]:
    """Découpe [0, L] × [0, H] en rectangles pleins autour des ouvertures.

    Les ouvertures sont traitées par décalage croissant ; un chevauchement est absorbé
    par la première. Une ouverture hors du mur est ignorée (l'invariant la signale).
    """
    segments: list[WallSegment] = []
    cursor = 0.0
    for o in sorted(openings, key=lambda o: o.offset):
        s0 = max(cursor, o.offset)
        s1 = min(length, o.offset + o.width)
        if s1 <= s0:
            continue
        if s0 > cursor:
            segments.append(WallSegment(cursor, s0, 0.0, height))
        z0 = min(o.sill_height, height)
        z1 = min(height, o.sill_height + o.height)
        if z0 > 0.0:
            segments.append(WallSegment(s0, s1, 0.0, z0))
        if z1 < height:
            segments.append(WallSegment(s0, s1, z1, height))
        cursor = s1
    if cursor < length:
        segments.append(WallSegment(cursor, length, 0.0, height))
    return segments


def _polyhedron(
    mesh: trimesh.Trimesh,
    element: ElementBase,
    part: int,
    kind: PolyhedronKind,
    style: BakeStyle,
) -> Polyhedron:
    finish = finish_of(element, kind)
    return Polyhedron(
        element_id=element.id,
        part=part,
        kind=kind,
        source=element.source,
        vertices=[Point3(x=float(x), y=float(y), z=float(z)) for x, y, z in mesh.vertices],
        faces=[(int(f[0]), int(f[1]), int(f[2])) for f in mesh.faces],
        color=_color(element.source, finish, style),
        finish=finish,
    )


ROOF_SKIRT_M = 0.35
"""Prolongement de chaque pente sous son égout (horizontalement) : la toiture passe sous le
plancher et ne laisse pas de filet de ciel à la jonction quand l'égout est bas ou à 0."""


def split_panel(length: float, slope: float, holes: list[Rect]) -> list[Rect]:
    """Le rectangle de la pente moins les trous des velux, en rectangles (bandes le long de
    l'égout, intervalles le long de la pente) : chaque morceau reste un solide convexe."""
    cuts = sorted({0.0, length, *(h[0] for h in holes), *(h[1] for h in holes)})
    cuts = [c for c in cuts if 0.0 <= c <= length]
    out: list[Rect] = []
    for u0, u1 in itertools.pairwise(cuts):
        if u1 - u0 < 1e-6:
            continue
        cover = sorted((h[2], h[3]) for h in holes if h[0] <= u0 + 1e-9 and h[1] >= u1 - 1e-9)
        cursor = 0.0
        for v0, v1 in cover:
            if v0 > cursor + 1e-6:
                out.append((u0, u1, cursor, v0))
            cursor = max(cursor, v1)
        if slope > cursor + 1e-6:
            out.append((u0, u1, cursor, slope))
    return out


def _panel_box(panel: RoofPanel, rect: Rect, t0: float, t1: float) -> Float:
    base = panel.vertices[:4]
    along = base[1] - base[0]
    up = base[3] - base[0]
    u = along / max(float(np.linalg.norm(along)), 1e-12)
    v = up / max(float(np.linalg.norm(up)), 1e-12)
    u0, u1, v0, v1 = rect
    corners = [base[0] + u * a + v * b for a, b in ((u0, v0), (u1, v0), (u1, v1), (u0, v1))]
    return np.array(
        [c + panel.normal * t0 for c in corners] + [c + panel.normal * t1 for c in corners]
    )


def _eave_sill(panel: RoofPanel) -> Float:
    """Semelle horizontale juste sous le plancher, de part et d'autre de la ligne d'égout : le
    plancher est une plaque mince qui s'arrête à la face du mur, et par la fente entre pente et
    plancher on voyait dessous (le ciel)."""
    base = panel.vertices[:4]
    a, b = base[0], base[1]
    up = base[3] - base[0]
    inward = np.array([up[0], up[1], 0.0])
    inward /= max(float(np.linalg.norm(inward)), 1e-12)
    reach = ROOF_SKIRT_M + 0.05
    # Au niveau du plancher (pas de l'égout) : sous un muret d'égout de 1 m, la semelle à
    # hauteur d'égout dépassait dans la pièce comme une corniche.
    floor_z = float(a[2]) - panel.roof.eave_height
    z_top, z_bottom = floor_z - 0.02, floor_z - 0.05
    corners = [a - inward * reach, b - inward * reach, b + inward * reach, a + inward * reach]
    return np.array(
        [[c[0], c[1], z_top] for c in corners] + [[c[0], c[1], z_bottom] for c in corners]
    )


PURLIN_EVERY_M = 2.2


def _gaps(start: float, end: float, cuts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """[start, end] moins les intervalles `cuts` (trous des velux)."""
    out: list[tuple[float, float]] = []
    cursor = start
    for a, b in sorted(cuts):
        if a > cursor + 1e-6:
            out.append((cursor, min(a, end)))
        cursor = max(cursor, b)
    if end > cursor + 1e-6:
        out.append((cursor, end))
    return [(a, b) for a, b in out if b - a > 0.05]


TIMBER_CLEARANCE_M = 0.02
"""Jeu laissé entre une pièce de charpente coupée et le jour d'un velux."""


def reveal_span(
    hole: Rect,
    depth: float,
    pitch: float,
    slope: float = math.inf,
    reveal: float | None = None,
) -> tuple[float, float]:
    """KAN-28 : étendue (v0, v1), le long de la pente, du jour d'un velux jusqu'à `depth` sous
    la toiture. Le caisson (H2, `reveal_solids`) s'évase vers la pièce : le linteau part à
    l'horizontale du haut du trou (`lintel_reach`), l'appui à la verticale du bas, jusqu'à sa
    profondeur (`reveal`, REVEAL_DEPTH_M par défaut, `reveal_depth` d'une pente) ; le jour
    déborde donc du trou de depth·cot(pente) en haut (borné par le faîtage) et de
    depth·tan(pente) en bas."""
    full = REVEAL_DEPTH_M if reveal is None else reveal
    d = min(max(depth, 0.0), full)
    sin_p, cos_p = math.sin(pitch), math.cos(pitch)
    reach = min(d / max(sin_p, 0.05), lintel_reach(hole[3], slope, pitch, full))
    return hole[2] - d * sin_p / max(cos_p, 0.05), hole[3] + reach * cos_p


def timber_rects(
    length: float,
    slope: float,
    spacing: float,
    holes: list[Rect],
    pitch: float = 0.0,
    reveal: float | None = None,
) -> list[tuple[Rect, float, float]]:
    """Charpente apparente d'une pente (C1), dans son repère (u le long de l'égout, v le long
    de la pente) : chevrons espacés de `spacing` et pannes tous les ~2,2 m de pente, chaque
    pièce avec sa profondeur sous la toiture (t0, t1 < 0) ; la faîtière est à part
    (`_ridge_half_beam`). Chevrons et pannes s'interrompent au jour des velux : pas seulement
    au trou dans la pente, mais sur toute l'étendue du caisson évasé à la profondeur de la
    pièce (KAN-28 ; `pitch` = 0 garde l'ancienne coupe au seul trou)."""
    out: list[tuple[Rect, float, float]] = []
    gap = TIMBER_CLEARANCE_M
    rw, rd = RAFTER
    count = max(1, int((length - 0.2) / spacing) + 1)
    first = (length - (count - 1) * spacing) / 2.0
    for k in range(count):
        u = first + k * spacing
        u0, u1 = u - rw / 2, u + rw / 2
        cuts = []
        for h in holes:
            if h[0] < u1 + gap and h[1] > u0 - gap:
                a, b = reveal_span(h, rd, pitch, slope, reveal) if pitch else (h[2], h[3])
                cuts.append((a - gap, b + gap))
        for v0, v1 in _gaps(0.0, slope, cuts):
            out.append(((u0, u1, v0, v1), -rd, 0.0))
    pw, pd = PURLIN
    purlins = max(1, round(slope / PURLIN_EVERY_M))
    for j in range(purlins):
        v = slope * (j + 1) / (purlins + 1)
        v0, v1 = v - pw / 2, v + pw / 2
        cuts = []
        for h in holes:
            # La panne pend sous les chevrons (de rd à rd + pd) : le jour la touche si, à sa
            # profondeur la plus basse encore dans le caisson, il recouvre sa bande.
            a, b = reveal_span(h, rd + pd, pitch, slope, reveal) if pitch else (h[2], h[3])
            if a - gap < v1 and b + gap > v0:
                cuts.append((h[0] - gap, h[1] + gap))
        for u0, u1 in _gaps(0.0, length, cuts):
            out.append(((u0, u1, v0, v1), -rd - pd, -rd))
    return out


def _ridge_half_beam(panel: RoofPanel) -> Float:
    """Moitié de faîtière côté `panel` : section verticale, de la sous-face des chevrons au
    faîtage jusqu'à RIDGE_BEAM[1] plus bas, et de RIDGE_BEAM[0] vers l'intérieur de la pente
    (une demi-poutre perpendiculaire à la pente laisserait un V ouvert sous le faîtage)."""
    base = panel.vertices[:4]
    up = base[3] - base[0]
    run = float(np.hypot(up[0], up[1]))
    inward = -np.array([up[0], up[1], 0.0]) / max(run, 1e-12)
    tan_p = float(up[2]) / max(run, 1e-12)
    drop = RAFTER[1] * math.hypot(1.0, tan_p)
    w, depth = RIDGE_BEAM
    z = np.array([0.0, 0.0, 1.0])
    points: list[Float] = []
    for r in (base[3], base[2]):
        points += [
            r - z * drop,
            r + inward * w - z * (drop + w * tan_p),
            r + inward * w - z * (drop + depth),
            r - z * (drop + depth),
        ]
    return np.array(points)


def panel_timber(panel: RoofPanel, holes: list[Rect]) -> list[Float]:
    """Charpente apparente d'une pente (chevrons, pannes, puis la demi-faîtière en dernier),
    chaque pièce en sommets de son solide convexe ; vide si la charpente est masquée. Seule
    source pour le rendu (`_bake_roof_panel`) et pour la mesure du cadrage (`timber_solids`)."""
    enabled, spacing = timber_settings(panel.roof)
    if not enabled:
        return []
    base = panel.vertices[:4]
    length = float(np.linalg.norm(base[1] - base[0]))
    slope = float(np.linalg.norm(base[3] - base[0]))
    rects = timber_rects(length, slope, spacing, holes, panel.roof.pitch, reveal_depth(panel.roof))
    return [*(_panel_box(panel, r, t0, t1) for r, t0, t1 in rects), _ridge_half_beam(panel)]


def timber_solids(level: Level, graph: WallGraph) -> list[Float]:
    """Charpente apparente du niveau telle que le rendu la bâtit (chevrons, pannes, faîtière),
    chaque pièce en sommets de son solide convexe : la mesure du cadrage des vues la compte
    (une caméra logée dans une panne, ou collée à elle, est refusée)."""
    out: list[Float] = []
    for panel in roof_panels(level, graph):
        out += panel_timber(panel, [h for _, h in window_holes(level, panel)])
    return out


def _furniture_top_under(solids: list[Float], furniture: list[Prism]) -> float | None:
    """Haut du meuble le plus haut qui entre dans le caisson en pleine profondeur (KAN-63) ;
    même test de contact que la règle d'aménagement `velux_casing` (KAN-67) : un meuble sous
    l'ombre du caisson mais plus bas que lui ne le raccourcit pas."""
    boards = [s for s in (Solid.of(points) for points in solids) if s is not None]
    tops = [
        p.z1
        for p in furniture
        if p.kind == "furniture"
        and any(
            b.depth_in(Polygon([(c.x, c.y) for c in p.exterior]), p.z0, p.z1) > SURE_TOUCH_M
            for b in boards
        )
    ]
    return max(tops) if tops else None


def _bake_roof_panel(
    level: Level, panel: RoofPanel, style: BakeStyle, furniture: list[Prism] | None = None
) -> list[Polyhedron]:
    """Une pente pleine, ou percée de ses velux : morceaux de toiture autour des trous, cadre en
    bois et vitrage (finition `glass`) dans chaque trou ; la lumière du ciel entre par là. Le
    caisson de chaque velux s'arrête au-dessus des meubles posés dessous (KAN-63)."""
    base = panel.vertices[:4]
    length = float(np.linalg.norm(base[1] - base[0]))
    slope = float(np.linalg.norm(base[3] - base[0]))
    holes = window_holes(level, panel)
    skirt_slope = ROOF_SKIRT_M / max(math.cos(panel.roof.pitch), 0.2)
    skirt = trimesh.convex.convex_hull(
        _panel_box(panel, (0.0, length, -skirt_slope, 0.0), 0.0, ROOF_THICKNESS)
    )
    sill = trimesh.convex.convex_hull(_eave_sill(panel))
    closing = [
        _polyhedron(skirt, panel.roof, 1000, "roof", style),
        _polyhedron(sill, panel.roof, 1001, "slab", style),
    ]
    tf = _timber_finish()
    pieces = panel_timber(panel, [h for _, h in holes])
    for k, points in enumerate(pieces):
        # La demi-faîtière (dernière) garde son numéro de toujours.
        number = 1999 if k == len(pieces) - 1 else 2000 + k
        beam = _polyhedron(trimesh.convex.convex_hull(points), panel.roof, number, "timber", style)
        # Bois de charpente quelle que soit la finition choisie pour la pente.
        closing.append(
            beam.model_copy(update={"finish": tf, "color": _color(beam.source, tf, style)})
        )
    if not holes:
        hull = trimesh.convex.convex_hull(panel.vertices)
        return [_polyhedron(hull, panel.roof, 0, "roof", style), *closing]
    out: list[Polyhedron] = list(closing)
    for part, rect in enumerate(split_panel(length, slope, [h for _, h in holes])):
        box = trimesh.convex.convex_hull(_panel_box(panel, rect, 0.0, ROOF_THICKNESS))
        out.append(_polyhedron(box, panel.roof, part, "roof", style))
    f = ROOF_WINDOW_FRAME_M
    for w, (u0, u1, v0, v1) in holes:
        frame = [
            (u0, u1, v0, v0 + f),
            (u0, u1, v1 - f, v1),
            (u0, u0 + f, v0 + f, v1 - f),
            (u1 - f, u1, v0 + f, v1 - f),
        ]
        for k, rect in enumerate(frame, start=1):
            box = trimesh.convex.convex_hull(_panel_box(panel, rect, 0.0, ROOF_THICKNESS))
            out.append(_polyhedron(box, w, k, "roof", style))
        pane = _panel_box(
            panel, (u0 + f, u1 - f, v0 + f, v1 - f), 0.4 * ROOF_THICKNESS, 0.6 * ROOF_THICKNESS
        )
        out.append(_polyhedron(trimesh.convex.convex_hull(pane), w, 0, "roof_window", style))
        finish = reveal_finish(level)
        boards = reveal_solids(panel, (u0, u1, v0, v1))
        top = _furniture_top_under(boards, furniture) if furniture else None
        if top is not None:
            boards = reveal_solids(panel, (u0, u1, v0, v1), furniture_top=top)
        for k, solid in enumerate(boards, start=10):
            piece = _polyhedron(trimesh.convex.convex_hull(solid), w, k, "reveal", style)
            out.append(
                piece.model_copy(
                    update={"finish": finish, "color": _color(piece.source, finish, style)}
                )
            )
    return out


_PLANKED = ("naturel", "chalet")


def reveal_finish(level: Level) -> Finish:
    """Ébrasement en planches pour les ambiances bois, en enduit clair sinon (H2)."""
    return "wood_boards" if ambiance(level) in _PLANKED else "plaster"


def niche_planes(panels: list[RoofPanel]) -> Float:
    """KAN-25 : un plan par pente sous lequel reste la niche d'un lit encastré (n·p + c ≤ 0),
    sous les chevrons quand la charpente est apparente, avec le jeu NICHE_GAP_M."""
    rows = []
    for panel in panels:
        depth = RAFTER[1] if timber_settings(panel.roof)[0] else 0.0
        n = panel.normal
        rows.append((*(float(c) for c in n), depth + NICHE_GAP_M - float(n @ panel.origin)))
    return np.array(rows, dtype=np.float64).reshape(-1, 4)


def built_in_niches(level: Level, graph: WallGraph) -> dict[UUID, list[tuple[str, Float]]]:
    """KAN-25 : la niche de chaque lit encastré visible (joues, fond, étagère), en sommets de
    solides convexes. Seule source pour le bake (donc le GLB et Blender) et les tests : sous les
    chevrons, et plafonnée sous les pannes, la faîtière et le caisson en pleine profondeur des
    velux."""
    beds = [
        e
        for e in level.elements.values()
        if isinstance(e, Furniture) and e.visible and e.category == BUILT_IN_BED
    ]
    if not beds:
        return {}
    panels = roof_panels(level, graph)
    planes = niche_planes(panels)
    solids = timber_solids(level, graph)
    solids += [board for _, boards in velux_casings(level, graph) for board in boards]
    obstacles = capping(planes, solids)
    ceiling = level.elevation + level.height - NICHE_GAP_M
    out: dict[UUID, list[tuple[str, Float]]] = {}
    for bed in beds:
        t, b = bed.transform, bed.bbox
        out[bed.id] = niche(
            b.width * t.scale,
            b.depth * t.scale,
            t.position.x,
            t.position.y,
            t.yaw,
            t.position.z,  # même cote que les prismes du coffre (`_bake_built_in_bed`)
            planes,
            ceiling,
            obstacles,
        )
    return out


def _clip_below_roofs(mesh: trimesh.Trimesh, panels: list[RoofPanel]) -> trimesh.Trimesh | None:
    """Ne garde du solide que ce qui est sous chaque pente ; None s'il ne reste rien."""
    for panel in panels:
        mesh = mesh.slice_plane(panel.origin, -panel.normal, cap=True)
        if mesh is None or len(mesh.faces) == 0:
            return None
    return mesh


def bake_level(project: Project, level_id: UUID, style: BakeStyle = "provenance") -> Bake:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise GeometryError(msg)
    graph = WallGraph.from_level(level)
    token = CURRENT_STYLE.set(style_finishes(level, graph))
    try:
        return _bake_level(project, level, graph, style)
    finally:
        CURRENT_STYLE.reset(token)


def _bake_level(project: Project, level: Level, graph: WallGraph, style: BakeStyle) -> Bake:
    panels = roof_panels(level, graph)
    prisms, polyhedra = _bake_walls(level, graph, panels, style)
    openings = _bake_openings(level, graph)
    for slab in _bake_slabs(level, graph, style):
        # Une dalle percée (îlot de murs, trémie) devient un polyèdre : le script Blender ne
        # sait pas extruder une face à trous, trimesh oui.
        if slab.holes:
            mesh = _prism_mesh(slab)
            if mesh is not None:
                element = level.elements[slab.element_id]
                polyhedra.append(_polyhedron(mesh, element, slab.part, "slab", style))
            continue
        prisms.append(slab)
    furniture = _bake_furniture(level, style)
    for panel in panels:
        polyhedra.extend(_bake_roof_panel(level, panel, style, furniture))
    prisms.extend(furniture)
    polyhedra.extend(_bake_niches(level, graph, style))
    prisms.extend(_bake_stairs(level, graph, style))
    # Menuiseries (E5b) : teintes de matière dans les deux styles, c'est de l'habillage.
    prisms.extend(joinery_prisms(level, graph, lambda _o, finish: FINISH_COLORS[finish]))
    decor = dress_level(level, openings) if style == "realistic" else []
    bounds_min = bounds_max = None
    points = [(p.x, p.y, pr.z0) for pr in prisms for p in pr.exterior]
    points += [(p.x, p.y, pr.z1) for pr in prisms for p in pr.exterior]
    points += [(v.x, v.y, v.z) for poly in polyhedra for v in poly.vertices]
    if points:
        xs, ys, zs = zip(*points, strict=True)
        bounds_min = Point3(x=min(xs), y=min(ys), z=min(zs))
        bounds_max = Point3(x=max(xs), y=max(ys), z=max(zs))
    return Bake(
        level_id=level.id,
        level_name=level.name,
        project_name=project.name,
        style=style,
        prisms=prisms,
        polyhedra=polyhedra,
        openings=openings,
        decor=decor,
        palette=palette_of(level) if style == "realistic" else None,
        bounds_min=bounds_min,
        bounds_max=bounds_max,
    )


def _bake_furniture(level: Level, style: BakeStyle) -> list[Prism]:
    """Meubles et éléments fixes : leur boîte, posée à la hauteur de leur transformée et
    tournée de son lacet ; un meuble du catalogue porte aussi son modèle 3D, que le rendu
    met à l'échelle de la boîte."""
    out: list[Prism] = []
    for element in level.elements.values():
        if not isinstance(element, Furniture | Fixture) or not element.visible:
            continue
        t, b = element.transform, element.bbox
        if isinstance(element, Furniture) and element.category == BUILT_IN_BED:
            out.extend(_bake_built_in_bed(element, style))
            continue
        kind: PrismKind = "furniture" if isinstance(element, Furniture) else "fixture"
        corners = footprint_corners(
            t.position.x, t.position.y, t.yaw, b.width * t.scale, b.depth * t.scale
        )
        finish = finish_of(element, kind)
        out.append(
            Prism(
                element_id=element.id,
                kind=kind,
                source=element.source,
                exterior=[Point2(x=x, y=y) for x, y in corners],
                z0=t.position.z,
                z1=t.position.z + b.height * t.scale,
                color=_color(element.source, finish, style),
                finish=finish,
                model=_model_of(element) if isinstance(element, Furniture) else None,
                yaw=t.yaw,
                tints=_tints() if isinstance(element, Furniture) else None,
                woods=_woods() if isinstance(element, Furniture) else None,
            )
        )
    return out


def _bake_built_in_bed(bed: Furniture, style: BakeStyle) -> list[Prism]:
    """Lit encastré sous pente (H1) : coffre dans le bois de l'ambiance, façades de tiroirs un
    ton plus sombre, matelas en lin ; chaque morceau est un prisme du même élément (sa boîte
    reste la référence des règles). La niche, qui suit la pente, est à part (`_bake_niches`)."""
    t, b = bed.transform, bed.bbox
    wood = finish_of(bed, "furniture")
    woods = _woods() or {}
    wood = woods.get("wood", wood)
    dark = woods.get("woodDark", "wood_dark")
    c, s = math.cos(t.yaw), math.sin(t.yaw)
    out: list[Prism] = []
    for index, part in enumerate(bed_parts(b.width * t.scale, b.depth * t.scale)):
        corners = [
            (t.position.x + c * u - s * v, t.position.y + s * u + c * v)
            for u, v in (
                (part.u0, part.v0),
                (part.u1, part.v0),
                (part.u1, part.v1),
                (part.u0, part.v1),
            )
        ]
        finish: Finish = (
            "linen" if part.part == "mattress" else dark if part.part == "drawer" else wood
        )
        out.append(
            Prism(
                element_id=bed.id,
                part=index,
                kind="furniture",
                source=bed.source,
                exterior=[Point2(x=x, y=y) for x, y in corners],
                z0=t.position.z + part.z0,
                z1=t.position.z + part.z1,
                color=_color(bed.source, finish, style),
                finish=finish,
                yaw=t.yaw,
            )
        )
    return out


NICHE_PART = 100
"""Numéro du premier morceau de niche d'un lit encastré (ceux du coffre commencent à 0)."""


def _bake_niches(level: Level, graph: WallGraph, style: BakeStyle) -> list[Polyhedron]:
    """Niche de chaque lit encastré (KAN-25) : joues, fond et étagère dans le bois de l'ambiance,
    comme le coffre ; des polyèdres, car leur dessus suit la pente."""
    out: list[Polyhedron] = []
    woods = _woods() or {}
    for bed_id, solids in built_in_niches(level, graph).items():
        bed = level.elements[bed_id]
        wood = woods.get("wood", finish_of(bed, "furniture"))
        for index, (_name, points) in enumerate(solids, start=NICHE_PART):
            mesh = trimesh.convex.convex_hull(points)
            out.append(
                Polyhedron(
                    element_id=bed.id,
                    part=index,
                    kind="furniture",
                    source=bed.source,
                    vertices=[
                        Point3(x=float(p[0]), y=float(p[1]), z=float(p[2])) for p in mesh.vertices
                    ],
                    faces=[(int(f[0]), int(f[1]), int(f[2])) for f in mesh.faces],
                    color=_color(bed.source, wood, style),
                    finish=wood,
                )
            )
    return out


def _timber_finish() -> Finish:
    """Charpente (C1) : bois massif vieilli, sauf si l'ambiance de la pièce la veut blanchie ou
    sombre (V1)."""
    styled = CURRENT_STYLE.get()
    return styled.by_surface.get("timber", "timber") if styled is not None else "timber"


def _woods() -> dict[str, Finish] | None:
    styled = CURRENT_STYLE.get()
    return dict(styled.woods) if styled is not None and styled.woods else None


def _tints() -> dict[str, tuple[float, float, float]] | None:
    styled = CURRENT_STYLE.get()
    return dict(styled.tints) if styled is not None and styled.tints else None


def _model_of(furniture: Furniture) -> str | None:
    """Modèle 3D du meuble s'il est au catalogue et présent sur le disque."""
    item = load_catalog().get(furniture.asset_id)
    if item is None or item.model is None or not asset_file(item.model).is_file():
        return None
    return item.model


@lru_cache(maxsize=64)
def _unit_model(relative: str) -> trimesh.Trimesh:
    """Modèle du catalogue ramené au repère du plan : centré, posé sur z = 0, face avant
    vers +y, de taille 1 × 1 × 1 ; couleurs de ses matières en couleurs de faces. Les
    modèles Kenney sont Y vers le haut, face avant +Z : (x, y, z) → (−x, z, y), rotation
    propre (déterminant +1)."""
    scene = cast(trimesh.Scene, trimesh.load(asset_file(relative), force="scene"))
    meshes: list[trimesh.Trimesh] = []
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        geometry = scene.geometry[name]
        if not isinstance(geometry, trimesh.Trimesh):
            continue
        mesh = geometry.copy()
        mesh.apply_transform(transform)
        material = getattr(geometry.visual, "material", None)
        factor = getattr(material, "baseColorFactor", None)
        rgba = [int(c) for c in factor] if factor is not None else [200, 200, 200, 255]
        mesh.visual = ColorVisuals(mesh=mesh, face_colors=[rgba] * len(mesh.faces))
        meshes.append(mesh)
    merged = trimesh.util.concatenate(meshes)
    axes = np.array(
        [[-1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    )
    merged.apply_transform(axes)
    lo, hi = merged.bounds
    merged.apply_translation((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2]))
    size = np.maximum(hi - lo, 1e-6)
    merged.apply_scale(1.0 / size)  # type: ignore[no-untyped-call]
    return cast(trimesh.Trimesh, merged)


def furniture_mesh(prism: Prism) -> trimesh.Trimesh | None:
    """Le modèle d'un meuble à la taille et à la place de sa boîte (repère du plan)."""
    if prism.model is None:
        return None
    xs = [p.x for p in prism.exterior]
    ys = [p.y for p in prism.exterior]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    c, s = math.cos(prism.yaw), math.sin(prism.yaw)
    # Largeur le long de l'axe local x, profondeur le long de y (même convention que la boîte).
    p0, p1, p3 = prism.exterior[0], prism.exterior[1], prism.exterior[3]
    width = math.hypot(p1.x - p0.x, p1.y - p0.y)
    depth = math.hypot(p3.x - p0.x, p3.y - p0.y)
    if abs((p1.x - p0.x) * c + (p1.y - p0.y) * s) < 0.5 * width:
        width, depth = depth, width
    mesh = _unit_model(prism.model).copy()
    mesh.apply_scale((width, depth, prism.z1 - prism.z0))  # type: ignore[no-untyped-call]
    rotation = trimesh.transformations.rotation_matrix(prism.yaw, [0.0, 0.0, 1.0])  # type: ignore[no-untyped-call]
    mesh.apply_transform(rotation)
    mesh.apply_translation((cx, cy, prism.z0))
    return mesh


def _bake_openings(level: Level, graph: WallGraph) -> list[BakedOpening]:
    """Portes et fenêtres visibles, repérées sur l'axe de leur mur (habillage de rendu)."""
    out: list[BakedOpening] = []
    for element in level.elements.values():
        if not isinstance(element, Opening) or not element.visible:
            continue
        wall = graph.walls.get(element.host_wall)
        if wall is None or not wall.visible:
            continue
        try:
            ux, uy = graph.wall_direction(element.host_wall)
        except GeometryError:
            continue
        a, _ = graph.wall_axis(element.host_wall)
        nx, ny = -uy, ux
        half = wall.thickness / 2.0
        shift = {"center": 0.0, "left": half, "right": -half}[wall.offset]
        along = element.offset + element.width / 2.0
        out.append(
            BakedOpening(
                element_id=element.id,
                kind="door" if element.kind in DOOR_KINDS else "window",
                source=element.source,
                center=Point3(
                    x=a.x + ux * along + nx * shift,
                    y=a.y + uy * along + ny * shift,
                    z=level.elevation + element.sill_height,
                ),
                direction=(ux, uy),
                normal=(nx, ny),
                width=element.width,
                height=element.height,
                wall_thickness=wall.thickness,
            )
        )
    return out


def _bake_walls(
    level: Level, graph: WallGraph, panels: list[RoofPanel], style: BakeStyle
) -> tuple[list[Prism], list[Polyhedron]]:
    """Murs en prismes ; sous des pentes, chaque morceau est découpé et devient un polyèdre."""
    openings_by_wall: dict[UUID, list[Opening]] = defaultdict(list)
    for element in level.elements.values():
        if isinstance(element, Opening) and element.visible:
            openings_by_wall[element.host_wall].append(element)
    prisms: list[Prism] = []
    polyhedra: list[Polyhedron] = []
    footprints = graph.wall_footprints()
    for wid, wall in graph.walls.items():
        if not wall.visible:
            continue
        try:
            ux, uy = graph.wall_direction(wid)
        except GeometryError:
            continue
        a, _ = graph.wall_axis(wid)
        length = graph.wall_length(wid)
        height = wall.height if wall.height is not None else level.height
        nx, ny = -uy, ux
        half = wall.thickness / 2.0
        shift = {"center": 0.0, "left": half, "right": -half}[wall.offset]
        finish = finish_of(wall, "wall")
        footprint = footprints.get(wid)
        for part, seg in enumerate(wall_segments(length, height, openings_by_wall.get(wid, []))):
            p0 = (a.x + ux * seg.s0 + nx * shift, a.y + uy * seg.s0 + ny * shift)
            p1 = (a.x + ux * seg.s1 + nx * shift, a.y + uy * seg.s1 + ny * shift)
            polygon = LineString([p0, p1]).buffer(half, cap_style="flat")
            if footprint is not None:
                polygon = _mitered_piece(footprint, polygon, (a.x, a.y), (ux, uy), seg, length)
            prism = Prism(
                element_id=wid,
                part=part,
                kind="wall",
                source=wall.source,
                exterior=to_points(polygon),
                z0=level.elevation + seg.z0,
                z1=level.elevation + seg.z1,
                color=_color(wall.source, finish, style),
                finish=finish,
            )
            if not panels:
                prisms.append(prism)
                continue
            mesh = _prism_mesh(prism)
            if mesh is None:
                continue
            clipped = _clip_below_roofs(mesh, panels)
            if clipped is not None:
                polyhedra.append(_polyhedron(clipped, wall, part, "wall", style))
    return prisms, polyhedra


def _mitered_piece(
    footprint: Polygon,
    flat: Polygon,
    origin: tuple[float, float],
    u: tuple[float, float],
    seg: WallSegment,
    length: float,
) -> Polygon:
    """Morceau de mur entre s0 et s1 le long de l'axe, découpé dans l'emprise raccordée
    (KAN-29) : un morceau qui touche un bout du mur garde l'onglet de l'angle ; les coupes
    d'une baie restent perpendiculaires à l'axe."""
    if seg.s0 > 1e-9 and seg.s1 < length - 1e-9:
        return flat
    if seg.s0 <= 1e-9 and seg.s1 >= length - 1e-9:
        return footprint
    reach = length + 100.0
    s0 = -reach if seg.s0 <= 1e-9 else seg.s0
    s1 = reach if seg.s1 >= length - 1e-9 else seg.s1
    (ox, oy), (ux, uy) = origin, u
    nx, ny = -uy, ux
    strip = Polygon(
        [
            (ox + ux * s + nx * t, oy + uy * s + ny * t)
            for s, t in ((s0, -reach), (s1, -reach), (s1, reach), (s0, reach))
        ]
    )
    piece = largest_polygon(footprint.intersection(strip))
    return flat if piece.is_empty else orient(piece, sign=1.0)


def _prism_mesh(prism: Prism) -> trimesh.Trimesh | None:
    polygon = Polygon(
        [(p.x, p.y) for p in prism.exterior],
        [[(p.x, p.y) for p in hole] for hole in prism.holes],
    )
    if polygon.is_empty or polygon.area <= 0.0 or prism.z1 <= prism.z0:
        return None
    mesh = extrude_polygon(polygon, prism.z1 - prism.z0)
    mesh.apply_translation((0.0, 0.0, prism.z0))
    return mesh


def _bake_slabs(level: Level, graph: WallGraph, style: BakeStyle) -> list[Prism]:
    out: list[Prism] = []
    for element in level.elements.values():
        if not isinstance(element, Floor | Ceiling) or not element.visible:
            continue
        polygon = _slab_polygon(element, level, graph)
        if polygon is None or polygon.is_empty:
            continue
        kind: PrismKind
        if isinstance(element, Floor):
            z0, z1 = level.elevation - SLAB, level.elevation
            kind = "floor"
        else:
            top = level.elevation + (element.height if element.height is not None else level.height)
            z0, z1 = top, top + SLAB
            kind = "ceiling"
        finish = finish_of(element, kind)
        out.append(
            Prism(
                element_id=element.id,
                kind=kind,
                source=element.source,
                exterior=to_points(polygon),
                holes=[
                    [Point2(x=float(x), y=float(y)) for x, y in ring.coords[:-1]]
                    for ring in polygon.interiors
                ],
                z0=z0,
                z1=z1,
                color=FINISH_COLORS[finish] if style == "realistic" else SLAB_COLORS[kind],
                finish=finish,
            )
        )
    return out


def _slab_polygon(element: Floor | Ceiling, level: Level, graph: WallGraph) -> Polygon | None:
    polygon = _slab_outline(element, level, graph)
    if polygon is None or not isinstance(element, Floor):
        return polygon
    # Trémies (S1) : le plancher s'ouvre au-dessus de chaque escalier qui arrive ici.
    for _, opening, _arrival in stair_zones(level):
        cut = polygon.difference(opening)
        if isinstance(cut, Polygon) and not cut.is_empty:
            polygon = cut
    return polygon


def _slab_outline(element: Floor | Ceiling, level: Level, graph: WallGraph) -> Polygon | None:
    if not element.derived and element.polygon is not None:
        if len(element.polygon) < 3:
            return None
        return Polygon([(p.x, p.y) for p in element.polygon])
    if not isinstance(level.elements.get(element.room), Room):
        return None
    try:
        return graph.room_polygon(element.room)
    except GeometryError:
        return None


METAL_RAILING = frozenset({"industriel", "contemporain"})
"""Ambiances au garde-corps en métal noir ; les autres l'ont en bois de charpente."""
RAILING_HEIGHT_M = 1.0
SPIRAL_POST_M = 0.06
SHAFT_WALL_M = 0.10
"""Épaisseur des murs de cage sous la trémie."""


def _stair_shaft(
    opening: Polygon, base: float, rise: float
) -> list[tuple[Polygon, float, float, Finish]]:
    """Cage sous la trémie : un mur enduit par côté jusqu'au plancher d'en dessous, et ce
    plancher (parquet) sous l'escalier."""
    bottom = base - rise
    out: list[tuple[Polygon, float, float, Finish]] = []
    floor = opening.buffer(SHAFT_WALL_M, join_style=2)
    if isinstance(floor, Polygon):
        out.append((floor, bottom - SLAB, bottom, "wood_floor"))
    for a, b in itertools.pairwise(opening.exterior.coords):
        edge = LineString([a, b])
        if edge.length < 1e-3:
            continue
        wall = edge.buffer(SHAFT_WALL_M, cap_style=2).difference(opening)
        if isinstance(wall, Polygon) and wall.area > 1e-6:
            out.append((wall, bottom, base - SLAB, "plaster"))
    return out


def _bake_stairs(level: Level, graph: WallGraph, style: BakeStyle) -> list[Prism]:
    """Marches pleines (volées) ou palettes sur fût (hélicoïdal), et garde-corps : main
    courante, lisse basse et barreaux, en bois ou en métal selon l'ambiance de la pièce."""
    walls = graph.walls_union() if graph.walls else None
    styled = CURRENT_STYLE.get()
    if styled is not None and styled.by_surface:
        # Pièce avec une ambiance (V1) : le garde-corps suit le style comme le reste.
        rail_finish: Finish = STYLE_RAILING.get(styled.style, "timber")
    else:
        rail_finish = "metal" if ambiance(level) in METAL_RAILING else "timber"
    out: list[Prism] = []
    for e in level.elements.values():
        if not isinstance(e, Stairs) or not e.visible:
            continue
        lay = stair_layout(e)
        tread_finish = finish_of(e, "stairs")
        base = level.elevation
        part = 0

        def prism(shape: Polygon, z0: float, z1: float, finish: Finish, el: Stairs = e) -> Prism:
            nonlocal part
            part += 1
            return Prism(
                element_id=el.id,
                part=part - 1,
                kind="stairs",
                source=el.source,
                exterior=to_points(shape),
                z0=z0,
                z1=z1,
                color=_color(el.source, finish, style),
                finish=finish,
            )

        for t in lay.treads:
            thick = 0.05 if e.kind == "spiral" else lay.steps.riser_m
            out.append(prism(t.polygon, base + t.z_top - thick, base + t.z_top, tread_finish))
        if e.kind == "spiral":
            post = Point(e.position.x, e.position.y).buffer(SPIRAL_POST_M, quad_segs=6)
            out.append(prism(post, base - e.rise, base + RAILING_HEIGHT_M, rail_finish))
        # Cage : murs enduits autour de la trémie jusqu'au plancher d'en dessous, et ce
        # plancher sous l'escalier ; le niveau inférieur n'est pas toujours dessiné, et sans
        # eux on verrait le ciel au fond de la trémie.
        for shape, z0, z1, finish in _stair_shaft(lay.opening, base, e.rise):
            out.append(prism(shape, z0, z1, finish))
        if not e.railing:
            continue
        spacing, size = (0.12, 0.02) if rail_finish == "metal" else (0.15, 0.04)
        for line in railing_lines(lay, walls):
            path = LineString(line)
            rail = path.buffer(0.025, cap_style=2, join_style=2)
            if isinstance(rail, Polygon):
                top = base + RAILING_HEIGHT_M
                out.append(prism(rail, top - 0.05, top, rail_finish))
                low = path.buffer(0.015, cap_style=2, join_style=2)
                if isinstance(low, Polygon):
                    out.append(prism(low, base + 0.08, base + 0.11, rail_finish))
            count = max(1, int(path.length / spacing))
            for k in range(count + 1):
                p = path.interpolate(k * path.length / count)
                square = Polygon(
                    [
                        (p.x - size / 2, p.y - size / 2),
                        (p.x + size / 2, p.y - size / 2),
                        (p.x + size / 2, p.y + size / 2),
                        (p.x - size / 2, p.y + size / 2),
                    ]
                )
                out.append(prism(square, base, base + RAILING_HEIGHT_M - 0.05, rail_finish))
    return out


def build_scene(bake: Bake) -> trimesh.Scene:
    """Maillage trimesh : un nœud par élément, nommé par son UUID, coloré par provenance."""
    scene = trimesh.Scene()
    parts: dict[UUID, list[trimesh.Trimesh]] = defaultdict(list)
    for prism in bake.prisms:
        model = furniture_mesh(prism)
        if model is not None:
            parts[prism.element_id].append(model)
            continue
        mesh = _prism_mesh(prism)
        if mesh is None:
            continue
        rgba = [*prism.color, 255]
        mesh.visual = ColorVisuals(mesh=mesh, face_colors=[rgba] * len(mesh.faces))
        parts[prism.element_id].append(mesh)
    for poly in bake.polyhedra:
        mesh = trimesh.Trimesh(
            vertices=[(v.x, v.y, v.z) for v in poly.vertices], faces=poly.faces, process=False
        )
        rgba = [*poly.color, 255]
        mesh.visual = ColorVisuals(mesh=mesh, face_colors=[rgba] * len(mesh.faces))
        parts[poly.element_id].append(mesh)
    for element_id, meshes in parts.items():
        merged = meshes[0] if len(meshes) == 1 else trimesh.util.concatenate(meshes)
        scene.add_geometry(
            cast(trimesh.Trimesh, merged), node_name=str(element_id), geom_name=str(element_id)
        )
    return scene


def scene_volume(scene: trimesh.Scene) -> float:
    return float(sum(abs(g.volume) for g in scene.geometry.values()))


Z_UP_TO_Y_UP = trimesh.transformations.rotation_matrix(  # type: ignore[no-untyped-call]
    -math.pi / 2.0, [1.0, 0.0, 0.0]
)
"""Le format de scène est Z vers le haut ; glTF est Y vers le haut. Sans cette rotation, un
viewer (Three.js) affiche la profondeur du plan à la verticale. Blender applique la même
conversion à l'export."""


def empty_glb() -> bytes:
    """GLB valide sans aucun nœud : un niveau encore vide (projet neuf, tout effacé).
    trimesh refuse d'exporter une scène vide (et de la tourner : KeyError 'world'), ce qui
    donnait une erreur 500 à l'ouverture de la 3D ; le visualiseur, lui, sait afficher rien."""
    doc = json.dumps({"asset": {"version": "2.0"}, "scene": 0, "scenes": [{}]}).encode()
    doc += b" " * (-len(doc) % 4)
    chunk = struct.pack("<II", len(doc), 0x4E4F534A) + doc
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunk)) + chunk


def glb_bytes(project: Project, level_id: UUID, style: BakeStyle = "provenance") -> bytes:
    scene = build_scene(bake_level(project, level_id, style))
    if not scene.geometry:
        return empty_glb()
    scene.apply_transform(Z_UP_TO_Y_UP)  # type: ignore[no-untyped-call]
    data = scene.export(file_type="glb")  # type: ignore[no-untyped-call]
    return cast(bytes, data)


def export_glb(project: Project, level_id: UUID, out: Path) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(glb_bytes(project, level_id))
    return Artifact(name=out.name, path=out, media_type="model/gltf-binary", description="Scène 3D")


def export_bake(project: Project, level_id: UUID, out: Path) -> Artifact:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(bake_level(project, level_id).model_dump_json(indent=2) + "\n", encoding="utf-8")
    return Artifact(
        name=out.name, path=out, media_type="application/json", description="Prismes 3D"
    )
