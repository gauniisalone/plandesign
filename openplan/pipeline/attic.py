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
from openplan.schemas.numbers import decimal, metres
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
        return f"{decimal(self.value)} ± {metres(self.sigma)}"


class PlaneSummary(StrictModel):
    kind: PlaneKind
    normal: tuple[float, float, float]
    """Dans le repère des combles : X le long du faîtage, Y en travers, Z vers le haut."""
    distance_m: float = Field(ge=0.0)
    tilt_deg: float = Field(ge=0.0, le=180.0)
    """Angle entre la normale et la verticale : 0 = horizontal, 90 = vertical."""
    inlier_fraction: float = Field(ge=0.0, le=1.0)
    rms_m: float = Field(ge=0.0)


class RidgeProfile(StrictModel):
    """KAN-95 (lot B) : profil en travers des combles, lu sur l'enveloppe haute du nuage (le
    dessus des points, c'est-à-dire la toiture vue par en dessous) et ajusté en « Λ ». Unités
    de l'estimation (celles du modèle, puis mètres après `rescale`)."""

    ridge_y: float
    """Écart en travers entre l'appareil et le faîtage."""
    ridge_z: float = Field(gt=0.0)
    """Hauteur du faîtage au-dessus du sol."""
    pitch_deg: tuple[float, float]
    """Pente de chaque versant lue sur le profil (côté gauche, côté droit)."""
    bins: tuple[int, int]
    """Tranches d'enveloppe qui portent chaque versant (points d'appui)."""
    rms: float = Field(ge=0.0)


class PitchVote(StrictModel):
    """KAN-95 (lot B) : un plan incliné en travers du faîtage vote pour la pente du toit."""

    pitch_deg: float
    weight: float = Field(ge=0.0)
    """Part du nuage de points portée par ce plan."""


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
    eave_rejected: str | None = None
    symmetric_roof: bool = False
    """KAN-72b : faîtage et égouts tirés d'un toit supposé symétrique (pente commune), dit à
    l'écran ; faux quand les deux pentes lues diffèrent trop ou qu'une jambette manque."""
    """KAN-73 : égout trouvé puis écarté comme invraisemblable (raison affichée) ; `eave_heights`
    et `width` sont alors vides, l'égout est inconnu comme dans KAN-61."""
    ridge_profile: RidgeProfile | None = None
    """KAN-95 : profil en travers en « Λ » ; quand il est valable et qu'une pente au moins est
    vue, c'est lui qui donne `ridge_height` (sauf toit symétrique KAN-72b, plus sûr)."""
    pitch_votes: list[PitchVote] = Field(default_factory=list)
    """KAN-95 : votes de pente de tous les plans inclinés en travers du faîtage."""
    voted_pitch_deg: float | None = None
    """KAN-95 : pente votée (médiane pondérée, aberrants écartés) ; sert au recoupement de
    plusieurs photos (`attic_fusion`)."""
    along_ridge: float | None = None
    """KAN-95 : |cosinus| entre l'axe de la photo et le faîtage (1 = photo dans l'axe, qui voit
    un pignon de face ; 0 = photo de biais vers un versant)."""


EAVE_MIN_BELOW_RIDGE_M = 0.5
"""KAN-73 : un égout à moins de 0,5 m sous le faîtage n'est pas un égout (en mètres, après
recalage sur la hauteur de prise de vue)."""
EAVE_MIN_WIDTH_M = 1.5
"""KAN-73 : une largeur entre égouts sous 1,5 m n'est pas celle de combles habitables."""


def plausible_eaves(estimate: AtticEstimate) -> AtticEstimate:
    """KAN-73 : écarte des égouts invraisemblables (plan voisin pris pour une jambette, puis
    recopié de l'autre côté par symétrie). Tous les égouts et la largeur entre égouts sont
    retirés ensemble : un égout recopié du mauvais ne vaut pas mieux. À appliquer à l'échelle
    finale (les seuils sont en mètres)."""
    if not estimate.eave_heights:
        return estimate
    reason: str | None = None
    ridge = estimate.ridge_height
    if ridge is not None:
        gap = min(ridge.value - e.value for e in estimate.eave_heights)
        if gap < EAVE_MIN_BELOW_RIDGE_M:
            reason = (
                f"égout trouvé à {decimal(ridge.value - gap)} m, seulement {decimal(gap)} m sous "
                f"le faîtage : non retenu"
            )
    width = estimate.width
    if reason is None and width is not None and width.value < EAVE_MIN_WIDTH_M:
        reason = f"largeur entre égouts de {decimal(width.value)} m : non retenue"
    if reason is None:
        return estimate
    return estimate.model_copy(update={"eave_heights": [], "width": None, "eave_rejected": reason})


PRIOR_REL_SIGMA = 0.10
"""Incertitude relative attribuée à une hauteur de prise de vue saisie sans mesure."""
SCALE_DISAGREEMENT = 2.0
"""Au-delà de ce facteur entre le modèle et la hauteur saisie, l'un des deux est faux."""


def height_words(camera_height_m: float, *, entered: bool) -> str:
    """KAN-62 : « hauteur de prise de vue saisie 1,45 m » ou « … supposée 1,50 m (par défaut) »."""
    if entered:
        return f"hauteur de prise de vue saisie {metres(camera_height_m)}"
    return f"hauteur de prise de vue supposée {metres(camera_height_m)} (par défaut)"


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

    scaled_estimate = estimate.model_copy(
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
            "ridge_profile": None
            if estimate.ridge_profile is None
            else estimate.ridge_profile.model_copy(
                update={
                    "ridge_y": estimate.ridge_profile.ridge_y * factor,
                    "ridge_z": estimate.ridge_profile.ridge_z * factor,
                    "rms": estimate.ridge_profile.rms * factor,
                }
            ),
            "scale_factor": estimate.scale_factor * factor,
            "scale_note": f"Échelle recalée sur la {words} : facteur {decimal(factor)} "
            f"appliqué aux longueurs du modèle de profondeur (qui donnait "
            f"{metres(estimate.camera_height.value)}).",
        }
    )
    return plausible_eaves(scaled_estimate)  # KAN-73 : seuils en mètres, après recalage


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
        self.foot_x: dict[int, float] = {}
        """KAN-46 : abscisse (le long du faîtage) du pied d'une pente dont la jambette est
        déduite du bord du sol ; l'égout y est lu, là où la jambette est vue, pas à l'aplomb
        de la caméra (une pente légèrement gauchie y donnerait un autre égout)."""


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
    best = max(candidates, key=lambda i: planes[i].count)
    # KAN-70 : un plateau (bureau, lit) est souvent le plan horizontal le plus soutenu. Un plan
    # parallèle nettement plus bas et assez soutenu est le vrai sol.
    lower = [
        i
        for i in candidates
        if angle_between(planes[i].plane.normal, planes[best].plane.normal) <= LOWER_FLOOR_MAX_ANGLE
        and planes[i].plane.distance > planes[best].plane.distance + LOWER_FLOOR_GAP_M
        and planes[i].count >= LOWER_FLOOR_MIN_SHARE * planes[best].count
    ]
    return max(lower, key=lambda i: planes[i].plane.distance) if lower else best


LOWER_FLOOR_GAP_M = 0.4
"""KAN-70 : un plan horizontal est écarté comme sol s'il existe, plus de 0,4 m sous lui (échelle
du modèle de profondeur), un plan ou une nappe de points parallèle assez soutenu."""
LOWER_FLOOR_MAX_ANGLE = math.radians(15.0)
LOWER_FLOOR_MIN_SHARE = 0.25
"""Plan candidat plus bas : au moins le quart des points du plan le plus soutenu."""
LOWER_FLOOR_MIN_FRACTION = 0.05
"""Nappe de points plus basse (sol encombré, non extrait comme plan) : 5 % du nuage au moins."""
LOWER_FLOOR_BAND_M = 0.2
LOWER_FLOOR_MIN_ROW = 0.5
"""Nappe acceptée seulement dans la moitié basse de l'image (ligne médiane des points)."""


def lower_floor(
    points: Float,
    pixels: NDArray[np.intp],
    planes: list[FittedPlane],
    floor_idx: int,
    image_height: int,
) -> tuple[list[FittedPlane], int]:
    """KAN-70 : le sol est le plan horizontal plausible le plus bas. Le sol encombré (pieds de
    meubles, objets) n'est pas toujours extrait comme plan : on cherche, parallèlement au sol
    choisi, la nappe de points la plus basse située à plus de LOWER_FLOOR_GAP_M sous lui, assez
    soutenue et dans le bas de l'image. Trouvée, elle devient le sol (normale du plan d'origine,
    distance médiane de la nappe) ; l'ancien « sol » reste dans la liste (plateau, « other »)."""
    floor = planes[floor_idx].plane
    below = -(points @ floor.normal)
    start = floor.distance + LOWER_FLOOR_GAP_M
    deeper = below > start
    if deeper.sum() < LOWER_FLOOR_MIN_FRACTION * len(points):
        return planes, floor_idx
    minimum = LOWER_FLOOR_MIN_FRACTION * len(points)
    step = LOWER_FLOOR_BAND_M / 4
    passing: list[tuple[float, NDArray[np.intp]]] = []
    for low in np.arange(start, float(below.max()), step):
        band = np.flatnonzero((below >= low) & (below < low + LOWER_FLOOR_BAND_M))
        if (
            len(band) >= minimum
            and np.median(pixels[band, 1]) >= LOWER_FLOOR_MIN_ROW * image_height
        ):
            passing.append((float(low), band))
    if not passing:
        return planes, floor_idx
    # La nappe la plus basse ; parmi les fenêtres qui la recouvrent, la plus dense (centrée).
    deepest = passing[-1][0]
    chosen = max((b for low, b in passing if low >= deepest - LOWER_FLOOR_BAND_M), key=len)
    values = below[chosen]
    distance = float(np.median(values))
    rms = float(np.sqrt(np.mean((values - distance) ** 2)))
    new = FittedPlane(Plane(floor.normal, distance), chosen.astype(np.intp), rms)
    return [*planes, new], len(planes)


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


UPRIGHT_MIN_DEG = 0.5
UPRIGHT_MAX_DEG = 15.0
"""KAN-72a : correction de la verticale appliquée entre ces bornes ; en dessous, rien à
corriger ; au-delà, le pignon ou les lignes de fuite sont plus douteux que le sol."""


def upright_floor(
    points: Float,
    planes: list[FittedPlane],
    floor_idx: int,
    frame_up: Float | None = None,
    *,
    frame_up_reliable: bool = False,
) -> tuple[list[FittedPlane], str | None]:
    """KAN-72a : la profondeur apprise penche le sol (≈ 6° sur la scène de contrôle), d'où des
    hauteurs fausses d'autant plus que le point est loin. Un pignon est vertical : la verticale
    est la normale du sol privée de sa composante le long de la normale du pignon ; sans pignon,
    celle des points de fuite quand ils sont sûrs. Le sol garde ses points, sa distance est
    reprise à la médiane. Renvoie les plans et ce qui a été fait (None : rien)."""
    n = planes[floor_idx].plane.normal / np.linalg.norm(planes[floor_idx].plane.normal)
    cls = classify(planes, floor_idx)
    gables = [i for i, k in cls.kinds.items() if k == "gable"]
    how: str
    if gables:
        g = max(gables, key=lambda i: planes[i].count)
        gn = planes[g].plane.normal / np.linalg.norm(planes[g].plane.normal)
        up: Float = n - float(n @ gn) * gn
        how = "pignon vertical"
    elif frame_up is not None and frame_up_reliable:
        up = np.asarray(frame_up, dtype=np.float64)
        up = up if float(up @ n) >= 0 else -up
        how = "lignes de fuite"
    else:
        return planes, None
    up = up / np.linalg.norm(up)
    fix = math.degrees(math.acos(min(1.0, abs(float(up @ n)))))
    if not UPRIGHT_MIN_DEG <= fix <= UPRIGHT_MAX_DEG:
        return planes, None
    floor = planes[floor_idx]
    pts = points[floor.inliers]
    distance = float(np.median(-(pts @ up))) if len(pts) else floor.plane.distance
    out = list(planes)
    out[floor_idx] = FittedPlane(Plane(up, distance), floor.inliers, floor.rms_m)
    return out, f"verticale corrigée de {decimal(fix, 1)}° ({how})"


SYMMETRY_MAX_DIFF_DEG = 8.0
"""KAN-72b : au-delà de cet écart entre les deux pentes lues, le toit n'est pas supposé
symétrique (les deux pentes sont gardées)."""


def symmetric_roof(
    estimate: AtticEstimate, planes: list[FittedPlane], cls: ClassifiedPlanes
) -> AtticEstimate:
    """KAN-72b : le faîtage à l'intersection des deux pentes amplifie leur erreur loin du sol ;
    l'égout et la largeur, mesurés près des jambettes, sont plus sûrs. Avec deux versants
    opposés et deux jambettes vues, toit supposé symétrique : pente commune (moyenne pondérée
    par le nombre de points de chaque versant), même égout des deux côtés (leur moyenne),
    faîtage = égout + largeur / 2 × tan(pente). Dit tel quel à l'écran."""
    slopes = _opposite_slopes(planes, cls)
    eaves = estimate.eave_heights
    if (
        len(slopes) != 2
        or estimate.width is None
        or estimate.ridge_height is None
        or len(eaves) != 2
        or any(e.value <= 0.0 or e.source is Source.INFERRED for e in eaves)
    ):
        return estimate
    pitches = [_tilt_deg(cls.world[i]) for i in slopes]
    if abs(pitches[0] - pitches[1]) > SYMMETRY_MAX_DIFF_DEG:
        return estimate
    weights = [max(planes[i].count, 1) for i in slopes]
    pitch = sum(p * c for p, c in zip(pitches, weights, strict=True)) / sum(weights)
    eave = sum(e.value for e in eaves) / 2.0
    half = estimate.width.value / 2.0
    tan = math.tan(math.radians(pitch))
    ridge = eave + half * tan
    note = (
        f"toit supposé symétrique : pente commune {decimal(pitch, 1)}° (lues "
        f"{decimal(pitches[0], 1)}° et {decimal(pitches[1], 1)}°)"
    )
    sigma_eave = max(e.sigma for e in eaves)
    usable = estimate.usable_width
    if ridge > USABLE_HEIGHT_M:
        value = estimate.width.value - 2.0 * max(USABLE_HEIGHT_M - eave, 0.0) / tan
        usable = Estimate(value=max(value, 0.0), sigma=estimate.width.sigma, note=note)
    return estimate.model_copy(
        update={
            "ridge_height": estimate.ridge_height.model_copy(update={"value": ridge, "note": note}),
            "eave_heights": [
                e.model_copy(update={"value": eave, "sigma": sigma_eave, "note": note})
                for e in eaves
            ],
            "slope_pitch_deg": [pitch, pitch],
            "usable_width": usable,
            "symmetric_roof": True,
        }
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


EDGE_K_MIN = 9
EDGE_K_SHARE = 0.03
"""Bord robuste du sol : médiane des k points les plus latéraux d'un côté (k = 3 % des points
du sol de ce côté, au moins 9) ; quelques points aberrants ne le déplacent pas."""
KNEE_MIN_HEIGHT = 0.25
"""Le bas de la pente doit être au-dessus du sol d'au moins cette part de la hauteur de la
caméra (unités du modèle) : sinon la pente descend jusqu'au sol, sans jambette."""
KNEE_MAX_GAP = 0.40
"""Écart latéral maximal, en part de la hauteur de la caméra, entre le bord du sol vu et le
pied de la pente : au-delà, le sol n'est pas vu jusqu'au pied de la pente."""
KNEE_LOW_SHARE = 0.05
"""Pied de la pente : la part la plus basse de ses points."""


def floor_edge_side_walls(
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes, slopes: list[int]
) -> list[FittedPlane]:
    """KAN-46 : les jambettes, trop petites pour la recherche de plans (leurs points partent
    dans le pignon), sont déduites du bord du sol vu. Quand le sol est vu jusqu'au pied d'une
    pente qui s'arrête nettement au-dessus de lui, il y a entre les deux un mur vertical : la
    jambette, au bord du sol. Rien si le sol est supposé (KAN-61, « égout inconnu »), ni si le
    sol s'arrête loin du pied de la pente (hors champ, meuble) : la pente descend alors au sol."""
    if cls.floor_assumed:
        return []
    h = cls.floor.plane.distance
    world = points @ cls.axes.T
    floor_pts = world[cls.floor.inliers]
    floor_pts = floor_pts[np.isfinite(floor_pts).all(axis=1)]
    out: list[FittedPlane] = []
    for i in slopes:
        slope_pts = world[planes[i].inliers]
        slope_pts = slope_pts[np.isfinite(slope_pts).all(axis=1)]
        if len(slope_pts) < EDGE_K_MIN:
            continue
        side = 1.0 if float(np.median(slope_pts[:, 1])) > 0 else -1.0
        lateral = side * floor_pts[:, 1]
        lateral = lateral[lateral > 0]
        k = max(EDGE_K_MIN, int(EDGE_K_SHARE * len(lateral)))
        if len(lateral) < 2 * k:
            continue
        edge = float(np.median(np.sort(lateral)[-k:]))
        low = slope_pts[slope_pts[:, 2] <= np.quantile(slope_pts[:, 2], KNEE_LOW_SHARE)]
        foot_z = float(np.median(low[:, 2])) + h
        foot_y = float(np.median(side * low[:, 1]))
        if foot_z < KNEE_MIN_HEIGHT * h or abs(foot_y - edge) > KNEE_MAX_GAP * h:
            continue
        near = np.sort(lateral)[-k:]
        cls.foot_x[i] = float(np.median(low[:, 0]))
        normal: Float = cls.axes.T @ np.array([0.0, -side, 0.0])
        rms = float(np.std(near))
        out.append(FittedPlane(Plane(normal, edge), np.empty(0, dtype=np.intp), rms))
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
            if z is not None and slope_idx in cls.foot_x:
                z = plane.height_at(cls.foot_x[slope_idx], y_wall)
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


# ------------------------------------------------------------------ KAN-95, lot B
VOTE_TILT_DEG = (15.0, 70.0)
"""Plans qui votent pour la pente : inclinés de 15 à 70° sur l'horizontale."""
VOTE_MAX_ALONG = 0.25
"""Un plan de toit a sa normale en travers du faîtage : composante le long au plus 0,25 (un
plan oblique, coin de pignon ou objet, ne vote pas)."""
VOTE_MAD_K = 2.5
"""Rejet des aberrants : au-delà de 2,5 écarts absolus médians (normalisés) de la médiane."""
AZIMUTH_BINS = 72
PROFILE_BAND = 0.9
"""Enveloppe haute : points à plus de 0,9 × la hauteur de l'appareil au-dessus du sol (la
toiture ; l'encombrement du sol, planches et meubles, reste en dessous)."""
PROFILE_BIN = 0.05
"""Largeur d'une tranche en travers, en hauteurs d'appareil."""
PROFILE_QUANTILE = 95.0
PROFILE_MIN_BINS = 5
"""Tranches minimales par versant pour lire un « Λ »."""
PROFILE_PITCH_DEG = (10.0, 70.0)
PROFILE_MAX_RIDGE = 4.0
"""Faîtage lu au plus à 4 hauteurs d'appareil (6 m pour 1,50 m) : au-delà, le « Λ » suit du
bruit, pas une toiture."""
PROFILE_KEEP = 0.8
"""Moindres carrés tronqués : 80 % des tranches les mieux ajustées gardées (deux passes)."""
PROFILE_CANDIDATES = 120
PROFILE_JOIN_WEIGHT = 50.0
"""Poids de l'écart entre les deux droites au sommet (un seul faîtage)."""
PROFILE_NOTE = "faîtage lu sur le profil en travers (enveloppe haute en « Λ »)"


def weighted_median(values: list[float], weights: list[float]) -> float | None:
    if not values:
        return None
    v = np.asarray(values, dtype=np.float64)
    w = np.maximum(np.asarray(weights, dtype=np.float64), 0.0)
    if float(w.sum()) <= 0.0:
        w = np.ones_like(v)
    order = np.argsort(v)
    cumulative = np.cumsum(w[order])
    return float(v[order][int(np.searchsorted(cumulative, cumulative[-1] / 2.0))])


def robust_center(
    values: list[float], weights: list[float], k: float = VOTE_MAD_K, min_band: float = 0.0
) -> tuple[float | None, list[int]]:
    """Médiane pondérée après rejet des valeurs à plus de k MAD (normalisés) de la médiane
    pondérée, sans jamais rejeter à moins de `min_band` (des valeurs presque égales ne sont
    pas des aberrantes) ; renvoie aussi les indices gardés."""
    center = weighted_median(values, weights)
    if center is None:
        return None, []
    deviations = np.abs(np.asarray(values, dtype=np.float64) - center)
    mad = float(np.median(deviations))
    band = max(k * 1.4826 * mad, min_band, 1e-6 + 1e-3 * abs(center))
    kept = [i for i, d in enumerate(deviations) if d <= band]
    if not kept:
        return center, list(range(len(values)))
    return weighted_median([values[i] for i in kept], [weights[i] for i in kept]), kept


def _across_ridge(cls: ClassifiedPlanes, planes: list[FittedPlane]) -> Float | None:
    """Direction horizontale en travers du faîtage, dans le repère des combles : mode (pondéré
    par les points) de l'azimut des normales de tous les plans inclinés, angle doublé pour que
    les deux versants votent ensemble. None sans plan incliné."""
    hist = np.zeros(AZIMUTH_BINS)
    for i, plane in enumerate(planes):
        n = cls.world[i].normal
        tilt = math.degrees(math.acos(min(1.0, abs(float(n[2])))))
        if not VOTE_TILT_DEG[0] <= tilt <= VOTE_TILT_DEG[1] or plane.count == 0:
            continue
        doubled = (2.0 * math.atan2(float(n[1]), float(n[0]))) % (2.0 * math.pi)
        hist[int(doubled / (2.0 * math.pi) * AZIMUTH_BINS) % AZIMUTH_BINS] += plane.count
    if hist.sum() <= 0:
        return None
    smooth = hist + 0.5 * (np.roll(hist, 1) + np.roll(hist, -1))
    k = int(np.argmax(smooth))
    idx = [(k - 1) % AZIMUTH_BINS, k, (k + 1) % AZIMUTH_BINS]
    angles = np.array([(i + 0.5) / AZIMUTH_BINS * 2.0 * math.pi for i in idx])
    w = hist[idx]
    doubled = math.atan2(float((w * np.sin(angles)).sum()), float((w * np.cos(angles)).sum()))
    a = doubled / 2.0
    return np.array([math.cos(a), math.sin(a), 0.0])


def pitch_votes(planes: list[FittedPlane], cls: ClassifiedPlanes, total: int) -> list[PitchVote]:
    """KAN-95 : tous les plans inclinés dont la normale est en travers du faîtage votent pour la
    pente, avec pour poids leur part du nuage."""
    across = _across_ridge(cls, planes)
    if across is None:
        return []
    along = np.array([-across[1], across[0], 0.0])
    out: list[PitchVote] = []
    for i, plane in enumerate(planes):
        n = cls.world[i].normal / max(float(np.linalg.norm(cls.world[i].normal)), 1e-12)
        tilt = math.degrees(math.acos(min(1.0, abs(float(n[2])))))
        if not VOTE_TILT_DEG[0] <= tilt <= VOTE_TILT_DEG[1] or plane.count == 0:
            continue
        if abs(float(n @ along)) >= VOTE_MAX_ALONG:
            continue
        out.append(PitchVote(pitch_deg=tilt, weight=plane.count / max(total, 1)))
    return out


def voted_pitch(votes: list[PitchVote]) -> float | None:
    """Pente votée : médiane pondérée, aberrants écartés (VOTE_MAD_K)."""
    value, _ = robust_center([v.pitch_deg for v in votes], [v.weight for v in votes])
    return value


def _fit_lambda(y: Float, z: Float) -> tuple[float, float, float, float, float, int, int] | None:
    """Λ sur l'enveloppe : (y du sommet, z du sommet, tan gauche, tan droite, rms, tranches
    gauche, tranches droite) ; moindres carrés tronqués par versant, sommet cherché sur une
    grille, les deux droites devant se rejoindre au sommet."""
    best: tuple[float, float, list[tuple[float, float, float, int]]] | None = None
    for top in np.linspace(float(y.min()), float(y.max()), PROFILE_CANDIDATES)[1:-1]:
        sides: list[tuple[float, float, float, int]] = []
        residual = 0.0
        for sign in (-1.0, 1.0):
            mask = (y - top) * sign >= 0
            if int(mask.sum()) < PROFILE_MIN_BINS:
                break
            d = np.abs(y[mask] - top)
            zz = z[mask]
            keep = np.ones(len(d), dtype=bool)
            coef = np.zeros(2)
            r = np.zeros(len(d))
            for _ in range(2):
                a = np.vstack([np.ones(int(keep.sum())), -d[keep]]).T
                coef, *_ = np.linalg.lstsq(a, zz[keep], rcond=None)
                r = np.abs(zz - (coef[0] - coef[1] * d))
                keep = r <= np.quantile(r, PROFILE_KEEP)
            rms = float(np.sqrt(np.mean(r[keep] ** 2)))
            sides.append((float(coef[0]), float(coef[1]), rms, int(mask.sum())))
            residual += float(np.sum(r[keep] ** 2))
        if len(sides) < 2 or min(s[1] for s in sides) <= 0.0:
            continue
        score = residual + PROFILE_JOIN_WEIGHT * (sides[0][0] - sides[1][0]) ** 2
        if best is None or score < best[0]:
            best = (score, float(top), sides)
    if best is None:
        return None
    _, top, sides = best
    left, right = sides
    return (
        top,
        (left[0] + right[0]) / 2.0,
        left[1],
        right[1],
        math.hypot(left[2], right[2]),
        left[3],
        right[3],
    )


def ridge_profile(  # noqa: PLR0911
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes
) -> RidgeProfile | None:
    """KAN-95 : faîtage lu sur le profil en travers. Les points au-dessus de PROFILE_BAND × h
    sont projetés en travers du faîtage (direction votée par les plans inclinés) ; le haut de
    chaque tranche (95ᵉ centile) dessine la toiture, ajustée en « Λ ». None si les deux
    versants ne sont pas assez vus, ou si le « Λ » n'est pas un toit plausible (plafond plat,
    pente hors de PROFILE_PITCH_DEG, sommet sous l'appareil)."""
    if cls.floor_assumed:
        return None
    across = _across_ridge(cls, planes)
    if across is None:
        return None
    h = float(cls.floor.plane.distance)
    if h <= 0:
        return None
    world = points @ cls.axes.T
    z = world[:, 2] + h
    y = world @ across
    high = np.isfinite(z) & np.isfinite(y) & (z > PROFILE_BAND * h)
    if int(high.sum()) < PROFILE_MIN_BINS * 2:
        return None
    ys, zs = y[high], z[high]
    step = PROFILE_BIN * h
    minimum = max(5, round(len(points) / 5000))
    env_y: list[float] = []
    env_z: list[float] = []
    for start in np.arange(float(ys.min()), float(ys.max()), step):
        mask = (ys >= start) & (ys < start + step)
        if int(mask.sum()) >= minimum:
            env_y.append(float(start + step / 2.0))
            env_z.append(float(np.percentile(zs[mask], PROFILE_QUANTILE)))
    if len(env_y) < 2 * PROFILE_MIN_BINS:
        return None
    fit = _fit_lambda(np.asarray(env_y), np.asarray(env_z))
    if fit is None:
        return None
    top, ridge_z, tan_left, tan_right, rms, n_left, n_right = fit
    pitches = (math.degrees(math.atan(tan_left)), math.degrees(math.atan(tan_right)))
    if not all(PROFILE_PITCH_DEG[0] <= p <= PROFILE_PITCH_DEG[1] for p in pitches):
        return None
    if not h < ridge_z <= PROFILE_MAX_RIDGE * h:
        return None
    return RidgeProfile(
        ridge_y=top,
        ridge_z=ridge_z,
        pitch_deg=pitches,
        bins=(n_left, n_right),
        rms=rms,
    )


def _with_profile(
    estimate: AtticEstimate, points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes
) -> AtticEstimate:
    """KAN-95 (lot B) : profil, votes de pente et axe de la photo ; le faîtage du profil
    remplace celui de l'intersection des deux pentes (qui amplifie leur erreur loin du sol).
    Il ne crée pas de faîtage là où les plans n'en voyaient pas : une photo seule qui ne voit
    qu'une pente reste sans combles ; le recoupement de plusieurs photos lit son profil."""
    profile = ridge_profile(points, planes, cls)
    votes = pitch_votes(planes, cls, len(points))
    across = _across_ridge(cls, planes)
    # Axe du faîtage voté (comme le profil), ramené dans le repère de l'appareil : sa
    # composante sur l'axe optique dit si la photo regarde dans l'axe ou de biais.
    ridge_dir = (
        cls.axes.T @ np.array([-across[1], across[0], 0.0]) if across is not None else cls.axes[0]
    )
    update: dict[str, object] = {
        "ridge_profile": profile,
        "pitch_votes": votes,
        "voted_pitch_deg": voted_pitch(votes),
        "along_ridge": abs(float(ridge_dir[2])) / max(float(np.linalg.norm(ridge_dir)), 1e-12),
    }
    if profile is not None and estimate.ridge_height is not None:
        update["ridge_height"] = Estimate(
            value=profile.ridge_z,
            sigma=estimate_scale_sigma(profile.ridge_z, profile.rms),
            note=PROFILE_NOTE,
        )
    return estimate.model_copy(update=update)


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


def _add_floor_edge_side_walls(
    points: Float, planes: list[FittedPlane], cls: ClassifiedPlanes
) -> list[FittedPlane]:
    """KAN-46 : sol vu et aucune jambette extraite, elles sont déduites du bord du sol."""
    if "side_wall" in cls.kinds.values():
        return planes
    for wall in floor_edge_side_walls(points, planes, cls, _opposite_slopes(planes, cls)):
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
            f"Hauteur d'égout non visible sur la photo : au plus {metres(out.eave_bound.value)} "
            "(bas de pente le plus bas vu)."
        )
    elif not out.eave_heights:
        warnings.append("Hauteur d'égout non visible sur la photo.")
    return out


def analyse_attic(  # noqa: PLR0912
    depth: NDArray[np.float32] | Float,
    focal_px: float,
    principal_point: tuple[float, float],
    *,
    frame_up: Float | None = None,
    frame_up_reliable: bool = False,
    seed: int = 0,
    camera_height_m: float | None = None,
    camera_height_entered: bool = True,
    stride: int = STRIDE,
) -> tuple[AtticEstimate, list[FittedPlane], ClassifiedPlanes | None, NDArray[np.intp], list[str]]:
    """Cœur de l'étape, sans fichier : estimation, plans, classement, pixels, avertissements.

    `camera_height_m` (hauteur de prise de vue) ne sert que si le sol n'est pas vu : le sol est
    alors posé à cette hauteur sous la caméra (sinon à la hauteur par défaut).
    `camera_height_entered` (KAN-62) : faux quand cette hauteur est le défaut de l'app.
    `stride` (KAN-95) : un pixel sur `stride` ; plus petit pour une profondeur gardée réduite."""
    entered = camera_height_m is not None and camera_height_entered
    points, pixel_index = backproject(depth, focal_px, principal_point, stride=stride)
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
    if floor_idx is not None:
        planes, floor_idx = lower_floor(points, pixel_index, planes, floor_idx, h)
        planes, upright = upright_floor(
            points, planes, floor_idx, frame_up, frame_up_reliable=frame_up_reliable
        )
        if upright is not None:
            warnings.append(f"Profondeur apprise : {upright}.")
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
            f"supposé symétrique), sol à {metres(assumed_floor)} sous l'appareil "
            f"({'hauteur saisie' if entered else 'hauteur supposée'})."
        )
    cls = classify(planes, floor_idx)
    cls.floor_assumed = assumed_floor is not None
    if assumed_floor is not None:
        planes = _add_residual_side_walls(points, planes, cls)
    else:
        planes = _add_floor_edge_side_walls(points, planes, cls)
    if frame_up is not None:
        disagreement = angle_between(frame_up, cls.floor.plane.normal)
        if disagreement > FRAME_DISAGREEMENT_WARN:
            warnings.append(
                f"La verticale des points de fuite et la normale du sol divergent de "
                f"{math.degrees(disagreement):.0f}° : sol ou lignes peu fiables."
            )
    estimate = summarize(planes, cls, len(points))
    estimate = _with_profile(estimate, points, planes, cls)  # KAN-95, lot B
    if assumed_floor is None:
        estimate = symmetric_roof(estimate, planes, cls)
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
                f"lointains dans l'axe du faîtage ({metres(far, 1)})."
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
        frame_up_reliable=camera.frame is not None and camera.frame.focal_from_vps,
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
        if estimate.eave_rejected:
            warnings.append(f"Hauteur d'égout inconnue : {estimate.eave_rejected}.")
        f = estimate.scale_factor
        if f > SCALE_DISAGREEMENT or f < 1.0 / SCALE_DISAGREEMENT:
            warnings.append(
                f"Le modèle de profondeur et la hauteur "
                f"{'saisie' if entered else 'supposée'} divergent d'un facteur "
                f"{decimal(max(f, 1.0 / f), 1)} : l'un des deux est faux (photo prise accroupi "
                "ou en plongée, ou échelle du modèle hors de son domaine)."
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
