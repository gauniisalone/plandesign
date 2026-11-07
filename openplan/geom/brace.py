"""KAN-161 : bois inclinés (jambes de force, arbalétriers), géométrie pure.

Un bois incliné est un `Beam` du format (présent depuis 0.1.0, jamais dessiné jusqu'ici) :
`start` est le pied, `end` la tête, en mètres dans le repère du niveau (z au-dessus de son
sol), `section_width` × `section_height` sa section (20 × 20 cm par défaut). Ce que le format
n'a pas de champ pour dire est gardé dans `metadata["brace"]`, comme la charpente d'une pente
l'est dans `metadata["timber"]` (aucun changement de schéma) :

- `profile` : "square" (défaut) ou "round" (section ronde de diamètre `section_width`) ;
- `head_on` : ce sur quoi la tête est aimantée, "ridge" (faîtière), "slope" (rampant, ou
  plafond hors des combles), "timber" (un autre bois, `head_beam`) ou "height" (donnée) ;
- `foot_on` : "floor" (sol), "wall" (dessus d'un muret, `foot_wall`) ou "height" (donnée).

Le pied est coupé de niveau (horizontal), comme un bois réel posé sur le sol ou le muret. Seule
source pour les commandes (`scene.ops`), les règles (`validation.layout`), le bake, le dérivé
et le prompt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from shapely.geometry import LineString, MultiPoint, Point, Polygon

from openplan.geom.roof import derived_roofs, height_under_slope
from openplan.geom.truss import Ridge, ridge_span
from openplan.geom.velux import RAFTER, RIDGE_BEAM, timber_settings
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.schemas.common import Point2, Point3
from openplan.schemas.elements import Beam, RoofPlane, Room, Wall
from openplan.schemas.numbers import metres
from openplan.schemas.scene import Level

Float = NDArray[np.float64]
XY = tuple[float, float]

HeadOn = Literal["ridge", "slope", "timber", "height"]
FootOn = Literal["floor", "wall", "height"]
Profile = Literal["square", "round"]
HEAD_MODES: tuple[str, ...] = ("auto", "ridge", "slope", "timber", "height")
PROFILES: tuple[str, ...] = ("square", "round")

BRACE_KEY = "brace"
"""Clé de `metadata` où un bois incliné garde son profil et ses aimantations."""
SECTION_M = 0.20
"""Section par défaut (20 × 20 cm), décision du ticket."""
SNAP_M = 0.15
"""La tête s'aimante sur la faîtière ou sur un autre bois à moins de 15 cm en plan."""
MIN_RISE_M = 0.30
"""Un bois « incliné » monte d'au moins 30 cm du pied à la tête."""
ROUND_SIDES = 16
OBSTACLE_HEIGHT_M = 1.80
"""Sous 1,80 m, le bois gêne le passage : sa partie basse est un obstacle pour la circulation."""

FOOT_OUTSIDE = "le pied du bois incliné se pose dans une pièce ou sur un muret"
TOO_FLAT = "la tête du bois incliné doit monter d'au moins 30 cm au-dessus du pied"


@dataclass(frozen=True)
class BraceSpec:
    """Ce que la personne a posé : pied et tête en plan, aimantations, section."""

    foot: XY
    head: XY
    head_on: str = "auto"
    head_height: float | None = None
    foot_height: float | None = None
    width: float = SECTION_M
    depth: float = SECTION_M
    profile: Profile = "square"
    head_beam: UUID | None = None


@dataclass(frozen=True)
class Placed:
    """Le bois posé : pied et tête en 3D (repère du niveau) et ce qui les porte."""

    start: Point3
    end: Point3
    head_on: HeadOn
    foot_on: FootOn
    head_beam: UUID | None = None
    foot_wall: UUID | None = None


def settings(beam: Beam) -> dict[str, object]:
    raw = beam.metadata.get(BRACE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def profile_of(beam: Beam) -> Profile:
    return "round" if settings(beam).get("profile") == "round" else "square"


def is_brace(beam: object) -> bool:
    """Un bois posé par l'outil (il garde ses aimantations) ; un autre `Beam` est dessiné et
    compté de la même façon, mais ne se ré-aimante pas."""
    return isinstance(beam, Beam) and BRACE_KEY in beam.metadata


# --------------------------------------------------------------------- volume
def _frame(start: Float, end: Float) -> tuple[Float, Float, Float]:
    """(axe, côté horizontal, côté « dessous ») unitaires ; un bois vertical prend +x."""
    axis = end - start
    length = float(np.linalg.norm(axis))
    u = axis / length if length > 1e-9 else np.array([0.0, 0.0, 1.0])
    side = np.cross(np.array([0.0, 0.0, 1.0]), u)
    if float(np.linalg.norm(side)) < 1e-6:
        side = np.array([1.0, 0.0, 0.0])
    v = side / np.linalg.norm(side)
    w = np.cross(u, v)
    return u, v, w


def _section(beam: Beam) -> list[tuple[float, float]]:
    """Contour de la section dans le repère (v, w) du bois."""
    a, b = beam.section_width / 2.0, beam.section_height / 2.0
    if profile_of(beam) == "round":
        return [
            (
                a * math.cos(2 * math.pi * k / ROUND_SIDES),
                a * math.sin(2 * math.pi * k / ROUND_SIDES),
            )
            for k in range(ROUND_SIDES)
        ]
    return [(-a, -b), (a, -b), (a, b), (-a, b)]


def _clip(points: Float, z: float, keep: Literal["above", "below"]) -> Float:
    """Sommets du solide convexe `points` coupé par le plan horizontal z (le côté gardé) : les
    sommets de ce côté et les croisements de chaque paire de sommets avec le plan (l'enveloppe
    convexe suffit à en refaire le solide)."""
    sign = 1.0 if keep == "above" else -1.0
    d = (points[:, 2] - z) * sign
    out = [p for p, s in zip(points, d, strict=True) if s >= -1e-9]
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            if d[i] * d[j] < 0.0:
                t = d[i] / (d[i] - d[j])
                out.append(points[i] + (points[j] - points[i]) * t)
    return np.array(out) if out else np.zeros((0, 3))


def brace_points(beam: Beam, z0: float = 0.0) -> Float:
    """Sommets du volume réel du bois (prisme de sa section le long de l'axe, pied coupé de
    niveau à la hauteur du pied), décalés de `z0` (l'altitude du niveau pour le bake)."""
    start = np.array([beam.start.x, beam.start.y, beam.start.z])
    end = np.array([beam.end.x, beam.end.y, beam.end.z])
    if end[2] < start[2]:
        start, end = end, start
    u, v, w = _frame(start, end)
    # Le prisme dépasse le pied d'autant que la section penche, puis est recoupé de niveau.
    reach = max(beam.section_width, beam.section_height)
    ring = [v * a + w * b for a, b in _section(beam)]
    base = start - u * reach
    pts = np.array([c + r for c in (base, end) for r in ring])
    pts = _clip(pts, float(start[2]), "above")
    pts[:, 2] += z0
    return pts


def brace_footprint(beam: Beam) -> Polygon:
    """Projection au sol du volume (trait plein en 2D)."""
    hull = MultiPoint([(float(x), float(y)) for x, y, _ in brace_points(beam)]).convex_hull
    return hull if isinstance(hull, Polygon) else Polygon()


def low_footprint(beam: Beam, height: float = OBSTACLE_HEIGHT_M) -> Polygon | None:
    """Projection au sol de la partie du bois plus basse que `height` : l'obstacle pour la
    circulation et les meubles ; None si le bois passe tout entier au-dessus."""
    low = _clip(brace_points(beam), height, "below")
    if len(low) < 3:
        return None
    hull = MultiPoint([(float(x), float(y)) for x, y, _ in low]).convex_hull
    return hull if isinstance(hull, Polygon) and hull.area > 1e-6 else None


def underside_at(beam: Beam, s: float) -> float:
    """Hauteur du dessous du bois à la fraction `s` (0 au pied, 1 à la tête) de son axe : la
    hauteur de passage sous lui à cet endroit, jamais sous le pied."""
    lo, hi = sorted((beam.start, beam.end), key=lambda p: p.z)
    run = math.hypot(hi.x - lo.x, hi.y - lo.y)
    rise = hi.z - lo.z
    cos = run / max(math.hypot(run, rise), 1e-9)
    half = (beam.section_width if profile_of(beam) == "round" else beam.section_height) / 2.0
    z = lo.z + rise * s - half / max(cos, 0.05)
    return max(lo.z, z)


def passage(beam: Beam) -> float:
    """La cote du ticket : hauteur de passage sous le bois, au milieu de sa portée en plan."""
    return round(underside_at(beam, 0.5), 3)


def low_reach(beam: Beam, height: float = OBSTACLE_HEIGHT_M) -> float:
    """Longueur en plan, depuis le pied, où l'on passe à moins de `height` sous le bois."""
    lo, hi = sorted((beam.start, beam.end), key=lambda p: p.z)
    run = math.hypot(hi.x - lo.x, hi.y - lo.y)
    if underside_at(beam, 1.0) < height:
        return round(run, 3)
    a, b = 0.0, 1.0
    while b - a > 1e-4:
        mid = (a + b) / 2.0
        if underside_at(beam, mid) < height:
            a = mid
        else:
            b = mid
    return round(run * a, 3)


# ----------------------------------------------------------------- aimantation
def _roof_under(level: Level, graph: WallGraph, point: XY) -> float:
    """Dessous de la toiture (sous les chevrons s'ils sont apparents) à l'aplomb du point ; le
    plafond du niveau sans pente."""
    out = level.height
    for rid, roof in derived_roofs(level, graph).items():
        h = height_under_slope(
            point,
            (roof.eave[0].x, roof.eave[0].y),
            (roof.eave[1].x, roof.eave[1].y),
            (roof.inward.x, roof.inward.y),
            roof.eave_height,
            roof.pitch,
            level.height,
        )
        element = level.elements.get(rid)
        if isinstance(element, RoofPlane) and timber_settings(element)[0]:
            h -= RAFTER[1] / max(math.cos(roof.pitch), 0.2)
        out = min(out, h)
    return out


def _on_ridge(ridge: Ridge, point: XY) -> XY:
    t = min(max(ridge.along(point), ridge.t0), ridge.t1)
    return ridge.at(t)


def _ridge_under(level: Level, ridge: Ridge, point: XY) -> float:
    """Dessous de la faîtière au point de l'axe (sous les chevrons sans charpente apparente)."""
    z = ridge.under_timber(point)
    if any(timber_settings(roof)[0] for _, roof in ridge.roofs):
        z -= RIDGE_BEAM[1]
    return min(z, level.height)


def _on_beam(beam: Beam, point: XY) -> tuple[XY, float, float]:
    """(point de l'axe du bois le plus proche en plan, sa hauteur, distance en plan)."""
    line = LineString([(beam.start.x, beam.start.y), (beam.end.x, beam.end.y)])
    if line.length < 1e-9:
        return (
            (beam.start.x, beam.start.y),
            beam.end.z,
            math.hypot(point[0] - beam.start.x, point[1] - beam.start.y),
        )
    s = line.project(Point(point)) / line.length
    x = beam.start.x + (beam.end.x - beam.start.x) * s
    y = beam.start.y + (beam.end.y - beam.start.y) * s
    z = beam.start.z + (beam.end.z - beam.start.z) * s
    return (x, y), z, math.hypot(point[0] - x, point[1] - y)


def head_snap(
    level: Level, graph: WallGraph, spec: BraceSpec, own: UUID | None = None
) -> tuple[XY, float, HeadOn, UUID | None]:
    """La tête aimantée : (point en plan, hauteur, sur quoi, bois porteur). « auto » prend un
    autre bois à moins de 15 cm, sinon la faîtière à moins de 15 cm, sinon le rampant (ou le
    plafond) à l'aplomb ; « height » garde la hauteur donnée."""
    mode = spec.head_on
    point = spec.head
    if mode == "height":
        if spec.head_height is None:
            msg = "hauteur de la tête du bois incliné à donner, en mètres"
            raise ValueError(msg)
        return point, float(spec.head_height), "height", None
    others = [
        e for e in level.elements.values() if isinstance(e, Beam) and e.visible and e.id != own
    ]
    if mode in ("auto", "timber"):
        chosen = [b for b in others if b.id == spec.head_beam] if spec.head_beam else others
        best: tuple[XY, float, float, UUID] | None = None
        for other in chosen:
            xy, z, d = _on_beam(other, point)
            if best is None or d < best[2]:
                best = (xy, z, d, other.id)
        if best is not None and (best[2] <= SNAP_M or (mode == "timber" and spec.head_beam)):
            return best[0], best[1], "timber", best[3]
        if mode == "timber":
            mode = "auto"
    ridge = ridge_span(level, graph)
    if mode in ("auto", "ridge") and ridge is not None:
        on = _on_ridge(ridge, point)
        if mode == "ridge" or math.hypot(on[0] - point[0], on[1] - point[1]) <= SNAP_M:
            return on, _ridge_under(level, ridge, on), "ridge", None
    return point, _roof_under(level, graph, point), "slope", None


def foot_support(
    level: Level, graph: WallGraph, foot: XY, given: float | None
) -> tuple[float, FootOn, UUID | None]:
    """(hauteur du pied, sur quoi, muret) : sur un mur plus bas que le niveau (un muret, une
    jambette), le dessus du mur ; dans une pièce, le sol ; une hauteur donnée est prise telle
    quelle (dans une pièce ou sur un mur). ValueError (texte dit) ailleurs."""
    spot = Point(foot)
    for wid, wall in level.elements.items():
        if not isinstance(wall, Wall) or wid not in graph.walls:
            continue
        try:
            inside = graph.wall_polygon(wid).contains(spot)
        except (GeometryError, KeyError, ValueError):
            continue
        if not inside:
            continue
        top = wall.height if wall.height is not None else level.height
        if top >= level.height - 1e-6:
            raise ValueError(FOOT_OUTSIDE)  # dans un mur plein
        if given is not None:
            return float(given), "height", None
        return float(top), "wall", wid
    for rid, room in level.elements.items():
        if not isinstance(room, Room):
            continue
        try:
            inside = graph.room_polygon(rid).buffer(1e-3).covers(spot)
        except (GeometryError, KeyError, ValueError):
            continue
        if inside:
            return (float(given), "height", None) if given is not None else (0.0, "floor", None)
    raise ValueError(FOOT_OUTSIDE)


def place(level: Level, graph: WallGraph, spec: BraceSpec, own: UUID | None = None) -> Placed:
    """Pied et tête en 3D ; ValueError (texte dit) hors d'une pièce, trop plat ou au-dessus du
    plafond du niveau."""
    z_foot, foot_on, wall = foot_support(level, graph, spec.foot, spec.foot_height)
    head, z_head, head_on, beam = head_snap(level, graph, spec, own)
    if z_head > level.height + 1e-6:
        msg = f"tête du bois incliné au plus au plafond du niveau, {metres(level.height)}"
        raise ValueError(msg)
    if z_head < z_foot + MIN_RISE_M - 1e-6:
        raise ValueError(TOO_FLAT)
    return Placed(
        start=Point3(x=spec.foot[0], y=spec.foot[1], z=round(z_foot, 4)),
        end=Point3(x=head[0], y=head[1], z=round(z_head, 4)),
        head_on=head_on,
        foot_on=foot_on,
        head_beam=beam,
        foot_wall=wall,
    )


def spec_of(beam: Beam) -> BraceSpec:
    """Le bois tel qu'il a été posé, pour le reposer (déplacé, modifié, toit retouché)."""
    data = settings(beam)
    head_on = str(data.get("head_on", "height"))
    foot_on = str(data.get("foot_on", "height"))
    raw_beam = data.get("head_beam")
    return BraceSpec(
        foot=(beam.start.x, beam.start.y),
        head=(beam.end.x, beam.end.y),
        head_on=head_on if head_on in HEAD_MODES else "height",
        head_height=beam.end.z if head_on == "height" else None,
        foot_height=beam.start.z if foot_on == "height" else None,
        width=beam.section_width,
        depth=beam.section_height,
        profile=profile_of(beam),
        head_beam=UUID(str(raw_beam)) if raw_beam else None,
    )


def metadata_of(placed: Placed, profile: str) -> dict[str, object]:
    out: dict[str, object] = {
        "profile": profile,
        "head_on": placed.head_on,
        "foot_on": placed.foot_on,
    }
    if placed.head_beam is not None:
        out["head_beam"] = str(placed.head_beam)
    if placed.foot_wall is not None:
        out["foot_wall"] = str(placed.foot_wall)
    return out


def cast_profile(value: object) -> Profile:
    if value not in PROFILES:
        msg = "section du bois incliné : carrée (square) ou ronde (round)"
        raise ValueError(msg)
    return cast("Profile", value)


def plan_point(p: Point3) -> Point2:
    return Point2(x=p.x, y=p.y)
