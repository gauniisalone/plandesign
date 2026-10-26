"""KAN-94 : faîtage en plan, fermes intermédiaires et poteaux (géométrie pure).

Le faîtage est la ligne où deux pentes opposées se rejoignent (`roof.ridge_pair`), borné par
l'étendue de leurs égouts : ses deux bouts sont les pignons. Une ferme intermédiaire est un
point de cet axe ; ses deux poteaux et sa poutre sont calculés ici, à chaque lecture, sur le
faîtage du moment (une retouche du toit la reprojette). Seule source pour les commandes
(`scene.ops`), les règles (`validation.layout`), le bake et les exports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from uuid import UUID

from shapely.geometry import Polygon
from shapely.ops import unary_union

from openplan.geom.roof import derived_roofs, height_under_slope, ridge_pair
from openplan.geom.velux import RAFTER, timber_settings
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.derived import DerivedRoof
from openplan.schemas.elements import Post, RoofPlane, Room, Truss, Wall
from openplan.schemas.numbers import metres
from openplan.schemas.scene import Level

XY = tuple[float, float]

MIN_BEAM_HEIGHT_M = 1.00
"""Sous 1 m de passage, la poutre d'une ferme n'a plus de sens : l'écartement est refusé."""

GABLE_SIN = 0.26
"""Un pignon est en travers du faîtage, à 15° près (sinus de l'angle avec la normale)."""

NO_RIDGE = (
    "une ferme intermédiaire se pose sous un faîtage : ce niveau n'a pas deux pentes qui se "
    "rejoignent"
)


@dataclass(frozen=True)
class Ridge:
    """Axe du faîtage en plan : `origin` + t · `direction`, de `t0` à `t1` (les pignons)."""

    origin: XY
    direction: XY
    t0: float
    t1: float
    roofs: tuple[tuple[DerivedRoof, RoofPlane], tuple[DerivedRoof, RoofPlane]]
    top: float
    """Hauteur du faîtage au-dessus du sol du niveau."""

    @property
    def length(self) -> float:
        return self.t1 - self.t0

    @property
    def normal(self) -> XY:
        return (-self.direction[1], self.direction[0])

    def at(self, t: float) -> XY:
        return (self.origin[0] + self.direction[0] * t, self.origin[1] + self.direction[1] * t)

    def along(self, point: XY) -> float:
        return (point[0] - self.origin[0]) * self.direction[0] + (
            point[1] - self.origin[1]
        ) * self.direction[1]

    def under_timber(self, point: XY) -> float:
        """Hauteur libre à l'aplomb de `point` : sous la pente la plus basse, moins la hauteur
        des chevrons quand la charpente est apparente."""
        out = self.top
        for derived, roof in self.roofs:
            h = height_under_slope(
                point,
                (derived.eave[0].x, derived.eave[0].y),
                (derived.eave[1].x, derived.eave[1].y),
                (derived.inward.x, derived.inward.y),
                derived.eave_height,
                derived.pitch,
                self.top,
            )
            if timber_settings(roof)[0]:
                h -= RAFTER[1] / max(math.cos(derived.pitch), 0.2)
            out = min(out, h)
        return out


def ridge_span(level: Level, graph: WallGraph) -> Ridge | None:
    """Le faîtage du niveau, borné par l'étendue des égouts de ses deux pentes ; None sans
    deux pentes opposées."""
    derived = derived_roofs(level, graph)
    pair = ridge_pair(list(derived.values()))
    if pair is None:
        return None
    a, b = pair
    ids = {id(d): rid for rid, d in derived.items()}
    roof_a, roof_b = level.elements[ids[id(a)]], level.elements[ids[id(b)]]
    if not isinstance(roof_a, RoofPlane) or not isinstance(roof_b, RoofPlane):
        return None
    dx, dy = a.eave[1].x - a.eave[0].x, a.eave[1].y - a.eave[0].y
    n = math.hypot(dx, dy)
    direction = (dx / n, dy / n)

    def top(r: DerivedRoof) -> XY:
        mx = (r.eave[0].x + r.eave[1].x) / 2.0
        my = (r.eave[0].y + r.eave[1].y) / 2.0
        return mx + r.inward.x * r.run_m, my + r.inward.y * r.run_m

    (ax, ay), (bx, by) = top(a), top(b)
    origin = ((ax + bx) / 2.0, (ay + by) / 2.0)
    ts = [
        (p.x - origin[0]) * direction[0] + (p.y - origin[1]) * direction[1]
        for r in (a, b)
        for p in r.eave
    ]
    return Ridge(
        origin=origin,
        direction=direction,
        t0=min(ts),
        t1=max(ts),
        roofs=((a, roof_a), (b, roof_b)),
        top=level.height,
    )


def gable_face(level: Level, graph: WallGraph, ridge: Ridge, wall_id: UUID) -> tuple[float, int]:
    """Position, le long du faîtage, de la face intérieure d'un pignon, et le sens (+1 / -1)
    qui part de lui vers l'autre pignon. ValueError (texte dit) si le mur n'en est pas un."""
    wall = level.elements.get(wall_id)
    if not isinstance(wall, Wall):
        msg = f"mur inconnu : {wall_id}"
        raise ValueError(msg)
    try:
        p0, p1 = graph.wall_axis(wall_id)
    except (GeometryError, KeyError) as exc:
        raise ValueError(str(exc)) from exc
    x0, y0, x1, y1 = p0.x, p0.y, p1.x, p1.y
    length = math.hypot(x1 - x0, y1 - y0)
    name = wall.name or "ce mur"
    if length < 1e-9 or abs((x1 - x0) * ridge.direction[0] + (y1 - y0) * ridge.direction[1]) > (
        GABLE_SIN * length
    ):
        msg = (
            f"« {name} » n'est pas un pignon : la distance se mesure depuis un mur en travers "
            "du faîtage"
        )
        raise ValueError(msg)
    t = ridge.along(((x0 + x1) / 2.0, (y0 + y1) / 2.0))
    sense = 1 if t <= (ridge.t0 + ridge.t1) / 2.0 else -1
    return t + sense * wall.thickness / 2.0, sense


@dataclass(frozen=True)
class TrussParts:
    """Une ferme posée : son point sur le faîtage du moment, ses deux poteaux (centres) et sa
    poutre (dessous à `beam_height`, éventuellement abaissée sous la pente)."""

    centre: XY
    along: float
    """Distance au premier pignon (t0), en mètres."""
    posts: tuple[XY, XY]
    beam_height: float
    lowered: bool
    """La poutre demandée passait au-dessus de la pente : elle a été abaissée."""


def truss_parts(level: Level, graph: WallGraph, truss: Truss) -> TrussParts | str:
    """Poteaux et poutre de la ferme, reprojetée sur le faîtage du moment ; sinon la raison,
    dite en français (plus de faîtage, hors du faîtage, écartement trop grand)."""
    ridge = ridge_span(level, graph)
    if ridge is None:
        return "plus de faîtage sur ce niveau : la ferme intermédiaire n'est plus dessinée"
    return place_truss(ridge, truss)


def place_truss(ridge: Ridge, truss: Truss) -> TrussParts | str:
    t = ridge.along((truss.position.x, truss.position.y))
    half = max(truss.beam_width, truss.post_depth) / 2.0
    if not ridge.t0 + half - 1e-6 <= t <= ridge.t1 - half + 1e-6:
        return (
            f"la ferme intermédiaire est hors du faîtage (à {metres(t - ridge.t0)} du premier "
            f"pignon, faîtage de {metres(ridge.length)})"
        )
    centre = ridge.at(t)
    nx, ny = ridge.normal
    s = truss.spacing / 2.0
    posts = ((centre[0] + nx * s, centre[1] + ny * s), (centre[0] - nx * s, centre[1] - ny * s))
    reach = s + truss.post_width / 2.0
    edges = [(centre[0] + nx * k * reach, centre[1] + ny * k * reach) for k in (1, -1)]
    highest = min(ridge.under_timber(p) for p in edges) - truss.beam_depth
    if highest < MIN_BEAM_HEIGHT_M - 1e-6:
        return (
            f"écartement de {metres(truss.spacing)} trop grand : la poutre passerait à moins de "
            f"{metres(MIN_BEAM_HEIGHT_M)} du sol sous la pente (au plus "
            f"{metres(max_spacing(ridge, truss, t), 2)})"
        )
    lowered = truss.beam_height > highest + 1e-6
    return TrussParts(
        centre=centre,
        along=t - ridge.t0,
        posts=posts,
        beam_height=round(highest, 3) if lowered else truss.beam_height,
        lowered=lowered,
    )


def max_spacing(ridge: Ridge, truss: Truss, t: float) -> float:
    """Le plus grand écartement qui laisse la poutre à MIN_BEAM_HEIGHT_M (recherche par pas de
    1 cm : la pente peut être dissymétrique)."""
    centre = ridge.at(t)
    nx, ny = ridge.normal
    best = 0.0
    spacing = 0.01
    while spacing <= 6.0:
        reach = spacing / 2.0 + truss.post_width / 2.0
        edges = [(centre[0] + nx * k * reach, centre[1] + ny * k * reach) for k in (1, -1)]
        if min(ridge.under_timber(p) for p in edges) - truss.beam_depth < MIN_BEAM_HEIGHT_M:
            break
        best = spacing
        spacing += 0.01
    return best


def footprint(centre: XY, across: float, along: float, direction: XY) -> Polygon:
    """Rectangle d'emprise : `along` dans le sens de `direction`, `across` en travers."""
    dx, dy = direction
    nx, ny = -dy, dx
    a, c = along / 2.0, across / 2.0
    return Polygon(
        [
            (centre[0] + sx * a * dx + sy * c * nx, centre[1] + sx * a * dy + sy * c * ny)
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]
    )


def post_footprint(post: Post) -> Polygon:
    """Emprise d'un poteau : `width` le long de son orientation `yaw`, `depth` en travers."""
    return footprint(
        (post.position.x, post.position.y),
        post.depth,
        post.width,
        (math.cos(post.yaw), math.sin(post.yaw)),
    )


def truss_footprints(ridge: Ridge, truss: Truss, parts: TrussParts) -> list[Polygon]:
    """Emprises des deux poteaux d'une ferme : `post_width` en travers du faîtage (le long de
    la poutre), `post_depth` le long du faîtage."""
    return [footprint(p, truss.post_width, truss.post_depth, ridge.direction) for p in parts.posts]


def post_shapes(level: Level, graph: WallGraph) -> list[Polygon]:
    """Emprises au sol de tous les poteaux du niveau : seuls, et ceux des fermes que le
    faîtage porte (pour l'aménagement : aucun meuble dessus)."""
    out: list[Polygon] = []
    ridge = ridge_span(level, graph)
    for element in level.elements.values():
        if isinstance(element, Post) and element.visible:
            out.append(post_footprint(element))
        elif isinstance(element, Truss) and element.visible and ridge is not None:
            parts = place_truss(ridge, element)
            if not isinstance(parts, str):
                out += truss_footprints(ridge, element, parts)
    return out


def truss_shape(ridge: Ridge, truss: Truss, parts: TrussParts) -> Polygon:
    """Emprise d'ensemble d'une ferme (ses deux poteaux et la poutre entre eux)."""
    hull = unary_union(truss_footprints(ridge, truss, parts)).convex_hull
    return hull if isinstance(hull, Polygon) else Polygon()


def room_containing(level: Level, graph: WallGraph, shape: Polygon) -> UUID | None:
    """La pièce qui contient toute l'emprise (à 1 mm près), sinon None."""
    for eid, element in level.elements.items():
        if not isinstance(element, Room):
            continue
        try:
            inside = graph.room_polygon(eid).buffer(1e-3).covers(shape)
        except (GeometryError, KeyError, ValueError):
            continue
        if inside:
            return eid
    return None
