"""Menuiseries des ouvertures en 3D (E5b) : chambranles, vantaux, vitrages, rails.

Des prismes ordinaires (`kind="joinery"`), donc la même géométrie dans l'aperçu du navigateur
(GLB trimesh) et dans le rendu Blender. Vantaux fermés, dans le plan du mur (au milieu de son
épaisseur) ; une coulissante en applique a son vantail contre la face du mur, côté rail, et son
rail au-dessus de la baie et du tronçon où elle s'efface ; une baie coulissante a ses deux
vantaux décalés dans l'épaisseur. Repère de chaque boîte : abscisse `s` le long de l'axe du mur
(depuis son départ), décalage `k` le long de sa normale gauche, cotes `z`.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from shapely.geometry import Polygon

from openplan.geom.opening_symbols import is_pair, stack_span, swing_side
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.pipeline.style import door_leaf_finish, floor_finish
from openplan.schemas.bake import Finish, Prism
from openplan.schemas.common import Point2
from openplan.schemas.elements import Opening, OpeningKind
from openplan.schemas.scene import Level
from openplan.validation.layout import _inside_sign, _room_polygons

FRAME_M = 0.05
"""Largeur vue d'un chambranle (montants et traverse haute)."""
LEAF_M = 0.04
STILE_M = 0.06
"""Montants et traverses d'un vantail vitré."""
GLASS_M = 0.012
RAIL_M = 0.05
REBATE_M = 0.01
"""Recouvrement de deux vantaux voisins (battement), au lieu d'un jour entre eux."""
REBATE_SHIFT_M = 0.006
"""Décalage dans l'épaisseur entre deux vantaux qui se recouvrent."""
WINDOW_OVERLAP = 0.55
FRAME_FINISH: Finish = "plaster"
LEAF_FINISH: Finish = "wood_dark"
GLASS_FINISH: Finish = "glass"
RAIL_FINISH: Finish = "metal"
THRESHOLD_FINISH: Finish = "wood_floor"
THRESHOLD_M = 0.02
"""Épaisseur du seuil sous une porte : celle de la dalle du sol (`scene3d.SLAB`), pour ne
pas descendre sous la scène."""
LOW_SILL_M = 0.05
"""En dessous de cette allège, l'ouverture descend au sol : elle a un seuil."""

Box = Callable[[float, float, float, float, float, float, Finish], None]


def joinery_prisms(
    level: Level, graph: WallGraph, color: Callable[[Opening, Finish], tuple[int, int, int]]
) -> list[Prism]:
    """Les menuiseries de toutes les ouvertures visibles du niveau ; `color` donne la teinte
    de chaque boîte selon le style de la scène."""
    out: list[Prism] = []
    rooms: dict[UUID, Polygon] | None = None
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
        half = wall.thickness / 2.0
        shift = {"center": 0.0, "left": half, "right": -half}[wall.offset]
        parts: list[Prism] = []
        box = _box_maker(element, (a.x, a.y), (ux, uy), shift, parts, color)
        z_sill = level.elevation + element.sill_height
        if element.kind is OpeningKind.PASSAGE:
            if rooms is None:
                rooms = _room_polygons(level, graph)
            along = element.offset + element.width / 2.0
            inside = _inside_sign(graph, element.host_wall, along, rooms)
            _corridor(element, box, half, z_sill, -inside)
        else:
            _opening_parts(element, half, z_sill, box)
        out.extend(parts)
    return out


def _box_maker(
    o: Opening,
    origin: tuple[float, float],
    u: tuple[float, float],
    shift: float,
    sink: list[Prism],
    color: Callable[[Opening, Finish], tuple[int, int, int]],
) -> Box:
    """Une boîte orientée dans le repère du mur hôte, ajoutée à `sink`."""

    def box(
        s0: float, s1: float, k0: float, k1: float, z0: float, z1: float, finish: Finish
    ) -> None:
        if s1 - s0 < 1e-4 or k1 - k0 < 1e-4 or z1 - z0 < 1e-4:
            return
        corners = [(s0, k0 + shift), (s1, k0 + shift), (s1, k1 + shift), (s0, k1 + shift)]
        exterior = [
            Point2(x=origin[0] + u[0] * s - u[1] * k, y=origin[1] + u[1] * s + u[0] * k)
            for s, k in corners
        ]
        sink.append(
            Prism(
                element_id=o.id,
                part=len(sink),
                kind="joinery",
                source=o.source,
                exterior=exterior,
                z0=z0,
                z1=z1,
                color=color(o, finish),
                finish=finish,
            )
        )

    return box


def _threshold(o: Opening, half: float, z_sill: float, box: Box) -> None:
    """Seuil dans l'épaisseur du mur sous une ouverture qui descend au sol : sans lui, le sol
    s'arrête au nu du mur et l'on voit le vide (le ciel) au pied de la porte, un liseré bleu."""
    if o.sill_height < LOW_SILL_M:
        s0, s1 = o.offset, o.offset + o.width
        box(s0, s1, -half, half, z_sill - THRESHOLD_M, z_sill, floor_finish(THRESHOLD_FINISH))


def _opening_parts(o: Opening, half: float, z_sill: float, box: Box) -> None:
    """Chambranle commun, puis vantaux ou vitrages selon la sorte."""
    s0, s1 = o.offset, o.offset + o.width
    z_top = z_sill + o.height
    # Le chambranle reste dans l'épaisseur du mur : l'emprise de la scène ne change pas.
    depth = (-half, half)
    _threshold(o, half, z_sill, box)
    box(s0, s0 + FRAME_M, *depth, z_sill, z_top, FRAME_FINISH)
    box(s1 - FRAME_M, s1, *depth, z_sill, z_top, FRAME_FINISH)
    box(s0, s1, *depth, z_top - FRAME_M, z_top, FRAME_FINISH)
    low_rail = o.kind in (OpeningKind.WINDOW, OpeningKind.SLIDING_WINDOW) and o.sill_height > 0.05
    if low_rail:
        box(s0, s1, *depth, z_sill, z_sill + FRAME_M, FRAME_FINISH)
    i0, i1 = s0 + FRAME_M, s1 - FRAME_M
    zb = z_sill + (FRAME_M if low_rail else 0.0)
    zt = z_top - FRAME_M
    kind = o.kind
    if kind in (OpeningKind.DOOR, OpeningKind.POCKET_DOOR):
        _leaf(box, i0, i1, 0.0, zb, zt)
    elif kind is OpeningKind.DOUBLE_DOOR:
        # Battement : les deux vantaux se recouvrent, décalés dans l'épaisseur ; un jour entre
        # eux laissait voir le ciel derrière une porte extérieure (liseré bleu).
        cut = _pair_cut(o, i0, i1)
        _leaf(box, i0, cut + REBATE_M, -REBATE_SHIFT_M, zb, zt)
        _leaf(box, cut - REBATE_M, i1, REBATE_SHIFT_M, zb, zt)
    elif kind is OpeningKind.FOLDING_DOOR:
        step = (i1 - i0) / 4.0
        for n in range(4):
            _leaf(
                box,
                max(i0, i0 + n * step - REBATE_M),
                min(i1, i0 + (n + 1) * step + REBATE_M),
                REBATE_SHIFT_M if n % 2 else -REBATE_SHIFT_M,
                zb,
                zt,
            )
    elif kind is OpeningKind.FRENCH_WINDOW:
        if is_pair(o):
            cut = _pair_cut(o, i0, i1)
            _glazed(box, i0, cut - 0.002, 0.0, zb, zt)
            _glazed(box, cut + 0.002, i1, 0.0, zb, zt)
        else:
            _glazed(box, i0, i1, 0.0, zb, zt)
    elif kind is OpeningKind.SLIDING_WINDOW:
        cover = WINDOW_OVERLAP * (i1 - i0)
        _glazed(box, i0, i0 + cover, half / 4.0, zb, zt)
        _glazed(box, i1 - cover, i1, -half / 4.0, zb, zt)
    elif kind is OpeningKind.SLIDING_DOOR:
        _sliding(o, box, half, z_sill, z_top)
    elif kind is OpeningKind.WINDOW:
        box(i0, i1, -GLASS_M / 2, GLASS_M / 2, zb, zt, GLASS_FINISH)
        if i1 - i0 > 0.9:
            mid = (i0 + i1) / 2.0
            box(mid - FRAME_M / 2, mid + FRAME_M / 2, -0.03, 0.03, zb, zt, FRAME_FINISH)


CORRIDOR_DEPTH_M = 1.2
CORRIDOR_SKIN_M = 0.05


def _corridor(o: Opening, box: Box, half: float, z_sill: float, side: float) -> None:
    """Couloir neutre derrière un passage (V1) : deux joues, un fond et un plafond enduits,
    du côté opposé à la pièce, pour qu'un rendu ne voie pas le ciel par la baie."""
    s0, s1 = o.offset - 0.3, o.offset + o.width + 0.3
    z_top = z_sill + o.height + 0.3
    near, far = half, half + CORRIDOR_DEPTH_M

    def band(k0: float, k1: float) -> tuple[float, float]:
        a, b = side * k0, side * k1
        return (min(a, b), max(a, b))

    floor = floor_finish(THRESHOLD_FINISH)
    box(o.offset, o.offset + o.width, -half, half, z_sill - THRESHOLD_M, z_sill, floor)
    box(s0, s1, *band(near, far), z_sill - THRESHOLD_M, z_sill, floor)
    box(s0, s1, *band(far, far + CORRIDOR_SKIN_M), z_sill, z_top, FRAME_FINISH)
    box(s0 - CORRIDOR_SKIN_M, s0, *band(near, far), z_sill, z_top, FRAME_FINISH)
    box(s1, s1 + CORRIDOR_SKIN_M, *band(near, far), z_sill, z_top, FRAME_FINISH)
    box(s0, s1, *band(near, far), z_top, z_top + CORRIDOR_SKIN_M, FRAME_FINISH)


def _pair_cut(o: Opening, i0: float, i1: float) -> float:
    """Abscisse de la jonction des deux vantaux : le principal (côté gonds) fait `split`."""
    main = (i1 - i0) * o.split
    left = o.swing is None or o.swing.hinge == "left"
    return i0 + main if left else i1 - main


def _leaf(box: Box, s0: float, s1: float, k: float, z0: float, z1: float) -> None:
    box(s0, s1, k - LEAF_M / 2, k + LEAF_M / 2, z0, z1, door_leaf_finish(LEAF_FINISH))


def _glazed(box: Box, s0: float, s1: float, k: float, z0: float, z1: float) -> None:
    """Vantail vitré : montants, traverses et vitre."""
    t0, t1 = k - LEAF_M / 2, k + LEAF_M / 2
    box(s0, s0 + STILE_M, t0, t1, z0, z1, FRAME_FINISH)
    box(s1 - STILE_M, s1, t0, t1, z0, z1, FRAME_FINISH)
    box(s0 + STILE_M, s1 - STILE_M, t0, t1, z0, z0 + STILE_M * 1.5, FRAME_FINISH)
    box(s0 + STILE_M, s1 - STILE_M, t0, t1, z1 - STILE_M, z1, FRAME_FINISH)
    box(
        s0 + STILE_M,
        s1 - STILE_M,
        k - GLASS_M / 2,
        k + GLASS_M / 2,
        z0 + STILE_M * 1.5,
        z1 - STILE_M,
        GLASS_FINISH,
    )


def _sliding(o: Opening, box: Box, half: float, z_sill: float, z_top: float) -> None:
    """Coulissante en applique : vantail contre la face, côté rail ; rail au-dessus de la baie
    et du tronçon où le vantail s'efface."""
    side = swing_side(o)
    k0, k1 = sorted((side * (half + 0.01), side * (half + 0.01 + LEAF_M)))
    box(
        o.offset - 0.03,
        o.offset + o.width + 0.03,
        k0,
        k1,
        z_sill + 0.01,
        z_top + 0.02,
        door_leaf_finish(LEAF_FINISH),
    )
    span = stack_span(o)
    lo = min(o.offset, span[0]) if span else o.offset
    hi = max(o.offset + o.width, span[1]) if span else o.offset + o.width
    r0, r1 = sorted((side * (half + 0.005), side * (half + RAIL_M)))
    box(lo - 0.02, hi + 0.02, r0, r1, z_top + 0.03, z_top + 0.03 + RAIL_M, RAIL_FINISH)
