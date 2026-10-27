"""Calibration métrique : résolution d'un facteur d'échelle à partir de mesures connues.

Hypothèses (documentées, à revoir en 0.2) :
- une seule échelle uniforme par niveau ;
- les mesures exploitables sont celles qui ciblent un mur (longueur d'axe), une
  ouverture (largeur) ou deux sommets du plan ; les mesures par points image
  demandent une caméra et sont ignorées ici ;
- `apply_scale` met à l'échelle les positions 2D et les grandeurs le long des murs
  (décalages, largeurs). Épaisseurs, hauteurs et allèges sont des valeurs métriques
  par défaut et ne sont pas touchées.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from openplan.geom.wallgraph import WallGraph
from openplan.schemas.common import Point2, Point3
from openplan.schemas.elements import (
    Beam,
    Camera,
    Ceiling,
    Fixture,
    Floor,
    Furniture,
    Light,
    Opening,
    Radiator,
    RoofPlane,
    Stairs,
    Wall,
)
from openplan.schemas.scene import Calibration, Level, Measurement

INCONSISTENCY_FACTOR = 3.0
"""Une mesure est incohérente si son résidu dépasse ce multiple de sa tolérance."""


class CalibrationError(ValueError):
    """Aucune mesure exploitable, ou géométrie dégénérée."""


@dataclass(frozen=True)
class ScaleObservation:
    measurement_id: UUID
    current: float
    """Valeur dans le plan avant calibration."""
    target: float
    """Valeur mesurée, en mètres."""
    tolerance: float


def observations(level: Level, measurements: Iterable[Measurement]) -> list[ScaleObservation]:
    """Mesures exploitables sur ce niveau, avec la valeur courante correspondante."""
    graph = WallGraph.from_level(level)
    out: list[ScaleObservation] = []
    for m in measurements:
        current = _current_value(graph, level, m)
        if current is None:
            continue
        if current <= 0.0:
            msg = f"la mesure {m.id} cible une longueur nulle dans le plan"
            raise CalibrationError(msg)
        out.append(
            ScaleObservation(
                measurement_id=m.id, current=current, target=m.value, tolerance=m.tolerance
            )
        )
    return out


def _current_value(graph: WallGraph, level: Level, m: Measurement) -> float | None:
    if m.element_id is not None:
        element = level.elements.get(m.element_id)
        if isinstance(element, Wall):
            return graph.wall_length(element.id)
        if isinstance(element, Opening):
            return element.width
        return None
    if m.vertex_ids is not None:
        a, b = (level.vertices.get(v) for v in m.vertex_ids)
        if a is None or b is None:
            return None
        return math.hypot(b.position.x - a.position.x, b.position.y - a.position.y)
    return None


def solve_scale(level: Level, measurements: Iterable[Measurement]) -> Calibration:
    """Facteur d'échelle par moindres carrés sur le logarithme des rapports.

    Une mesure : facteur direct. Plusieurs : moyenne géométrique des rapports, résidus
    par mesure en mètres, marquage des incohérences. Les tolérances ne pondèrent pas
    (choix simple, à revoir si des mesures de précisions très différentes coexistent).
    """
    obs = observations(level, measurements)
    if not obs:
        msg = "aucune mesure exploitable : il faut un mur, une ouverture ou deux sommets"
        raise CalibrationError(msg)
    factor = math.exp(sum(math.log(o.target / o.current) for o in obs) / len(obs))
    residuals = {o.measurement_id: factor * o.current - o.target for o in obs}
    rms = math.sqrt(sum(r * r for r in residuals.values()) / len(residuals))
    inconsistent = [
        o.measurement_id
        for o in obs
        if abs(residuals[o.measurement_id]) > INCONSISTENCY_FACTOR * o.tolerance
    ]
    return Calibration(
        level_id=level.id,
        measurement_ids=[o.measurement_id for o in obs],
        scale_factor=factor,
        residuals_m=residuals,
        rms_m=rms,
        inconsistent=inconsistent,
    )


def apply_scale(level: Level, factor: float) -> Level:  # noqa: PLR0912
    """Nouveau Level mis à l'échelle autour de l'origine du repère du niveau (fond de plan
    compris : il reste calé sur les murs)."""
    if factor <= 0.0:
        msg = "le facteur d'échelle doit être strictement positif"
        raise CalibrationError(msg)
    # Sonar S1244 : égalité voulue, 1,0 exactement est « aucune échelle » (valeur donnée, jamais
    # calculée) ; un facteur à peine différent de 1 est bien appliqué.
    if factor == 1.0:  # NOSONAR
        return level

    def p2(p: Point2) -> Point2:
        return Point2(x=p.x * factor, y=p.y * factor)

    def p3(p: Point3) -> Point3:
        return Point3(x=p.x * factor, y=p.y * factor, z=p.z)

    vertices = {
        vid: v.model_copy(update={"position": p2(v.position)}) for vid, v in level.vertices.items()
    }
    elements = dict(level.elements)
    for eid, e in level.elements.items():
        match e:
            case Opening() | Radiator():
                elements[eid] = e.model_copy(
                    update={"offset": e.offset * factor, "width": e.width * factor}
                )
            case Floor() | Ceiling() if e.polygon is not None:
                elements[eid] = e.model_copy(update={"polygon": [p2(p) for p in e.polygon]})
            case Stairs():
                width = min(1.2, max(0.7, e.width * factor))
                elements[eid] = e.model_copy(update={"position": p2(e.position), "width": width})
            case RoofPlane():
                elements[eid] = e.model_copy(
                    update={"eave_start": p2(e.eave_start), "eave_end": p2(e.eave_end)}
                )
            case Beam():
                elements[eid] = e.model_copy(update={"start": p3(e.start), "end": p3(e.end)})
            case Fixture() | Furniture():
                transform = e.transform.model_copy(update={"position": p3(e.transform.position)})
                elements[eid] = e.model_copy(update={"transform": transform})
            case Light() | Camera():
                elements[eid] = e.model_copy(update={"position": p3(e.position)})
            case _:
                pass
    background = dict(level.background_transform)
    for key in ("scale", "tx", "ty"):
        if key in background:
            background[key] = background[key] * factor
    return level.model_copy(
        update={"vertices": vertices, "elements": elements, "background_transform": background}
    )


ALIGN_TOL_RAD = math.radians(1.0)
"""Le fond de plan s'étire avec la pièce quand ses bords sont alignés sur les axes de la pièce
(à 1° près) ; sinon il garde sa forme (moyenne des deux facteurs) et on le dit."""


def apply_axis_scale(  # noqa: PLR0912, PLR0915
    level: Level, theta: float, sx: float, sy: float, sz: float = 1.0
) -> tuple[Level, list[str]]:
    """Mise à l'échelle non uniforme autour de l'origine : `sx` le long de l'axe d'angle
    `theta` (longueur de la pièce), `sy` le long de l'axe perpendiculaire (largeur), `sz` sur
    les hauteurs (plafond, murs d'égout, égouts et faîtage par la pente). Murs, ouvertures (le
    long de leur mur), meubles (place et orientation, pas leurs cotes), vues et fond suivent.
    Renvoie aussi ce qui n'a pu suivre qu'approximativement."""
    if min(sx, sy, sz) <= 0.0:
        msg = "les facteurs d'échelle doivent être strictement positifs"
        raise CalibrationError(msg)
    c, s = math.cos(theta), math.sin(theta)
    notes: list[str] = []

    def mxy(x: float, y: float) -> tuple[float, float]:
        u, v = (c * x + s * y) * sx, (-s * x + c * y) * sy
        return c * u - s * v, s * u + c * v

    def p2(p: Point2) -> Point2:
        x, y = mxy(p.x, p.y)
        return Point2(x=x, y=y)

    def p3(p: Point3) -> Point3:
        x, y = mxy(p.x, p.y)
        return Point3(x=x, y=y, z=p.z * sz)

    def stretch(dx: float, dy: float) -> float:
        n = math.hypot(dx, dy)
        if n < 1e-12:
            return 1.0
        x, y = mxy(dx / n, dy / n)
        return math.hypot(x, y)

    def turned(angle: float) -> float:
        x, y = mxy(math.cos(angle), math.sin(angle))
        return math.atan2(y, x)

    old = {vid: v.position for vid, v in level.vertices.items()}
    vertices = {
        vid: v.model_copy(update={"position": p2(v.position)}) for vid, v in level.vertices.items()
    }
    elements = dict(level.elements)
    for eid, e in level.elements.items():
        match e:
            case Wall():
                if e.height is not None:
                    elements[eid] = e.model_copy(update={"height": e.height * sz})
            case Opening() | Radiator():
                host = level.elements.get(e.host_wall)
                k = 1.0
                if isinstance(host, Wall) and host.start_vertex in old and host.end_vertex in old:
                    a, b = old[host.start_vertex], old[host.end_vertex]
                    k = stretch(b.x - a.x, b.y - a.y)
                elements[eid] = e.model_copy(update={"offset": e.offset * k, "width": e.width * k})
            case Floor() | Ceiling() if e.polygon is not None:
                elements[eid] = e.model_copy(update={"polygon": [p2(p) for p in e.polygon]})
            case Stairs():
                elements[eid] = e.model_copy(
                    update={
                        "position": p2(e.position),
                        "yaw": turned(e.yaw),
                        "rise": min(6.0, max(0.51, e.rise * sz)),
                    }
                )
            case RoofPlane():
                dx, dy = e.eave_end.x - e.eave_start.x, e.eave_end.y - e.eave_start.y
                k_run = stretch(-dy, dx)  # la course du versant est perpendiculaire à l'égout
                pitch = math.atan(math.tan(e.pitch) * sz / k_run)
                elements[eid] = e.model_copy(
                    update={
                        "eave_start": p2(e.eave_start),
                        "eave_end": p2(e.eave_end),
                        "eave_height": e.eave_height * sz,
                        "pitch": pitch,
                    }
                )
            case Beam():
                elements[eid] = e.model_copy(update={"start": p3(e.start), "end": p3(e.end)})
            case Fixture() | Furniture():
                yaw = turned(e.transform.yaw + math.pi / 2) - math.pi / 2
                transform = e.transform.model_copy(
                    update={"position": p3(e.transform.position), "yaw": yaw}
                )
                elements[eid] = e.model_copy(update={"transform": transform})
            case Camera():
                elements[eid] = e.model_copy(
                    update={"position": p3(e.position), "yaw": turned(e.yaw)}
                )
            case Light():
                elements[eid] = e.model_copy(update={"position": p3(e.position)})
            case _:
                pass
    background = dict(level.background_transform)
    if "tx" in background or "ty" in background:
        tx, ty = mxy(background.get("tx", 0.0), background.get("ty", 0.0))
        background["tx"], background["ty"] = tx, ty
    if level.background_image is not None or "scale" in background:
        delta = (theta - background.get("rotation", 0.0)) % (math.pi / 2)
        aligned = min(delta, math.pi / 2 - delta) <= ALIGN_TOL_RAD
        if aligned:
            # Axe x de l'image le long de la longueur de la pièce, ou de sa largeur (90°).
            along = (theta - background.get("rotation", 0.0)) % math.pi
            x_is_length = min(along, math.pi - along) <= ALIGN_TOL_RAD
            kx, ky = (sx, sy) if x_is_length else (sy, sx)
            background["stretch_x"] = background.get("stretch_x", 1.0) * kx
            background["stretch_y"] = background.get("stretch_y", 1.0) * ky
        else:
            if "scale" in background:
                background["scale"] = background["scale"] * math.sqrt(sx * sy)
            if abs(sx - sy) > 1e-9:
                notes.append(
                    "fond de plan non aligné sur la pièce : mis à l'échelle moyenne, sans étirement"
                )
    scaled = level.model_copy(
        update={
            "vertices": vertices,
            "elements": elements,
            "background_transform": background,
            "height": level.height * sz,
        }
    )
    return scaled, notes
