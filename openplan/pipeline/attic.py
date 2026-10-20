"""Étape « attic.planes » : photo → profondeur → plans (sol, pentes, pignon) → combles estimés.

Deviner avant de saisir : le sol est cherché dans le nuage de points lui-même (grand plan dont
la normale regarde vers le haut de la caméra), ce qui donne la hauteur de prise de vue sans la
demander. Les autres plans sont classés par leur inclinaison sur ce sol : horizontaux
(plafond), pentes (toit) et verticaux (pignons dans l'axe du faîtage, murs latéraux). Le repère
des points de fuite, quand il existe, ne sert qu'à la focale et à un recoupement de la verticale.

Tout ce qui sort est `estimated` avec un sigma : erreur d'échelle relative de la profondeur
apprise, combinée au résidu du plan. Rien n'est écrit dans la scène ici ; l'étape produit un
`AtticEstimate` que l'utilisateur corrige avant de générer la géométrie.
"""

from __future__ import annotations

import json
import math
import time
from itertools import combinations
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    DepthEstimator,
    DepthMap,
    RoofWindowDetector,
)
from openplan.geom.planes import (
    FittedPlane,
    Float,
    Plane,
    angle_between,
    backproject,
    fit_planes,
    rotate_plane,
)
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.roof_windows import RoofWindowSighting, locate_roof_windows
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "attic.planes"
SCALE_SIGMA = 0.15
"""Incertitude relative d'échelle attribuée à la profondeur apprise (docs/evaluation)."""
FLOOR_MAX_TILT = math.radians(50.0)
"""Le sol doit regarder vers le haut de l'image à moins de 50° (tangage de la photo)."""
HORIZONTAL_MAX_TILT = math.radians(12.0)
SLOPE_MAX_TILT = math.radians(78.0)
AXIS_ALIGNED = 0.9
"""|composante| minimale d'une normale pour compter comme alignée sur un axe du plan."""
FRAME_DISAGREEMENT_WARN = math.radians(12.0)
USABLE_HEIGHT_M = 1.80
STRIDE = 4

PlaneKind = Literal["floor", "ceiling", "slope", "gable", "side_wall", "other"]


class Estimate(StrictModel):
    value: float
    sigma: float = Field(ge=0.0)
    source: Source = Source.ESTIMATED
    note: str | None = None

    @property
    def text(self) -> str:
        return f"{self.value:.2f} ± {self.sigma:.2f} m"


class PlaneSummary(StrictModel):
    kind: PlaneKind
    normal: tuple[float, float, float]
    """Dans le repère des combles : X le long du faîtage, Y en travers, Z vers le haut."""
    distance_m: float = Field(ge=0.0)
    tilt_deg: float = Field(ge=0.0, le=180.0)
    """Angle entre la normale et la verticale : 0 = horizontal, 90 = vertical."""
    inlier_fraction: float = Field(ge=0.0, le=1.0)
    rms_m: float = Field(ge=0.0)


class AtticEstimate(StrictModel):
    camera_height: Estimate | None = None
    ridge_height: Estimate | None = None
    """Au-dessus du sol ; None avec une seule pente visible."""
    eave_heights: list[Estimate] = Field(default_factory=list)
    """Par pente : hauteur où elle rencontre le mur latéral (ou 0 si elle touche le sol)."""
    width: Estimate | None = None
    """Entre les deux égouts (ou murs latéraux)."""
    usable_width: Estimate | None = None
    """Largeur où la hauteur sous pente dépasse USABLE_HEIGHT_M."""
    gable_distance: Estimate | None = None
    """De la caméra au pignon visible, le long du faîtage."""
    length: Estimate | None = None
    """Entre deux pignons, seulement si les deux sont vus."""
    ceiling_height: Estimate | None = None
    slope_pitch_deg: list[float] = Field(default_factory=list)
    planes: list[PlaneSummary] = Field(default_factory=list)
    scale_factor: float = Field(default=1.0, gt=0.0)
    """Facteur appliqué aux longueurs du modèle de profondeur (1 = échelle du modèle)."""
    scale_note: str | None = None
    roof_windows: list[RoofWindowSighting] = Field(default_factory=list)
    """Fenêtres de toit vues sur la photo (C2), repère centré sur l'appareil."""
    floor_assumed: bool = False
    """KAN-45 : sol hors champ, posé à la hauteur de prise de vue (saisie ou par défaut)."""
    eave_bound: Estimate | None = None
    """KAN-61 : égout non vu (sol supposé, aucune jambette) : il est au plus à la hauteur du bas
    de pente le plus bas encore vu. `eave_heights` reste alors vide (égout inconnu)."""


PRIOR_REL_SIGMA = 0.10
"""Incertitude relative attribuée à une hauteur de prise de vue saisie sans mesure."""
SCALE_DISAGREEMENT = 2.0
"""Au-delà de ce facteur entre le modèle et la hauteur saisie, l'un des deux est faux."""


def height_words(camera_height_m: float, *, entered: bool) -> str:
    """KAN-62 : « hauteur de prise de vue saisie 1.45 m » ou « … supposée 1.50 m (par défaut) »."""
    if entered:
        return f"hauteur de prise de vue saisie {camera_height_m:.2f} m"
    return f"hauteur de prise de vue supposée {camera_height_m:.2f} m (par défaut)"


def rescale(
    estimate: AtticEstimate, camera_height_m: float, *, entered: bool = True
) -> AtticEstimate:
    """Recale toutes les longueurs pour que la hauteur de caméra vaille la valeur donnée.

    Le terme d'échelle du modèle (SCALE_SIGMA) est remplacé par l'incertitude de cette hauteur ;
    le résidu géométrique de chaque plan est conservé, mis à l'échelle. `entered` (KAN-62) :
    hauteur saisie (`user_defined`) ou valeur par défaut (`inferred`) ; les valeurs calculées
    sont les mêmes, seule l'étiquette change."""
    if estimate.camera_height is None or camera_height_m <= 0:
        return estimate
    factor = camera_height_m / estimate.camera_height.value
    words = height_words(camera_height_m, entered=entered)

    def scaled(e: Estimate | None) -> Estimate | None:
        if e is None:
            return None
        rms2 = max(e.sigma**2 - (SCALE_SIGMA * e.value) ** 2, 0.0)
        value = e.value * factor
        sigma = math.sqrt((PRIOR_REL_SIGMA * value) ** 2 + rms2 * factor**2)
        return e.model_copy(update={"value": value, "sigma": sigma})

    return estimate.model_copy(
        update={
            "camera_height": Estimate(
                value=camera_height_m,
                sigma=PRIOR_REL_SIGMA * camera_height_m,
                source=Source.USER_DEFINED if entered else Source.INFERRED,
                note=words,
            ),
            "ridge_height": scaled(estimate.ridge_height),
            "eave_heights": [e for e in (scaled(x) for x in estimate.eave_heights) if e],
            "width": scaled(estimate.width),
            "usable_width": scaled(estimate.usable_width),
            "gable_distance": scaled(estimate.gable_distance),
            "length": scaled(estimate.length),
            "ceiling_height": scaled(estimate.ceiling_height),
            "eave_bound": scaled(estimate.eave_bound),
            "planes": [
                p.model_copy(
                    update={"distance_m": p.distance_m * factor, "rms_m": p.rms_m * factor}
                )
                for p in estimate.planes
            ],
            "roof_windows": [w.scaled(factor) for w in estimate.roof_windows],
            "scale_factor": estimate.scale_factor * factor,
            "scale_note": f"Échelle recalée sur la {words} : facteur {factor:.2f} appliqué aux "
            f"longueurs du modèle de profondeur (qui donnait "
            f"{estimate.camera_height.value:.2f} m).",
        }
    )


class AtticRun(StrictModel):
    run_id: UUID
    image_id: UUID
    estimate: AtticEstimate
    result: StepResult


class ClassifiedPlanes:
    """Plans étiquetés, repère des combles (lignes = X, Y, Z dans le repère caméra)."""

    def __init__(self, floor: FittedPlane, axes: Float) -> None:
        self.floor = floor
        self.axes = axes
        self.floor_assumed = False
        """KAN-45 : sol sans points, posé à la hauteur de prise de vue (sol hors champ)."""
        self.kinds: dict[int, PlaneKind] = {}
        self.world: dict[int, Plane] = {}


def estimate_scale_sigma(value: float, *rms: float) -> float:
    return float(math.sqrt((SCALE_SIGMA * value) ** 2 + sum(r * r for r in rms)))


def find_floor(planes: list[FittedPlane]) -> int | None:
    """Indice du plan sol : normale (vers la caméra) dirigée vers le haut de l'image, le plus
    soutenu. L'axe y caméra pointe vers le bas, donc « vers le haut » = composante y négative."""
    down = np.array([0.0, 1.0, 0.0])
    candidates = [
        i for i, p in enumerate(planes) if angle_between(-p.plane.normal, down) <= FLOOR_MAX_TILT
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda i: planes[i].count)


SLOPE_PAIR_CANDIDATES = 6
"""Plans les mieux soutenus parmi lesquels chercher les deux pentes quand le sol manque."""
FALLBACK_MIN_FRACTION = 0.01
"""Sol non trouvé : plans cherchés jusqu'à 1 % des points (2 % sinon), pour garder le peu de
sol ou de jambettes encore dans le champ d'une photo levée."""
ASSUMED_FLOOR_SIGMA = 0.20
"""Incertitude (m) d'une hauteur de prise de vue supposée, sans sol vu ni saisie."""


def _tilt(normal: Float, up: Float) -> float:
    a = angle_between(normal, up)
    return min(a, math.pi - a)


def up_from_slopes(planes: list[FittedPlane]) -> Float | None:
    """KAN-45 : sol hors champ (photo levée sous un rampant). La verticale est la bissectrice
    des normales de deux pentes opposées (toit symétrique : leurs normales, orientées vers la
    caméra, font le même angle avec la verticale de part et d'autre). Parmi les paires
    possibles, celle dont la verticale est appuyée par le plus de points sur des plans
    verticaux ou horizontaux (pignon, jambettes, plafond) ; None sans paire plausible."""
    down = np.array([0.0, 1.0, 0.0])
    order = sorted(range(len(planes)), key=lambda i: -planes[i].count)[:SLOPE_PAIR_CANDIDATES]
    best: tuple[int, Float] | None = None
    for a, b in combinations(order, 2):
        total = planes[a].plane.normal + planes[b].plane.normal
        if np.linalg.norm(total) < 1e-6:
            continue
        up: Float = -total / np.linalg.norm(total)
        # Comme le sol : la verticale regarde vers le haut de l'image (tangage < 50°).
        if angle_between(-up, down) > FLOOR_MAX_TILT:
            continue
        if not HORIZONTAL_MAX_TILT < _tilt(planes[a].plane.normal, up) <= SLOPE_MAX_TILT:
            continue
        support = planes[a].count + planes[b].count
        for i in order:
            if i in (a, b):
                continue
            t = _tilt(planes[i].plane.normal, up)
            if t <= HORIZONTAL_MAX_TILT or t > SLOPE_MAX_TILT:
                support += planes[i].count
        if best is None or support > best[0]:
            best = (support, up)
    return None if best is None else best[1]


EAVE_BOUND_PERCENTILE = 1.0
"""Bas de pente vu : 1er centile des hauteurs de ses points (au-delà du bruit de profondeur)."""


def _eave_bound(
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes, slopes: list[int]
) -> Estimate | None:
    """KAN-61 : égout non vu. Il est au plus à la hauteur du point de pente le plus bas encore
    dans le champ (exact quand le bas de la pente est vu, une majoration sinon)."""
    h = cls.floor.plane.distance
    lows = [
        float(np.percentile((points[planes[i].inliers] @ cls.axes.T)[:, 2], EAVE_BOUND_PERCENTILE))
        + h
        for i in slopes
        if planes[i].count
    ]
    if not lows:
        return None
    value = min(lows)
    rms = max(planes[i].rms_m for i in slopes)
    return Estimate(
        value=value,
        sigma=estimate_scale_sigma(value, rms),
        source=Source.INFERRED,
        note="égout non vu sur la photo : au plus à la hauteur du bas de pente le plus bas vu",
    )


SIDE_WALL_MIN_SHARE = 0.003
"""Part minimale du nuage pour retenir une jambette cherchée dans les points restants."""
SIDE_WALL_MAX_SPREAD_M = 0.05
SIDE_WALL_MIN_CLUSTER = 0.6
"""Part des points restants d'un côté qui doivent tenir à SIDE_WALL_MAX_SPREAD_M de la médiane."""


def residual_side_walls(
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes, slopes: list[int]
) -> list[FittedPlane]:
    """KAN-45 : photo levée, seul le haut des jambettes reste dans le champ, trop peu pour une
    recherche de plans à l'aveugle (qui les relie parfois en un faux plan incliné). Le repère
    étant connu, chaque jambette est cherchée directement : les points qui ne sont ni sur les
    deux pentes ni sur un pignon, d'un même côté du faîtage et groupés à une même distance
    latérale. Plan vertical, normale vers la caméra."""
    used = np.zeros(len(points), dtype=bool)
    for i in [*slopes, *(i for i, k in cls.kinds.items() if k == "gable")]:
        used[planes[i].inliers] = True
    world = points @ cls.axes.T
    finite = np.isfinite(world).all(axis=1)
    out: list[FittedPlane] = []
    for side in (-1.0, 1.0):
        idx = np.flatnonzero(~used & finite & (np.sign(world[:, 1]) == side))
        if len(idx) < SIDE_WALL_MIN_SHARE * len(points):
            continue
        y = world[idx, 1]
        y_wall = float(np.median(y))
        near = idx[np.abs(y - y_wall) < SIDE_WALL_MAX_SPREAD_M]
        if len(near) < SIDE_WALL_MIN_SHARE * len(points) or len(near) < SIDE_WALL_MIN_CLUSTER * len(
            idx
        ):
            continue
        normal: Float = cls.axes.T @ np.array([0.0, -side, 0.0])
        rms = float(np.sqrt(np.mean((world[near, 1] - y_wall) ** 2)))
        out.append(FittedPlane(Plane(normal, abs(y_wall)), near.astype(np.intp), rms))
    return out


def attic_axes(up: Float, planes: list[FittedPlane], floor_idx: int) -> Float:
    """Repère : Z = haut ; X = direction du faîtage, soit l'horizontale perpendiculaire à la
    normale de la pente la mieux soutenue (exact pour toute pente de toit) ; sans pente, la
    normale du plus grand mur vertical ; à défaut, l'axe caméra horizontal."""
    # Inclinaison du plan, indépendante du sens de la normale (une pente au-dessus de la
    # caméra a sa normale orientée vers le bas : angle avec la verticale > 90°).
    tilts = {
        i: min(a, math.pi - a)
        for i, p in enumerate(planes)
        if i != floor_idx
        for a in (angle_between(p.plane.normal, up),)
    }
    slopes = [i for i, t in tilts.items() if HORIZONTAL_MAX_TILT < t <= SLOPE_MAX_TILT]
    x: Float | None = None
    if slopes:
        best = max(slopes, key=lambda i: planes[i].count)
        x = np.cross(up, planes[best].plane.normal)
    else:
        walls = [i for i, t in tilts.items() if t > SLOPE_MAX_TILT]
        if walls:
            biggest = max(walls, key=lambda i: planes[i].count)
            x = planes[biggest].plane.normal.copy()
    if x is None or np.linalg.norm(x) < 1e-6:
        x = np.array([0.0, 0.0, 1.0])
    x = x - float(x @ up) * up
    if np.linalg.norm(x) < 1e-6:
        x = np.array([1.0, 0.0, 0.0]) - up[0] * up
    x = x / np.linalg.norm(x)
    y = np.cross(up, x)
    axes: Float = np.vstack([x, y, up])
    return axes


def classify(planes: list[FittedPlane], floor_idx: int) -> ClassifiedPlanes:
    up = planes[floor_idx].plane.normal
    out = ClassifiedPlanes(planes[floor_idx], attic_axes(up, planes, floor_idx))
    for i, fitted in enumerate(planes):
        world = rotate_plane(fitted.plane, out.axes)
        out.world[i] = world
        nx, ny, nz = (float(v) for v in world.normal)
        tilt = math.acos(min(1.0, abs(nz)))
        kind: PlaneKind
        if i == floor_idx:
            kind = "floor"
        elif tilt <= HORIZONTAL_MAX_TILT:
            kind = "ceiling" if nz < 0 else "other"
        elif tilt <= SLOPE_MAX_TILT:
            kind = "slope"
        elif abs(nx) >= AXIS_ALIGNED:
            kind = "gable"
        elif abs(ny) >= AXIS_ALIGNED:
            kind = "side_wall"
        else:
            kind = "other"
        out.kinds[i] = kind
    return out


def _slope_y_at_height(plane: Plane, z_world: float) -> float | None:
    """Coordonnée Y (en travers) où la pente atteint la hauteur z (repère caméra-centré)."""
    ny = float(plane.normal[1])
    if abs(ny) < 1e-6:
        return None
    return -(plane.distance + float(plane.normal[2]) * z_world) / ny


def _tilt_deg(plane: Plane) -> float:
    return math.degrees(math.acos(min(1.0, abs(float(plane.normal[2])))))


def _summaries(planes: list[FittedPlane], cls: ClassifiedPlanes, total: int) -> list[PlaneSummary]:
    out: list[PlaneSummary] = []
    for i, p in enumerate(planes):
        n = cls.world[i].normal
        out.append(
            PlaneSummary(
                kind=cls.kinds[i],
                normal=(round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4)),
                distance_m=cls.world[i].distance,
                tilt_deg=_tilt_deg(cls.world[i]),
                inlier_fraction=p.count / max(total, 1),
                rms_m=p.rms_m,
            )
        )
    return out


def _eave(
    planes: list[FittedPlane], cls: ClassifiedPlanes, slope_idx: int, sides: list[int]
) -> tuple[float, float, float] | None:
    """(y de l'égout, hauteur d'égout, rms) : la pente rencontre le sol, ou un mur latéral
    du même côté et plus proche, auquel cas l'égout est à la hauteur de ce mur."""
    h = cls.floor.plane.distance
    plane = cls.world[slope_idx]
    y_floor = _slope_y_at_height(plane, -h)
    if y_floor is None:
        return None
    eave_y, eave_h, rms = y_floor, 0.0, planes[slope_idx].rms_m
    for s in sides:
        wall = cls.world[s]
        y_wall = -wall.distance / float(wall.normal[1])
        if y_wall * y_floor > 0 and abs(y_wall) < abs(y_floor):
            z = plane.height_at(0.0, y_wall)
            if z is not None and z + h > eave_h:
                eave_y, eave_h, rms = y_wall, z + h, math.hypot(rms, planes[s].rms_m)
    return eave_y, eave_h, rms


SYMMETRY_NOTE = "mur latéral non vu : supposé symétrique de l'autre côté du faîtage"


def _mirror_unseen_wall(
    bounds: list[tuple[float, float, float]], ridge_y: float | None
) -> list[tuple[float, float, float, Source]]:
    """Quand un seul côté bute sur un mur latéral, l'autre côté (qui semble descendre jusqu'au
    sol parce que son mur est hors champ) reçoit son symétrique par rapport au faîtage,
    marqué `inferred`."""
    out = [(y, z, rms, Source.ESTIMATED) for y, z, rms in bounds]
    if len(bounds) != 2 or ridge_y is None:
        return out
    walled = [i for i, b in enumerate(bounds) if b[1] > 0]
    if len(walled) != 1:
        return out
    known = bounds[walled[0]]
    other = 1 - walled[0]
    mirrored = 2 * ridge_y - known[0]
    if abs(mirrored) < abs(bounds[other][0]) and mirrored * bounds[other][0] > 0:
        out[other] = (mirrored, known[1], known[2], Source.INFERRED)
    return out


def _slopes_summary(
    est: AtticEstimate, planes: list[FittedPlane], cls: ClassifiedPlanes, slopes: list[int]
) -> None:
    """Faîtage, largeur utile, égouts et largeur entre égouts (au plus deux pentes)."""
    h = cls.floor.plane.distance
    sides = [i for i, k in cls.kinds.items() if k == "side_wall"]
    bounds: list[tuple[float, float, float]] = []
    for i in slopes:
        est.slope_pitch_deg.append(_tilt_deg(cls.world[i]))
        eave = _eave(planes, cls, i, sides)
        if eave is not None:
            bounds.append(eave)
    ridge_y: float | None = None
    if len(slopes) >= 2:
        a, b = cls.world[slopes[0]], cls.world[slopes[1]]
        ridge = ridge_point(a, b)
        rms = math.hypot(planes[slopes[0]].rms_m, planes[slopes[1]].rms_m)
        if ridge is not None:
            ridge_y, height = ridge[0], ridge[1] + h
            est.ridge_height = Estimate(value=height, sigma=estimate_scale_sigma(height, rms))
            if height > USABLE_HEIGHT_M:
                ya = _slope_y_at_height(a, USABLE_HEIGHT_M - h)
                yb = _slope_y_at_height(b, USABLE_HEIGHT_M - h)
                if ya is not None and yb is not None:
                    width = abs(ya - yb)
                    est.usable_width = Estimate(value=width, sigma=estimate_scale_sigma(width, rms))
    eaves = _mirror_unseen_wall(bounds, ridge_y)
    if cls.floor_assumed:
        # KAN-61 : sol supposé, une pente sans jambette ne « touche » qu'un sol qu'on n'a pas vu.
        # Son égout est inconnu (borne donnée à part), jamais 0.
        eaves = [e for e in eaves if e[1] > 0.0]
    for _y, z, rms_e, source in eaves:
        est.eave_heights.append(
            Estimate(
                value=z,
                sigma=estimate_scale_sigma(z, rms_e),
                source=source,
                note=SYMMETRY_NOTE if source is Source.INFERRED else None,
            )
        )
    if len(eaves) == 2 and eaves[0][0] * eaves[1][0] < 0:
        width = abs(eaves[0][0] - eaves[1][0])
        inferred = any(e[3] is Source.INFERRED for e in eaves)
        est.width = Estimate(
            value=width,
            sigma=estimate_scale_sigma(width, eaves[0][2], eaves[1][2]),
            source=Source.INFERRED if inferred else Source.ESTIMATED,
            note=SYMMETRY_NOTE if inferred else None,
        )


def _gables_summary(
    est: AtticEstimate, planes: list[FittedPlane], cls: ClassifiedPlanes, gables: list[int]
) -> None:
    distances = sorted(
        ((-cls.world[g].distance / float(cls.world[g].normal[0]), planes[g].rms_m) for g in gables),
        key=lambda t: abs(t[0]),
    )
    if not distances:
        return
    x0, rms0 = distances[0]
    est.gable_distance = Estimate(value=abs(x0), sigma=estimate_scale_sigma(abs(x0), rms0))
    opposite = [t for t in distances[1:] if t[0] * x0 < 0]
    if opposite:
        x1, rms1 = opposite[0]
        length = abs(x0) + abs(x1)
        est.length = Estimate(value=length, sigma=estimate_scale_sigma(length, rms0, rms1))


def _opposite_slopes(planes: list[FittedPlane], cls: ClassifiedPlanes) -> list[int]:
    """La pente principale, puis la mieux soutenue des pentes de l'autre côté du faîtage
    (normale transversale de signe opposé). Un plan incliné du même côté n'est pas un
    second versant : il est ignoré pour le faîtage."""
    slopes = sorted(
        (i for i, k in cls.kinds.items() if k == "slope"), key=lambda i: -planes[i].count
    )
    if not slopes:
        return []
    first = slopes[0]
    side = float(cls.world[first].normal[1])
    opposite = [i for i in slopes[1:] if float(cls.world[i].normal[1]) * side < 0]
    return [first, *opposite[:1]]


def summarize(planes: list[FittedPlane], cls: ClassifiedPlanes, total: int) -> AtticEstimate:
    """Dimensions des combles à partir des plans classés. Origine = caméra, Z = haut."""
    h = cls.floor.plane.distance
    est = AtticEstimate(
        camera_height=Estimate(value=h, sigma=estimate_scale_sigma(h, cls.floor.rms_m)),
        planes=_summaries(planes, cls, total),
    )
    _slopes_summary(est, planes, cls, _opposite_slopes(planes, cls))
    _gables_summary(est, planes, cls, [i for i, k in cls.kinds.items() if k == "gable"])
    ceilings = [i for i, k in cls.kinds.items() if k == "ceiling"]
    if ceilings:
        c = max(ceilings, key=lambda i: planes[i].count)
        z = cls.world[c].distance + h
        est.ceiling_height = Estimate(value=z, sigma=estimate_scale_sigma(z, planes[c].rms_m))
    return est


def ridge_point(a: Plane, b: Plane) -> tuple[float, float] | None:
    """(y, z) du faîtage dans le repère caméra-centré : intersection des deux pentes dans le
    plan (Y, Z) ; None si elles sont parallèles."""
    m = np.array([[a.normal[1], a.normal[2]], [b.normal[1], b.normal[2]]], dtype=np.float64)
    if abs(np.linalg.det(m)) < 1e-6:
        return None
    y, z = np.linalg.solve(m, -np.array([a.distance, b.distance]))
    return float(y), float(z)


KIND_COLORS: dict[PlaneKind, tuple[int, int, int]] = {
    "floor": (60, 170, 80),
    "ceiling": (200, 200, 200),
    "slope": (240, 150, 40),
    "gable": (140, 70, 200),
    "side_wall": (50, 120, 220),
    "other": (120, 120, 120),
}


def planes_overlay(
    pixels: NDArray[np.uint8],
    planes: list[FittedPlane],
    cls: ClassifiedPlanes | None,
    pixel_index: NDArray[np.intp],
    stride: int,
) -> NDArray[np.uint8]:
    """Photo teintée par plan : chaque point du nuage colore son bloc stride × stride."""
    out = pixels.astype(np.float64)
    h, w = out.shape[:2]
    for i, fitted in enumerate(planes):
        kind: PlaneKind = cls.kinds[i] if cls is not None else "other"
        color = np.array(KIND_COLORS[kind], dtype=np.float64)
        us, vs = pixel_index[fitted.inliers, 0], pixel_index[fitted.inliers, 1]
        for du in range(stride):
            for dv in range(stride):
                uu, vv = np.clip(us + du, 0, w - 1), np.clip(vs + dv, 0, h - 1)
                out[vv, uu] = 0.45 * out[vv, uu] + 0.55 * color
    image: NDArray[np.uint8] = out.round().astype(np.uint8)
    return image


FAR_PERCENTILE = 97.0
"""Points les plus lointains le long du faîtage : le pignon du fond (au-delà, du bruit)."""
MIN_FAR_POINTS = 200
MIN_RIDGE_ALIGNMENT = 0.5
"""La photo doit regarder le long du faîtage (cos de l'angle) pour mesurer ainsi le pignon."""


def far_along_ridge(points: Float, axes: Float) -> float | None:
    """Distance au pignon du fond quand il n'est pas reconnu comme plan (petit dans l'image,
    encombré, éclairé à contre-jour) : les points les plus lointains dans l'axe du faîtage, du
    côté où regarde la photo. None si la photo regarde en travers du faîtage."""
    ridge = axes[0]
    forward = float(ridge[2])  # composante du faîtage sur l'axe optique (z caméra)
    if abs(forward) < MIN_RIDGE_ALIGNMENT:
        return None
    along = (points @ ridge) * (1.0 if forward > 0 else -1.0)
    ahead = along[np.isfinite(along) & (along > 0.0)]
    if len(ahead) < MIN_FAR_POINTS:
        return None
    return float(np.percentile(ahead, FAR_PERCENTILE))


def _add_residual_side_walls(
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes
) -> list[FittedPlane]:
    """KAN-45 : sol supposé et aucune jambette extraite, elles sont cherchées de chaque côté."""
    if "side_wall" in cls.kinds.values():
        return planes
    for wall in residual_side_walls(points, planes, cls, _opposite_slopes(planes, cls)):
        planes = [*planes, wall]
        i = len(planes) - 1
        cls.kinds[i] = "side_wall"
        cls.world[i] = rotate_plane(wall.plane, cls.axes)
    return planes


def _with_assumed_floor(
    estimate: AtticEstimate,
    points: Float,
    planes: list[FittedPlane],
    cls: ClassifiedPlanes,
    *,
    given: bool,
    entered: bool,
    warnings: list[str],
) -> AtticEstimate:
    """KAN-45/KAN-61 : sol supposé. Hauteur de prise de vue donnée (`given`) ou, sans rien, la
    valeur par défaut ; `entered` (KAN-62) : donnée par l'utilisateur, pas un défaut de l'app.
    Égout inconnu sans jambette, avec sa borne « au plus » (jamais 0)."""
    h = cls.floor.plane.distance
    out = estimate.model_copy(
        update={
            "camera_height": Estimate(
                value=h,
                sigma=PRIOR_REL_SIGMA * h if given else ASSUMED_FLOOR_SIGMA,
                source=Source.USER_DEFINED if entered else Source.INFERRED,
                note="sol hors champ : " + height_words(h, entered=entered),
            ),
            "floor_assumed": True,
            "eave_bound": None
            if estimate.eave_heights
            else _eave_bound(points, planes, cls, _opposite_slopes(planes, cls)),
        }
    )
    if out.eave_bound is not None:
        warnings.append(
            f"Hauteur d'égout non visible sur la photo : au plus {out.eave_bound.value:.2f} m "
            "(bas de pente le plus bas vu)."
        )
    elif not out.eave_heights:
        warnings.append("Hauteur d'égout non visible sur la photo.")
    return out


def analyse_attic(
    depth: NDArray[np.float32] | Float,
    focal_px: float,
    principal_point: tuple[float, float],
    *,
    frame_up: Float | None = None,
    seed: int = 0,
    camera_height_m: float | None = None,
    camera_height_entered: bool = True,
) -> tuple[AtticEstimate, list[FittedPlane], ClassifiedPlanes | None, NDArray[np.intp], list[str]]:
    """Cœur de l'étape, sans fichier : estimation, plans, classement, pixels, avertissements.

    `camera_height_m` (hauteur de prise de vue) ne sert que si le sol n'est pas vu : le sol est
    alors posé à cette hauteur sous la caméra (sinon à la hauteur par défaut).
    `camera_height_entered` (KAN-62) : faux quand cette hauteur est le défaut de l'app."""
    entered = camera_height_m is not None and camera_height_entered
    points, pixel_index = backproject(depth, focal_px, principal_point, stride=STRIDE)
    h, w = depth.shape
    planes = fit_planes(points, pixel_index, (w, h), seed=seed)
    warnings: list[str] = []
    floor_idx = find_floor(planes)
    assumed_floor: float | None = None
    if floor_idx is None:
        # KAN-45 : photo levée. Le sol et le bas des jambettes sortent du champ ; ce qu'il en
        # reste passe sous le seuil d'extraction. Seconde recherche, plus fine, dans ce seul cas
        # (les photos où le sol est trouvé ne changent pas).
        planes = fit_planes(
            points, pixel_index, (w, h), seed=seed, min_fraction=FALLBACK_MIN_FRACTION
        )
        floor_idx = find_floor(planes)
    if floor_idx is None:
        up = up_from_slopes(planes)
        if up is None:
            warnings.append("Aucun plan de sol trouvé dans la profondeur : combles non estimés.")
            return AtticEstimate(), planes, None, pixel_index, warnings
        # KAN-45 : photo levée, sol hors champ. Un sol sans points (repère seulement) à la
        # hauteur de prise de vue saisie, sinon par défaut ; les hauteurs en dépendent.
        assumed_floor = camera_height_m if camera_height_m is not None else DEFAULT_CAMERA_HEIGHT_M
        planes = [
            *planes,
            FittedPlane(Plane(up, assumed_floor), np.empty(0, dtype=np.intp), 0.0),
        ]
        floor_idx = len(planes) - 1
        warnings.append(
            f"Sol hors champ (photo levée) : verticale prise entre les deux pentes (toit "
            f"supposé symétrique), sol à {assumed_floor:.2f} m sous l'appareil "
            f"({'hauteur saisie' if entered else 'hauteur supposée'})."
        )
    cls = classify(planes, floor_idx)
    cls.floor_assumed = assumed_floor is not None
    if assumed_floor is not None:
        planes = _add_residual_side_walls(points, planes, cls)
    if frame_up is not None:
        disagreement = angle_between(frame_up, cls.floor.plane.normal)
        if disagreement > FRAME_DISAGREEMENT_WARN:
            warnings.append(
                f"La verticale des points de fuite et la normale du sol divergent de "
                f"{math.degrees(disagreement):.0f}° : sol ou lignes peu fiables."
            )
    estimate = summarize(planes, cls, len(points))
    if assumed_floor is not None:
        estimate = _with_assumed_floor(
            estimate,
            points,
            planes,
            cls,
            given=camera_height_m is not None,
            entered=entered,
            warnings=warnings,
        )
    if estimate.ridge_height is None:
        warnings.append("Une seule pente vue (ou aucune) : hauteur au faîtage non estimée.")
    if estimate.gable_distance is None:
        far = far_along_ridge(points, cls.axes)
        if far is not None:
            # Avant : longueur supposée de 5 m dès que le pignon n'était pas un plan net, même
            # quand la photo regardait un grenier long jusqu'à son pignon (photos d'augus).
            estimate = estimate.model_copy(
                update={
                    "gable_distance": Estimate(
                        value=far,
                        sigma=estimate_scale_sigma(far, 0.15 * far),
                        note="pignon du fond pris aux points les plus lointains dans l'axe",
                    )
                }
            )
            warnings.append(
                f"Pignon du fond non reconnu comme plan : distance prise aux points les plus "
                f"lointains dans l'axe du faîtage ({far:.1f} m)."
            )
        else:
            warnings.append("Aucun pignon vu : longueur des combles non estimée.")
    return estimate, planes, cls, pixel_index, warnings


def run_attic(
    project: Project,
    project_dir: Path,
    image_id: UUID,
    runs_dir: Path,
    *,
    estimator: DepthEstimator,
    camera_height_m: float | None = None,
    camera_height_entered: bool = True,
    run_id: UUID | None = None,
    roof_windows: RoofWindowDetector | None = None,
) -> AtticRun:
    """`camera_height_m` : hauteur de prise de vue ; si donnée, toutes les longueurs sont
    recalées dessus (l'échelle du modèle de profondeur est peu fiable sur une photo réelle),
    sinon l'échelle du modèle est gardée telle quelle. `camera_height_entered` (KAN-62) : faux
    quand c'est la valeur par défaut de l'app, pas une saisie (étiquettes et textes)."""
    entered = camera_height_entered
    started = time.perf_counter()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    camera = VanishingPointCameraEstimator().analyse(image)
    depth_map: DepthMap = estimator.predict(image)
    depth = depth_map.depth.astype(np.float32)
    frame_up = camera.frame.up if camera.frame is not None else None
    estimate, planes, cls, pixel_index, warnings = analyse_attic(
        depth,
        camera.camera.focal_px,
        camera.camera.principal_point,
        frame_up=frame_up,
        camera_height_m=camera_height_m,
        camera_height_entered=entered,
    )
    if roof_windows is not None and cls is not None and depth.shape == image.pixels.shape[:2]:
        try:
            boxes = roof_windows.detect_roof_windows(image)
        except (AdapterUnavailableError, RuntimeError) as exc:
            warnings.append(f"Fenêtres de toit non cherchées : {str(exc)[:120]}")
        else:
            sightings, notes = locate_roof_windows(
                boxes,
                planes,
                cls.kinds,
                cls.axes,
                cls.floor.plane.distance,
                camera.camera.focal_px,
                camera.camera.principal_point,
                pixel_index,
                depth.shape,
            )
            estimate = estimate.model_copy(update={"roof_windows": sightings})
            warnings.extend(notes)
    if camera_height_m is not None and estimate.camera_height is not None:
        estimate = rescale(estimate, camera_height_m, entered=entered)
        f = estimate.scale_factor
        if f > SCALE_DISAGREEMENT or f < 1.0 / SCALE_DISAGREEMENT:
            warnings.append(
                f"Le modèle de profondeur et la hauteur "
                f"{'saisie' if entered else 'supposée'} divergent d'un facteur "
                f"{max(f, 1.0 / f):.1f} : l'un des deux est faux (photo prise accroupi ou en "
                "plongée, ou échelle du modèle hors de son domaine)."
            )
    out_dir = runs_dir / str(rid) / "attic"
    out_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    png_path = out_dir / "planes.png"
    Image.fromarray(planes_overlay(image.pixels, planes, cls, pixel_index, STRIDE)).save(png_path)
    json_path = out_dir / "attic.json"
    json_path.write_text(estimate.model_dump_json(indent=2) + "\n", encoding="utf-8")
    camera_path = out_dir / "camera.json"
    camera_path.write_text(
        json.dumps(camera.camera.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
    )
    artifacts = [
        Artifact(
            name=camera_path.name,
            path=camera_path,
            media_type="application/json",
            description="Caméra estimée par les lignes (focale utilisée pour la rétro-projection)",
        ),
        Artifact(
            name=png_path.name,
            path=png_path,
            media_type="image/png",
            description="Plans ajustés : sol vert, pentes orange, pignon violet, murs bleus",
        ),
        Artifact(
            name=json_path.name,
            path=json_path,
            media_type="application/json",
            description="Estimation des combles avec incertitudes",
        ),
    ]
    assumptions = [
        Assumption(
            text=(
                f"{height_words(camera_height_m, entered=entered).capitalize()} : toutes "
                "les longueurs en dépendent (±10 % supposé)."
                if camera_height_m is not None
                else f"Échelle issue d'une profondeur apprise : ±{SCALE_SIGMA:.0%} sur toutes "
                "les longueurs, non mesuré sur photos réelles."
            ),
            impact="high",
        ),
        Assumption(
            text="Focale "
            + (
                "déduite des points de fuite."
                if camera.frame is not None and camera.frame.focal_from_vps
                else "prise dans l'EXIF ou par défaut : les largeurs en dépendent."
            ),
            impact="medium",
        ),
        Assumption(text="Pentes et pignon supposés plans et rectilignes.", impact="medium"),
    ]
    if not depth_map.metric:
        warnings.append("Profondeur relative : aucune longueur en mètres n'est valable.")
    n_found = len(
        [e for e in (estimate.ridge_height, estimate.width, estimate.gable_distance) if e]
    )
    confidence = 0.0 if estimate.camera_height is None else 0.25 + 0.1 * n_found
    if not depth_map.metric:
        confidence = min(confidence, 0.1)
    return AtticRun(
        run_id=rid,
        image_id=image_id,
        estimate=estimate,
        result=StepResult(
            step=STEP,
            confidence=confidence,
            assumptions=assumptions,
            warnings=[*camera.warnings, *warnings],
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[depth_map.model_id, camera.camera.model_id],
        ),
    )
