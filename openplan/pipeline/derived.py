"""Calcul des grandeurs dérivées d'un niveau pour l'interface (longueurs, aires, polygones)."""

from __future__ import annotations

import math
from uuid import UUID

from shapely.geometry import Polygon
from shapely.ops import unary_union

from openplan.geom.polygon import to_points
from openplan.geom.roof import derived_roofs
from openplan.geom.stairs import arrow_head, cut_line, headroom, railing_lines, stair_layout
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.common import Point2
from openplan.schemas.derived import (
    Bounds,
    DerivedLevel,
    DerivedRoof,
    DerivedRoom,
    DerivedStairs,
    DerivedWall,
)
from openplan.schemas.elements import Room, Stairs
from openplan.schemas.scene import Level
from openplan.validation.layout import clearance_at


def compute_derived(level: Level) -> DerivedLevel:
    graph = WallGraph.from_level(level)
    walls: dict[UUID, DerivedWall] = {}
    for wid in graph.walls:
        try:
            ux, uy = graph.wall_direction(wid)
        except GeometryError:
            continue  # mur dégénéré : rien à dessiner, l'invariant le signalera
        a, b = graph.wall_axis(wid)
        walls[wid] = DerivedWall(
            length_m=graph.wall_length(wid),
            polygon=to_points(graph.wall_polygon(wid)),
            midpoint=Point2(x=(a.x + b.x) / 2.0, y=(a.y + b.y) / 2.0),
            normal=Point2(x=-uy, y=ux),
        )
    rooms: dict[UUID, DerivedRoom] = {}
    for rid, element in level.elements.items():
        if not isinstance(element, Room):
            continue
        try:
            polygon = graph.room_polygon(rid)
        except GeometryError:
            continue
        if polygon.is_empty:
            continue
        centroid = polygon.centroid
        rooms[rid] = DerivedRoom(
            area_m2=float(polygon.area),
            perimeter_m=float(polygon.length),
            polygon=to_points(polygon),
            centroid=Point2(x=float(centroid.x), y=float(centroid.y)),
        )
    bounds = None
    if level.vertices:
        xs = [v.position.x for v in level.vertices.values()]
        ys = [v.position.y for v in level.vertices.values()]
        bounds = Bounds(min=Point2(x=min(xs), y=min(ys)), max=Point2(x=max(xs), y=max(ys)))
    loops = graph.closed_loops() if len(graph.walls) >= 3 else []
    roofs = derived_roofs(level, graph)
    if roofs:
        rooms = {rid: _with_usable_area(room, roofs) for rid, room in rooms.items()}
    return DerivedLevel(
        level_id=level.id,
        walls=walls,
        rooms=rooms,
        roofs=roofs,
        stairs=derived_stairs(level, graph, roofs),
        loops=loops,
        bounds=bounds,
    )


def _pts(points: list[tuple[float, float]]) -> list[Point2]:
    return [Point2(x=float(x), y=float(y)) for x, y in points]


def derived_stairs(
    level: Level, graph: WallGraph, roofs: dict[UUID, DerivedRoof]
) -> dict[UUID, DerivedStairs]:
    """Symbole en plan et grandeurs de chaque escalier du niveau (S1)."""
    walls = graph.walls_union() if graph.walls else None
    slopes = list(roofs.values())

    def ceiling(point: tuple[float, float]) -> float:
        return clearance_at(point, slopes, level) if slopes else level.height

    out: dict[UUID, DerivedStairs] = {}
    for sid, e in level.elements.items():
        if not isinstance(e, Stairs) or not e.visible:
            continue
        lay = stair_layout(e)
        out[sid] = DerivedStairs(
            outline=_pts([(x, y) for x, y in lay.opening.exterior.coords[:-1]]),
            treads=[_pts([(x, y) for x, y in t.polygon.exterior.coords[:-1]]) for t in lay.treads],
            walk=_pts(lay.walk),
            arrow=_pts(arrow_head(lay)),
            cut=_pts(cut_line(lay)),
            arrival=_pts([(x, y) for x, y in lay.arrival.coords]),
            railings=[_pts(r) for r in railing_lines(lay, walls)] if e.railing else [],
            risers=lay.steps.risers,
            riser_m=lay.steps.riser_m,
            going_m=lay.steps.going_m,
            blondel_m=lay.steps.value_m,
            headroom_m=headroom(lay, ceiling),
        )
    return out


def low_zone(roof: DerivedRoof) -> Polygon | None:
    """Bande entre l'égout et la ligne de hauteur utile (1,80 m), prolongée d'un mètre de
    chaque côté de l'égout pour couvrir l'épaisseur des murs ; None si tout est utilisable
    (égout ≥ 1,80 m) ; toute la pièce si le faîtage lui-même est trop bas (bande très large)."""
    d = roof.usable_offset_m
    if d is not None and d <= 0.0:
        return None
    reach = d if d is not None else 1e3
    a, b = roof.eave
    ux, uy = b.x - a.x, b.y - a.y
    length = math.hypot(ux, uy)
    if length < 1e-9:
        return None
    ux, uy = ux / length, uy / length
    ext = 1.0
    a0 = (a.x - ux * ext, a.y - uy * ext)
    b0 = (b.x + ux * ext, b.y + uy * ext)
    inward = (roof.inward.x, roof.inward.y)
    # Le mur d'égout a une épaisseur : la bande part un peu derrière la ligne d'égout.
    back = 0.5
    return Polygon(
        [
            (a0[0] - inward[0] * back, a0[1] - inward[1] * back),
            (b0[0] - inward[0] * back, b0[1] - inward[1] * back),
            (b0[0] + inward[0] * reach, b0[1] + inward[1] * reach),
            (a0[0] + inward[0] * reach, a0[1] + inward[1] * reach),
        ]
    )


def _with_usable_area(room: DerivedRoom, roofs: dict[UUID, DerivedRoof]) -> DerivedRoom:
    polygon = Polygon([(p.x, p.y) for p in room.polygon])
    zones = [z for z in (low_zone(r) for r in roofs.values()) if z is not None]
    if not zones:
        return room.model_copy(update={"usable_area_m2": room.area_m2})
    usable = polygon.difference(unary_union(zones))
    return room.model_copy(update={"usable_area_m2": float(max(usable.area, 0.0))})
