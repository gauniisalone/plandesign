"""Volume des velux dans la toiture (KAN-67) : panneaux des pentes, trous des velux et caisson
de chaque velux planche par planche, sans maillage (numpy seulement). Seule source du volume pour
le bake (`pipeline.scene3d`), le générateur d'aménagement et les règles (`validation.layout`) ;
ici, dans `geom`, pour que les règles n'importent pas le pipeline (couches openplan)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import numpy as np
from numpy.typing import NDArray

from openplan.geom.roof import horizontal_run, inward_normal
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.elements import RoofPlane, RoofWindow, Room
from openplan.schemas.scene import Level

Float = NDArray[np.float64]

ROOF_THICKNESS = 0.05
"""Épaisseur des panneaux de pente (visuelle, posée vers l'extérieur)."""


@dataclass(frozen=True)
class RoofPanel:
    """Pente prête à bâtir : origine sur l'égout, normale montante (côté extérieur),
    huit sommets du panneau (quatre sur le plan, quatre décalés de l'épaisseur)."""

    roof: RoofPlane
    origin: Float
    normal: Float
    vertices: Float


def _roof_center(roof: RoofPlane, level: Level, graph: WallGraph) -> tuple[float, float] | None:
    """Point vers lequel la pente monte : centre de sa pièce, sinon centre des sommets."""
    for rid in roof.rooms:
        if isinstance(level.elements.get(rid), Room):
            try:
                c = graph.room_polygon(rid).centroid
            except GeometryError:
                continue
            return (float(c.x), float(c.y))
    if level.vertices:
        xs = [v.position.x for v in level.vertices.values()]
        ys = [v.position.y for v in level.vertices.values()]
        return ((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
    return None


def roof_panels(level: Level, graph: WallGraph) -> list[RoofPanel]:
    """Panneaux des pentes visibles qui montent effectivement jusqu'à la hauteur du niveau."""
    out: list[RoofPanel] = []
    for element in level.elements.values():
        if not isinstance(element, RoofPlane) or not element.visible:
            continue
        a = (element.eave_start.x, element.eave_start.y)
        b = (element.eave_end.x, element.eave_end.y)
        run = horizontal_run(element.eave_height, element.pitch, level.height)
        if run <= 0.0 or math.hypot(b[0] - a[0], b[1] - a[1]) < 1e-9:
            continue
        nx, ny = inward_normal(a, b, _roof_center(element, level, graph))
        z0 = level.elevation + element.eave_height
        z1 = level.elevation + level.height
        base = np.array(
            [
                [a[0], a[1], z0],
                [b[0], b[1], z0],
                [b[0] + nx * run, b[1] + ny * run, z1],
                [a[0] + nx * run, a[1] + ny * run, z1],
            ]
        )
        along = base[1] - base[0]
        up_slope = base[3] - base[0]
        normal = np.cross(along, up_slope)
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
        if normal[2] < 0:
            normal = -normal
        vertices: Float = np.vstack([base, base + normal * ROOF_THICKNESS])
        out.append(RoofPanel(roof=element, origin=base[0], normal=normal, vertices=vertices))
    return out


ROOF_WINDOW_FRAME_M = 0.06
"""Largeur du cadre d'un velux (bois), autour du vitrage."""

Rect = tuple[float, float, float, float]
"""(u0, u1, v0, v1) dans le repère d'une pente : u le long de l'égout, v le long de la pente."""

RAFTER = (0.08, 0.16)
"""Chevron : largeur le long de l'égout, hauteur sous la toiture (8 × 16 cm)."""

PURLIN = (0.15, 0.22)
"""Panne : largeur le long de la pente, hauteur (15 × 22 cm)."""

RIDGE_BEAM = (0.07, 0.25)
"""Faîtière : demi-largeur de chaque côté du faîtage, hauteur sous les chevrons ; chaque pente
en pose une moitié, et les deux se rejoignent dans le plan vertical du faîtage."""

TIMBER_SPACING = (0.35, 0.60, 1.20)
"""Entraxe des chevrons : minimum, défaut, maximum (mètres)."""


def timber_settings(roof: RoofPlane) -> tuple[bool, float]:
    """(charpente apparente ?, entraxe des chevrons) d'après `metadata["timber"]` de la pente ;
    apparente par défaut, entraxe de 60 cm."""
    raw = roof.metadata.get("timber")
    if not isinstance(raw, dict):
        return True, TIMBER_SPACING[1]
    enabled = raw.get("enabled", True) is not False
    spacing = raw.get("spacing", TIMBER_SPACING[1])
    if not isinstance(spacing, int | float):
        spacing = TIMBER_SPACING[1]
    return enabled, min(TIMBER_SPACING[2], max(TIMBER_SPACING[0], float(spacing)))


def lintel_reach(hole_top: float, slope: float, pitch: float, reveal: float | None = None) -> float:
    """Portée horizontale du linteau d'un velux (H2), du haut du trou vers le faîtage : jusqu'à
    la profondeur du caisson (`reveal`, REVEAL_DEPTH_M par défaut) sous la toiture, mais jamais
    au-delà de la demi-faîtière (KAN-28 : près du faîtage, sur une pente douce, il traversait
    la faîtière et l'autre versant)."""
    depth = REVEAL_DEPTH_M if reveal is None else reveal
    to_ridge = (slope - hole_top) * math.cos(pitch) - RIDGE_BEAM[0]
    return max(0.0, min(depth / max(math.sin(pitch), 0.05), to_ridge))


def window_holes(level: Level, panel: RoofPanel) -> list[tuple[RoofWindow, Rect]]:
    """Les velux visibles d'une pente et leur trou (u0, u1, v0, v1) dans son repère, bornés à la
    pente ; un velux plus petit que son cadre n'en fait pas."""
    base = panel.vertices[:4]
    length = float(np.linalg.norm(base[1] - base[0]))
    slope = float(np.linalg.norm(base[3] - base[0]))
    holes: list[tuple[RoofWindow, Rect]] = []
    for w in level.elements.values():
        if isinstance(w, RoofWindow) and w.visible and w.roof == panel.roof.id:
            u0, u1 = max(0.0, w.offset), min(length, w.offset + w.width)
            v0, v1 = max(0.0, w.distance), min(slope, w.distance + w.length)
            if u1 - u0 > 2 * ROOF_WINDOW_FRAME_M and v1 - v0 > 2 * ROOF_WINDOW_FRAME_M:
                holes.append((w, (u0, u1, v0, v1)))
    return holes


REVEAL_DEPTH_M = RAFTER[1] + 0.06
"""Profondeur de l'ébrasement sous la toiture (H2) : la hauteur des chevrons et la doublure."""

CHEVETRE_DEPTH_M = RAFTER[1] + PURLIN[1] + 0.02
"""KAN-63 : sous une charpente apparente, le caisson descend jusqu'au bas des pannes (0,40 m),
comme un chevêtre ; sinon le bout d'une panne coupée au droit de la joue, plus bas que le
caisson, masquait un coin de la vitre en vue rasante."""

SILL_FLOOR_GAP_M = 0.10
"""L'appui vertical d'un caisson profond s'arrête à 10 cm au moins au-dessus du plancher."""


def reveal_depth(roof: RoofPlane) -> float:
    """Profondeur du caisson des velux d'une pente : jusqu'au bas des pannes si la charpente est
    apparente (KAN-63), l'ébrasement de H2 (REVEAL_DEPTH_M) sinon."""
    return CHEVETRE_DEPTH_M if timber_settings(roof)[0] else REVEAL_DEPTH_M


REVEAL_BOARD_M = 0.025


def velux_casings(level: Level, graph: WallGraph) -> list[tuple[RoofWindow, list[Float]]]:
    """KAN-67 : le caisson de chaque velux visible, en pleine profondeur (aucun meuble pris en
    compte), planche par planche, tel que le bake le bâtit (`reveal_solids` sur `window_holes`) :
    seule source du volume pour les règles d'aménagement et le générateur."""
    return [
        (window, reveal_solids(panel, rect))
        for panel in roof_panels(level, graph)
        for window, rect in window_holes(level, panel)
    ]


FURNITURE_GAP_M = 0.02
"""KAN-63 : jeu laissé entre le bas du caisson et le meuble le plus haut posé dessous."""


def reveal_solids(panel: RoofPanel, rect: Rect, furniture_top: float | None = None) -> list[Float]:
    """Caisson d'un velux sous la toiture (H2, références 12 et 14) : linteau horizontal en haut,
    appui vertical en bas, joues perpendiculaires à la pente ; le jour s'évase vers la pièce.
    Chaque planche est un solide convexe de `REVEAL_BOARD_M` d'épaisseur ; profondeur
    `reveal_depth(panel.roof)` (KAN-63). `furniture_top` : haut du meuble le plus haut sous
    l'emprise du caisson ; l'appui s'arrête FURNITURE_GAP_M au-dessus, sans remonter plus haut
    que l'ébrasement de H2 ni descendre à moins de SILL_FLOOR_GAP_M du plancher."""
    base = panel.vertices[:4]
    along = base[1] - base[0]
    up = base[3] - base[0]
    u_dir = along / max(float(np.linalg.norm(along)), 1e-12)
    v_dir = up / max(float(np.linalg.norm(up)), 1e-12)
    n = panel.normal
    u0, u1, v0, v1 = rect

    def at(u: float, v: float) -> Float:
        return cast(Float, base[0] + u_dir * u + v_dir * v)

    # Vers l'intérieur depuis le haut : à l'horizontale, vers le faîtage (sous la pente qui
    # monte) ; depuis le bas : à la verticale. Chacun s'arrête au plan situé à la profondeur du
    # caisson sous la toiture (`reveal_depth` : jusqu'au bas des pannes sous une charpente
    # apparente, KAN-63).
    depth = reveal_depth(panel.roof)
    to_ridge = np.array([v_dir[0], v_dir[1], 0.0])
    to_ridge /= max(float(np.linalg.norm(to_ridge)), 1e-12)
    vertical = np.array([0.0, 0.0, -1.0])
    # Le linteau s'arrête avant la faîtière (KAN-28).
    reach_h = lintel_reach(v1, float(np.linalg.norm(up)), panel.roof.pitch, depth)
    cos_v = max(-float(np.dot(vertical, n)), 0.05)
    reach_v = depth / cos_v
    top = [at(u0, v1), at(u1, v1)]
    top_in = [p + to_ridge * reach_h for p in top]
    low = [at(u0, v0), at(u1, v0)]
    low_z = float(low[0][2])
    # L'appui s'arrête au-dessus du meuble le plus haut posé dessous (KAN-63), sans remonter
    # plus haut que l'ébrasement de H2, et jamais à moins de SILL_FLOOR_GAP_M du plancher.
    if furniture_top is not None:
        reach_v = min(reach_v, low_z - furniture_top - FURNITURE_GAP_M)
        reach_v = max(reach_v, REVEAL_DEPTH_M / cos_v)
    floor_z = float(base[0][2]) - panel.roof.eave_height
    reach_v = max(0.0, min(reach_v, low_z - floor_z - SILL_FLOOR_GAP_M))
    low_in = [p + vertical * reach_v for p in low]
    t = REVEAL_BOARD_M
    up_z = np.array([0.0, 0.0, 1.0])
    lintel = [*top, *top_in[::-1]]
    sill = [*low, *low_in[::-1]]
    cheek0 = [low[0], top[0], top_in[0], low_in[0]]
    cheek1 = [low[1], top[1], top_in[1], low_in[1]]
    return [
        np.array([*lintel, *(p + up_z * t for p in lintel)]),
        np.array([*sill, *(p - to_ridge * t for p in sill)]),
        np.array([*cheek0, *(p - u_dir * t for p in cheek0)]),
        np.array([*cheek1, *(p + u_dir * t for p in cheek1)]),
    ]
