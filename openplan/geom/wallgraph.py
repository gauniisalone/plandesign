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
_MITER_LIMIT = 4.0
"""Pointe d'un angle très aigu : au-delà de 4 épaisseurs du sommet, les faces ne sont plus
prolongées (bout droit), comme la limite de pointe des logiciels de dessin."""
_PARALLEL_SIN = math.sin(math.radians(0.5))
"""Deux faces presque parallèles (mur prolongé tout droit) ne se croisent pas : bout droit."""

XY = tuple[float, float]


class GeometryError(ValueError):
    """Référence manquante ou géométrie dégénérée."""


@dataclass(frozen=True)
class _End:
    """Un bout de mur vu depuis un sommet : direction sortante `d`, normale gauche `n` et
    décalage de ses deux faces le long de `n` (gauche > droite)."""

    wall_id: UUID
    d: XY
    n: XY
    left: float
    right: float

    @property
    def angle(self) -> float:
        return math.atan2(self.d[1], self.d[0])


def _line_cross(p: XY, d: XY, q: XY, e: XY) -> XY | None:
    """Croisement des droites p + t·d et q + s·e ; None si (presque) parallèles."""
    det = d[0] * e[1] - d[1] * e[0]
    if abs(det) < _PARALLEL_SIN:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / det
    return (p[0] + d[0] * t, p[1] + d[1] * t)


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

    # ------------------------------------------------------------------ jonctions (KAN-29)
    def _ends(self) -> dict[UUID, list[_End]]:
        """Bouts de mur par sommet, triés dans le sens antihoraire."""
        out: dict[UUID, list[_End]] = {}
        for wid, wall in self.walls.items():
            try:
                ux, uy = self.wall_direction(wid)
            except GeometryError:
                continue
            half = wall.thickness / 2.0
            shift = {"center": 0.0, "left": half, "right": -half}[wall.offset]
            for vid, sign in ((wall.start_vertex, 1.0), (wall.end_vertex, -1.0)):
                faces = (sign * (shift + half), sign * (shift - half))
                d = (sign * ux, sign * uy)
                out.setdefault(vid, []).append(_End(wid, d, (-d[1], d[0]), max(faces), min(faces)))
        for ends in out.values():
            ends.sort(key=lambda e: e.angle)
        return out

    def _caps(self) -> dict[tuple[UUID, UUID], tuple[XY, XY, XY | None]]:
        """Coin gauche, coin droit (vus depuis le sommet) et sommet central de chaque bout.

        Entre deux bouts voisins dans l'ordre angulaire, la face gauche du premier et la face
        droite du second se prolongent jusqu'à leur croisement : les deux murs partagent ce
        point, donc leurs emprises se touchent sans trou ni chevauchement, quel que soit
        l'angle. Un bout seul, deux murs alignés ou une pointe trop longue : bout droit.
        Trois murs ou plus : le sommet lui-même ferme le moyeu.
        """
        caps: dict[tuple[UUID, UUID], tuple[XY, XY, XY | None]] = {}
        for vid, ends in self._ends().items():
            p = self.vertex(vid).position

            def foot(e: _End, offset: float, p: Point2 = p) -> XY:
                return (p.x + e.n[0] * offset, p.y + e.n[1] * offset)

            left = {e.wall_id: foot(e, e.left) for e in ends}
            right = {e.wall_id: foot(e, e.right) for e in ends}
            k = len(ends)
            if k >= 2:
                for i, e in enumerate(ends):
                    f = ends[(i + 1) % k]
                    if f.wall_id == e.wall_id:
                        continue
                    x = _line_cross(foot(e, e.left), e.d, foot(f, f.right), f.d)
                    reach = _MITER_LIMIT * max(e.left - e.right, f.left - f.right)
                    if x is None or math.hypot(x[0] - p.x, x[1] - p.y) > reach:
                        continue
                    left[e.wall_id] = x
                    right[f.wall_id] = x
            hub = (p.x, p.y) if k >= 3 else None
            for e in ends:
                caps[(e.wall_id, vid)] = (left[e.wall_id], right[e.wall_id], hub)
        return caps

    def wall_footprints(self) -> dict[UUID, Polygon]:
        """Emprises raccordées des murs (KAN-29) : les faces de deux murs qui se rejoignent se
        prolongent jusqu'à leur croisement (onglet), à tout angle. Les murs d'un même sommet
        se partagent leurs coins : l'union est fermée, sans trou ni recouvrement.

        `wall_polygon` (bouts droits) reste la référence des règles et des surfaces de pièce.
        """
        caps = self._caps()
        out: dict[UUID, Polygon] = {}
        for wid, wall in self.walls.items():
            start = caps.get((wid, wall.start_vertex))
            end = caps.get((wid, wall.end_vertex))
            if start is None or end is None:
                continue
            ring: list[XY] = [start[1], end[0]]
            if end[2] is not None:
                ring.append(end[2])
            ring += [end[1], start[0]]
            if start[2] is not None:
                ring.append(start[2])
            polygon = Polygon(ring)
            if not polygon.is_valid:
                polygon = largest_polygon(polygon.buffer(0))
            flat = self.wall_polygon(wid)
            if polygon.is_empty or polygon.area < 0.5 * flat.area:
                polygon = flat
            out[wid] = polygon
        return out

    def wall_footprint(self, wall_id: UUID) -> Polygon:
        """Emprise raccordée d'un mur (voir `wall_footprints`)."""
        self.wall(wall_id)
        return self.wall_footprints().get(wall_id) or self.wall_polygon(wall_id)

    def corner_reach(self, wall_id: UUID, at_start: bool) -> float:
        """Longueur, le long de l'axe depuis le bout, que l'angle prend sur les faces du mur :
        au-delà, les deux faces sont pleines. 0 pour un bout libre ; la demi-épaisseur du mur
        voisin à angle droit ; davantage pour un angle aigu, moins pour un pan coupé."""
        wall = self.wall(wall_id)
        vid = wall.start_vertex if at_start else wall.end_vertex
        cap = self._caps().get((wall_id, vid))
        if cap is None:
            return 0.0
        p = self.vertex(vid).position
        a, b = self.wall_axis(wall_id)
        inward = (b.x - a.x, b.y - a.y) if at_start else (a.x - b.x, a.y - b.y)
        length = math.hypot(*inward)
        if length < _EPS:
            return 0.0
        ux, uy = inward[0] / length, inward[1] / length
        reach = max((q[0] - p.x) * ux + (q[1] - p.y) * uy for q in cap[:2])
        return max(0.0, reach)

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
