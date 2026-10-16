"""« Recalibrer » : le seul outil de correction d'échelle (directive d'augus du 29 sept. 2026).

Tout ce que la chaîne automatique construit est à l'échelle près ; l'utilisateur donne une seule
longueur qu'il connaît, de trois façons : deux points du plan (« ce mur fait 3,20 m »), un mur, ou
la hauteur sous plafond (la profondeur d'une photo se trompe d'un facteur global : la hauteur
corrige aussi le plan). Murs, ouvertures, meubles, caméras et fond de plan sont mis à l'échelle
ensemble ; le geste est celui de l'utilisateur (il fait autorité), annulable.
"""

from __future__ import annotations

import math
from uuid import UUID

from pydantic import Field
from shapely.geometry import MultiPoint, Polygon

from openplan.geom.scale import CalibrationError, apply_axis_scale, apply_scale
from openplan.geom.wallgraph import GeometryError, WallGraph
from openplan.scene import history
from openplan.schemas.common import Point2, Source, StrictModel, Uncertainty
from openplan.schemas.elements import Room, Wall
from openplan.schemas.scene import Calibration, Measurement, MeasurementKind, Project

MIN_CURRENT_M = 0.05
"""En dessous, la longueur cliquée est un clic double : le facteur serait absurde."""
SNAP_M = 0.20
"""Un point cliqué à moins de cette distance d'un sommet vise ce sommet : la mesure est alors
gardée entre les deux sommets (elle survit aux déplacements)."""
FACTOR_RANGE = (0.05, 20.0)


class RecalibrateBody(StrictModel):
    real_m: float = Field(gt=0.0, lt=100.0)
    """La longueur connue, en mètres."""
    a: Point2 | None = None
    b: Point2 | None = None
    """Deux points du plan (mètres) : la longueur entre eux vaut `real_m`."""
    wall_id: UUID | None = None
    """Ou un mur : sa longueur vaut `real_m`."""
    ceiling: bool = False
    """Ou la hauteur sous plafond vaut `real_m` (l'échelle du plan suit)."""


class RecalibrateSummary(StrictModel):
    factor: float
    current_m: float
    real_m: float
    what: str
    """« la distance cliquée », « le mur … », « la hauteur sous plafond »."""
    text: str = ""
    """Dimensions de la pièce avant → après (R3), quand c'est ce qui a été donné."""


def _nearest_vertex(project: Project, level_id: UUID, p: Point2) -> UUID | None:
    best: tuple[float, UUID] | None = None
    for vid, v in project.levels[level_id].vertices.items():
        d = math.hypot(v.position.x - p.x, v.position.y - p.y)
        if d <= SNAP_M and (best is None or d < best[0]):
            best = (d, vid)
    return best[1] if best else None


def recalibrate_level(  # noqa: PLR0912
    project: Project, level_id: UUID, body: RecalibrateBody, *, actor: str
) -> tuple[Project, RecalibrateSummary]:
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise CalibrationError(msg)
    measurement: Measurement | None = None
    if body.ceiling:
        current, what = level.height, "la hauteur sous plafond"
    elif body.wall_id is not None:
        wall = level.elements.get(body.wall_id)
        if not isinstance(wall, Wall):
            msg = f"mur inconnu : {body.wall_id}"
            raise CalibrationError(msg)
        current = WallGraph.from_level(level).wall_length(wall.id)
        what = f"le mur {wall.name or ''}".strip()
        measurement = Measurement(
            kind=MeasurementKind.WALL_LENGTH, value=body.real_m, element_id=wall.id
        )
    elif body.a is not None and body.b is not None:
        current = math.hypot(body.b.x - body.a.x, body.b.y - body.a.y)
        what = "la distance cliquée"
        va, vb = (
            _nearest_vertex(project, level_id, body.a),
            _nearest_vertex(project, level_id, body.b),
        )
        if va is not None and vb is not None and va != vb:
            measurement = Measurement(
                kind=MeasurementKind.POINT_TO_POINT, value=body.real_m, vertex_ids=(va, vb)
            )
    else:
        msg = "donnez deux points, un mur ou la hauteur sous plafond"
        raise CalibrationError(msg)
    if current < MIN_CURRENT_M:
        msg = f"{what} est trop courte dans le plan ({current:.2f} m) pour servir de référence"
        raise CalibrationError(msg)
    factor = body.real_m / current
    if not FACTOR_RANGE[0] <= factor <= FACTOR_RANGE[1]:
        msg = (
            f"{what} mesure {current:.2f} m dans le plan et {body.real_m:.2f} m en vrai : le "
            f"facteur ×{factor:.2f} est invraisemblable, vérifiez l'unité"
        )
        raise CalibrationError(msg)

    scaled = apply_scale(level, factor)
    update: dict[str, object] = {}
    if body.ceiling:
        update = {"height": round(body.real_m, 3), "height_source": Source.MEASURED}
    elif measurement is not None and measurement.element_id is not None:
        wall = scaled.elements[measurement.element_id]
        update = {
            "elements": {
                **scaled.elements,
                wall.id: wall.model_copy(
                    update={
                        "source": Source.MEASURED,
                        "confidence": 1.0,
                        "uncertainty": Uncertainty(sigma=measurement.tolerance),
                    }
                ),
            }
        }
    if update:
        scaled = scaled.model_copy(update=update)
    measurements = dict(project.measurements)
    calibrations = list(project.calibrations)
    if measurement is not None:
        measurements[measurement.id] = measurement
        calibrations.append(
            Calibration(
                level_id=level_id,
                measurement_ids=[measurement.id],
                scale_factor=factor,
                residuals_m={measurement.id: 0.0},
                rms_m=0.0,
            )
        )
    after = project.model_copy(
        update={
            "levels": {**project.levels, level_id: scaled},
            "measurements": measurements,
            "calibrations": calibrations,
        }
    )
    label = f"Recalibrer : {what} = {body.real_m:.2f} m (×{factor:.3f})"
    committed = history.commit(project, after, actor=actor, label=label)
    return committed, RecalibrateSummary(
        factor=factor, current_m=current, real_m=body.real_m, what=what
    )


# ------------------------------------------------------------------ R3 : longueur, largeur, hauteur
class DimensionsBody(StrictModel):
    """Dimensions connues de la pièce, chacune facultative (R3, 3 octobre 2026)."""

    length_m: float | None = Field(default=None, gt=0.0, lt=100.0)
    width_m: float | None = Field(default=None, gt=0.0, lt=100.0)
    height_m: float | None = Field(default=None, gt=0.0, lt=20.0)


class RoomSize(StrictModel):
    length_m: float
    width_m: float
    height_m: float


class DimensionsSummary(StrictModel):
    before: RoomSize
    after: RoomSize
    sx: float
    """Facteur le long de la longueur de la pièce."""
    sy: float
    """Facteur le long de sa largeur."""
    sz: float
    """Facteur des hauteurs."""
    uniform: bool
    text: str
    """« 9,65 × 5,00 m → 8,00 × 4,20 m » (et la hauteur si elle change)."""
    notes: list[str] = Field(default_factory=list)


def _fr(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def room_axes(project: Project, level_id: UUID) -> tuple[float, float, float]:
    """(angle de la longueur, longueur, largeur) de la plus grande pièce, d'après son rectangle
    minimal ; à défaut, l'emprise des sommets."""
    level = project.levels[level_id]
    graph = WallGraph.from_level(level)
    best: Polygon | None = None
    for room in (e for e in level.elements.values() if isinstance(e, Room)):
        try:
            polygon = graph.room_polygon(room.id)
        except GeometryError:
            continue
        if not polygon.is_empty and (best is None or polygon.area > best.area):
            best = polygon
    if best is None:
        points = [(v.position.x, v.position.y) for v in level.vertices.values()]
        if len(points) < 3:
            msg = "tracez d'abord les murs : il n'y a pas de pièce à mettre aux dimensions"
            raise CalibrationError(msg)
        best = Polygon(MultiPoint(points).convex_hull)
    rect = best.minimum_rotated_rectangle
    coords = list(rect.exterior.coords)
    (x0, y0), (x1, y1), (x2, y2) = coords[0], coords[1], coords[2]
    e0, e1 = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
    if e0 >= e1:
        return math.atan2(y1 - y0, x1 - x0), e0, e1
    return math.atan2(y2 - y1, x2 - x1), e1, e0


def _factors(
    body: DimensionsBody, length: float, width: float, height: float
) -> tuple[float, float, float, str, float, float]:
    """(sx, sy, sz, ce qui a été donné, valeur actuelle, valeur voulue) pour le résumé."""
    if body.length_m is not None and body.width_m is not None:
        sx, sy = body.length_m / length, body.width_m / width
        what, current, real = "la pièce", length, body.length_m
    elif body.length_m is not None:
        sx = sy = body.length_m / length
        what, current, real = "la longueur de la pièce", length, body.length_m
    elif body.width_m is not None:
        sx = sy = body.width_m / width
        what, current, real = "la largeur de la pièce", width, body.width_m
    elif body.height_m is not None:
        sx = sy = body.height_m / height
        what, current, real = "la hauteur sous plafond", height, body.height_m
    else:
        msg = "donnez au moins une dimension : longueur, largeur ou hauteur"
        raise CalibrationError(msg)
    if body.height_m is not None and (body.length_m is not None or body.width_m is not None):
        sz = body.height_m / height
    elif body.length_m is None and body.width_m is None:
        sz = sx  # la hauteur seule : tout le plan suit, comme aujourd'hui
    else:
        sz = 1.0
    for factor in (sx, sy, sz):
        if not FACTOR_RANGE[0] <= factor <= FACTOR_RANGE[1]:
            msg = (
                f"facteur ×{factor:.2f} invraisemblable pour {what} : vérifiez l'unité "
                f"(aujourd'hui {_fr(length)} × {_fr(width)} m, hauteur {_fr(height)} m)"
            )
            raise CalibrationError(msg)
    return sx, sy, sz, what, current, real


def preview_dimensions(project: Project, level_id: UUID, body: DimensionsBody) -> DimensionsSummary:
    """Ce que ferait « Appliquer » (aucune écriture) ; sans valeur, les dimensions actuelles."""
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise CalibrationError(msg)
    _, length, width = room_axes(project, level_id)
    before = RoomSize(length_m=length, width_m=width, height_m=level.height)
    if body.length_m is None and body.width_m is None and body.height_m is None:
        return DimensionsSummary(
            before=before, after=before, sx=1.0, sy=1.0, sz=1.0, uniform=True, text=""
        )
    sx, sy, sz, _, _, _ = _factors(body, length, width, level.height)
    after = RoomSize(length_m=length * sx, width_m=width * sy, height_m=level.height * sz)
    text = f"{_fr(length)} × {_fr(width)} m → {_fr(after.length_m)} × {_fr(after.width_m)} m"
    if abs(sz - 1.0) > 1e-9:
        text += f" · hauteur {_fr(level.height)} → {_fr(after.height_m)} m"
    return DimensionsSummary(
        before=before, after=after, sx=sx, sy=sy, sz=sz, uniform=abs(sx - sy) < 1e-9, text=text
    )


def recalibrate_dimensions(
    project: Project, level_id: UUID, body: DimensionsBody, *, actor: str
) -> tuple[Project, RecalibrateSummary, DimensionsSummary]:
    """Met la pièce aux dimensions données : une valeur, échelle uniforme (comme aujourd'hui) ;
    longueur ET largeur, échelle selon les deux axes de la pièce ; la hauteur règle alors les
    hauteurs. Les valeurs saisies font foi (`user_defined`) ; geste annulable."""
    preview = preview_dimensions(project, level_id, body)
    level = project.levels[level_id]
    theta, length, width = room_axes(project, level_id)
    sx, sy, sz, what, current, real = _factors(body, length, width, level.height)
    scaled, notes = apply_axis_scale(level, theta, sx, sy, sz)
    elements = dict(scaled.elements)
    given = {"length": body.length_m is not None, "width": body.width_m is not None}
    for eid, e in scaled.elements.items():
        if not isinstance(e, Wall) or e.start_vertex not in scaled.vertices:
            continue
        a, b = scaled.vertices[e.start_vertex].position, scaled.vertices[e.end_vertex].position
        angle = math.atan2(b.y - a.y, b.x - a.x)
        along = abs(math.cos(angle - theta))
        if (given["length"] and along > 0.98) or (given["width"] and along < 0.2):
            elements[eid] = e.model_copy(
                update={
                    "source": Source.USER_DEFINED,
                    "confidence": 1.0,
                    "uncertainty": Uncertainty(),
                }
            )
    update: dict[str, object] = {"elements": elements}
    if body.height_m is not None:
        update.update({"height": round(body.height_m, 3), "height_source": Source.USER_DEFINED})
    scaled = scaled.model_copy(update=update)
    after = project.model_copy(update={"levels": {**project.levels, level_id: scaled}})
    label = f"Recalibrer : {preview.text}"
    committed = history.commit(project, after, actor=actor, label=label)
    summary = RecalibrateSummary(
        factor=math.sqrt(sx * sy),
        current_m=current,
        real_m=real,
        what=what,
        text=preview.text if what == "la pièce" else "",
    )
    return committed, summary, preview.model_copy(update={"notes": notes})
