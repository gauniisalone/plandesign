"""Graphe de murs d'un niveau : longueurs, emprises, pièces, boucles, édition paramétrique.

`WallGraph` est une vue calculée et immuable d'un `Level`. Les opérations d'édition
(`move_vertex`, `set_wall_length`) renvoient un **nouveau Level** ; la couche `scene`
en déduit le patch d'historique. Ce module ne connaît ni l'historique ni les acteurs :
il ne change jamais `source` ni `confidence`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from openplan.geom.polygon import largest_polygon, signed_area, to_polygon
from openplan.schemas.common import Point2
from openplan.schemas.elements import Room, Vertex, Wall
from openplan.schemas.scene import Level

Anchor = Literal["start", "end", "center"]
_EPS = 1e-9


class GeometryError(ValueError):
    """Référence manquante ou géométrie dégénérée."""


@dataclass(frozen=True)
class WallGraph:
    level: Level
    walls: dict[UUID, Wall] = field(init=False)

    def __post_init__(self) -> None:
        walls = {eid: e for eid, e in self.level.elements.items() if isinstance(e, Wall)}
        object.__setattr__(self, "walls", walls)

    @classmethod
    def from_level(cls, level: Level) -> WallGraph:
        return cls(level)

    # ------------------------------------------------------------------ accès
    def vertex(self, vertex_id: UUID) -> Vertex:
        try:
            return self.level.vertices[vertex_id]
        except KeyError as exc:
            msg = f"sommet inconnu : {vertex_id}"
            raise GeometryError(msg) from exc

    def wall(self, wall_id: UUID) -> Wall:
        try:
            return self.walls[wall_id]
        except KeyError as exc:
            msg = f"mur inconnu : {wall_id}"
            raise GeometryError(msg) from exc

    def room(self, room_id: UUID) -> Room:
        element = self.level.elements.get(room_id)
        if not isinstance(element, Room):
            msg = f"pièce inconnue : {room_id}"
            raise GeometryError(msg)
        return element

    def wall_axis(self, wall_id: UUID) -> tuple[Point2, Point2]:
        wall = self.wall(wall_id)
        return self.vertex(wall.start_vertex).position, self.vertex(wall.end_vertex).position

    def wall_length(self, wall_id: UUID) -> float:
        a, b = self.wall_axis(wall_id)
        return math.hypot(b.x - a.x, b.y - a.y)

    def wall_direction(self, wall_id: UUID) -> tuple[float, float]:
        """Vecteur unitaire start→end. Lève GeometryError si le mur est de longueur nulle."""
        a, b = self.wall_axis(wall_id)
        length = math.hypot(b.x - a.x, b.y - a.y)
        if length < _EPS:
            msg = f"mur de longueur nulle : {wall_id}"
            raise GeometryError(msg)
        return (b.x - a.x) / length, (b.y - a.y) / length

    def walls_at(self, vertex_id: UUID) -> list[UUID]:
        return [wid for wid, w in self.walls.items() if vertex_id in (w.start_vertex, w.end_vertex)]

    # ------------------------------------------------------------------ emprises
    def wall_polygon(self, wall_id: UUID) -> Polygon:
        """Emprise du mur avec son épaisseur, bouts droits, selon `offset`."""
        wall = self.wall(wall_id)
        a, b = self.wall_axis(wall_id)
        ux, uy = self.wall_direction(wall_id)
        nx, ny = -uy, ux  # normale gauche
        half = wall.thickness / 2.0
        shift = {"center": 0.0, "left": half, "right": -half}[wall.offset]
        axis = LineString(
            [(a.x + nx * shift, a.y + ny * shift), (b.x + nx * shift, b.y + ny * shift)]
        )
        return axis.buffer(half, cap_style="flat")

    def walls_union(self, wall_ids: Sequence[UUID] | None = None) -> Polygon:
        ids = list(self.walls) if wall_ids is None else list(wall_ids)
        return unary_union([self.wall_polygon(w) for w in ids])

    # ------------------------------------------------------------------ pièces
    def loop_vertices(self, wall_ids: Sequence[UUID]) -> list[UUID]:
        """Sommets ordonnés d'une boucle fermée de murs. Lève GeometryError sinon."""
        if len(wall_ids) < 3:
            msg = "une boucle demande au moins trois murs"
            raise GeometryError(msg)
        walls = [self.wall(w) for w in wall_ids]
        first, second = walls[0], walls[1]
        shared = {first.start_vertex, first.end_vertex} & {second.start_vertex, second.end_vertex}
        if not shared:
            msg = "les deux premiers murs ne partagent aucun sommet"
            raise GeometryError(msg)
        junction = next(iter(shared))
        start = first.end_vertex if junction == first.start_vertex else first.start_vertex
        ordered: list[UUID] = []
        current = start
        for wall in walls:
            if current not in (wall.start_vertex, wall.end_vertex):
                msg = "la boucle de murs ne se referme pas : un mur n'est pas connecté au précédent"
                raise GeometryError(msg)
            ordered.append(current)
            current = wall.end_vertex if current == wall.start_vertex else wall.start_vertex
        if current != start:
            msg = "la boucle de murs ne se referme pas"
            raise GeometryError(msg)
        return ordered

    def room_axis_polygon(self, room_id: UUID) -> Polygon:
        """Polygone passant par les axes des murs, orienté antihoraire."""
        vertex_ids = self.loop_vertices(self.room(room_id).boundary_walls)
        return to_polygon([self.vertex(v).position for v in vertex_ids])

    def room_polygon(self, room_id: UUID) -> Polygon:
        """Polygone intérieur : axe moins l'emprise des murs du contour."""
        room = self.room(room_id)
        axis = self.room_axis_polygon(room_id)
        return largest_polygon(axis.difference(self.walls_union(room.boundary_walls)))

    def room_area(self, room_id: UUID) -> float:
        """Surface intérieure en m²."""
        return float(self.room_polygon(room_id).area)

    def closed_loops(self) -> list[list[UUID]]:
        """Boucles fermées du graphe (faces intérieures), chacune comme liste de murs.

        Parcours de faces planaires : à chaque sommet, on tourne au plus à gauche.
        La face extérieure (aire signée négative) est ignorée. Un mur en impasse à
        l'intérieur d'une face apparaît une seule fois dans la liste.
        """
        outgoing: dict[UUID, list[tuple[float, UUID, UUID]]] = {}
        for wid, wall in self.walls.items():
            for a, b in (
                (wall.start_vertex, wall.end_vertex),
                (wall.end_vertex, wall.start_vertex),
            ):
                pa, pb = self.vertex(a).position, self.vertex(b).position
                outgoing.setdefault(a, []).append((math.atan2(pb.y - pa.y, pb.x - pa.x), wid, b))
        for edges in outgoing.values():
            edges.sort()

        visited: set[tuple[UUID, UUID, UUID]] = set()
        loops: list[list[UUID]] = []
        for a, edges in outgoing.items():
            for _, wid, b in edges:
                start = (wid, a, b)
                if start in visited:
                    continue
                face_walls: list[UUID] = []
                face_points: list[Point2] = []
                current = start
                while current not in visited:
                    visited.add(current)
                    cw, ca, cb = current
                    face_walls.append(cw)
                    face_points.append(self.vertex(ca).position)
                    at_b = outgoing[cb]
                    idx = next(i for i, e in enumerate(at_b) if e[1] == cw and e[2] == ca)
                    _, nw, nb = at_b[(idx - 1) % len(at_b)]
                    current = (nw, cb, nb)
                unique = list(dict.fromkeys(face_walls))
                if signed_area(face_points) > _EPS and len(unique) >= 3:
                    loops.append(unique)
        return loops

    # ------------------------------------------------------------------ édition
    def with_vertices(self, moves: dict[UUID, Point2]) -> Level:
        """Nouveau Level où les sommets indiqués ont une autre position."""
        vertices = dict(self.level.vertices)
        for vertex_id, position in moves.items():
            vertices[vertex_id] = self.vertex(vertex_id).model_copy(update={"position": position})
        return self.level.model_copy(update={"vertices": vertices})

    def move_vertex(self, vertex_id: UUID, to: Point2) -> Level:
        """Déplace un sommet ; tous les murs connectés suivent."""
        return self.with_vertices({vertex_id: to})

    def set_wall_length(self, wall_id: UUID, length: float, anchor: Anchor = "start") -> Level:
        """Impose la longueur d'un mur en déplaçant son extrémité libre le long de l'axe.

        anchor="start" déplace end_vertex, "end" déplace start_vertex, "center" les deux.
        """
        if length <= 0.0:
            msg = "la longueur doit être strictement positive"
            raise GeometryError(msg)
        wall = self.wall(wall_id)
        a, b = self.wall_axis(wall_id)
        ux, uy = self.wall_direction(wall_id)
        if anchor == "start":
            return self.with_vertices(
                {wall.end_vertex: Point2(x=a.x + ux * length, y=a.y + uy * length)}
            )
        if anchor == "end":
            return self.with_vertices(
                {wall.start_vertex: Point2(x=b.x - ux * length, y=b.y - uy * length)}
            )
        mx, my = (a.x + b.x) / 2.0, (a.y + b.y) / 2.0
        half = length / 2.0
        return self.with_vertices(
            {
                wall.start_vertex: Point2(x=mx - ux * half, y=my - uy * half),
                wall.end_vertex: Point2(x=mx + ux * half, y=my + uy * half),
            }
        )
