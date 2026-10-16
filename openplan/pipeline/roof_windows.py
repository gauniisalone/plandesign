"""C2 : fenêtres de toit vues sur une photo de combles, placées sur leur pente.

Le détecteur (OWLv2, requêtes « a roof window » / « a skylight » en concurrence avec « a
window » et « a door ») rend des boîtes image. Une boîte n'est gardée que si :

- son score atteint MIN_SCORE (0,30 : essai du 4 oct. 2026 sur les photos d'augus, la photo de
  référence et nos rendus, aucune fausse alarme au-dessus de 0,26) ;
- elle couvre moins du quart de l'image (les très grandes boîtes « tout le toit ») ;
- le pourtour de la boîte appartient à une pente (plans ajustés sur la profondeur) : le vitrage
  lui-même a une profondeur de ciel, on regarde donc la bande autour.

On cherche alors sur le plan de cette pente le châssis rectangulaire dont la projection donne
exactement la boîte (`fit_rectangle`) : position le long du faîtage, côté (gauche/droite de la
visée), largeur, hauteurs basse et haute au-dessus du sol, dans le repère des combles centré
sur l'appareil. `apply_attic` en fait des `RoofWindow`
estimés, corrigeables comme les autres ; rien n'est posé quand rien n'est sûr.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.protocols import DetectedElement
from openplan.geom.planes import FittedPlane, Float, Plane
from openplan.schemas.common import StrictModel

MIN_SCORE = 0.30
MAX_AREA_FRACTION = 0.25
MIN_SLOPE_RING = 0.30
"""Part minimale des points du pourtour de la boîte qui appartiennent à une pente."""
RING_MARGIN = 0.2
"""Pourtour : la boîte agrandie de 20 % de chaque côté, moins la boîte."""
FITTED_WIDTH_M = (0.5, 1.2)
FITTED_LENGTH_M = (0.7, 1.6)
"""Tailles de châssis crédibles pour l'ajustement ; hors de là, taille standard."""
STANDARD_SIZE_M = (0.78, 0.98)
"""Velux le plus courant (MK04, 78 × 98 cm), posé quand la boîte ne donne pas une taille sûre :
la caméra estimée d'une photo réelle (focale, verticale) est souvent trop approximative pour
que la boîte soit exactement la projection d'un rectangle de la pente."""


class RoofWindowSighting(StrictModel):
    """Une fenêtre de toit vue sur la photo, repère des combles centré sur l'appareil."""

    forward_m: float
    """Centre, le long du faîtage depuis l'appareil, compté vers où regarde la photo."""
    lateral_m: float
    """Centre, en travers du faîtage ; positif à gauche de la visée."""
    width_m: float = Field(gt=0.0)
    z_bottom_m: float
    z_top_m: float
    """Hauteurs du bas et du haut du châssis au-dessus du sol."""
    score: float = Field(ge=0.0, le=1.0)
    bbox_px: tuple[int, int, int, int]
    model_id: str
    standard_size: bool = False
    """Taille non mesurée sur la photo : châssis standard (STANDARD_SIZE_M) centré sur le point
    vu ; seule la position suit alors l'échelle."""

    def scaled(self, factor: float) -> RoofWindowSighting:
        """Recalage d'échelle : positions et hauteurs suivent ; une taille standard reste
        standard (le centre du châssis suit, pas sa taille)."""
        if self.standard_size:
            center = (self.z_bottom_m + self.z_top_m) / 2.0 * factor
            half = (self.z_top_m - self.z_bottom_m) / 2.0
            return self.model_copy(
                update={
                    "forward_m": self.forward_m * factor,
                    "lateral_m": self.lateral_m * factor,
                    "z_bottom_m": center - half,
                    "z_top_m": center + half,
                }
            )
        return self.model_copy(
            update={
                "forward_m": self.forward_m * factor,
                "lateral_m": self.lateral_m * factor,
                "width_m": self.width_m * factor,
                "z_bottom_m": self.z_bottom_m * factor,
                "z_top_m": self.z_top_m * factor,
            }
        )


def _labels(
    planes: list[FittedPlane], pixel_index: NDArray[np.intp], shape: tuple[int, int]
) -> tuple[NDArray[np.int32], NDArray[np.bool_]]:
    """Indice du plan de chaque point de la grille (−1 : aucun) et masque de la grille."""
    label = np.full(shape, -1, dtype=np.int32)
    grid = np.zeros(shape, dtype=bool)
    grid[pixel_index[:, 1], pixel_index[:, 0]] = True
    for i, fitted in enumerate(planes):
        label[pixel_index[fitted.inliers, 1], pixel_index[fitted.inliers, 0]] = i
    return label, grid


def _ring_slope(
    bbox: tuple[int, int, int, int],
    label: NDArray[np.int32],
    grid: NDArray[np.bool_],
    slopes: set[int],
) -> tuple[float, int | None]:
    """(part des points du pourtour sur une pente, pente la plus représentée)."""
    h, w = label.shape
    x0, y0, x1, y1 = bbox
    mx, my = int(RING_MARGIN * (x1 - x0)), int(RING_MARGIN * (y1 - y0))
    rx0, ry0 = max(0, x0 - mx), max(0, y0 - my)
    rx1, ry1 = min(w, x1 + mx), min(h, y1 + my)
    ring = grid[ry0:ry1, rx0:rx1].copy()
    ring[max(0, y0 - ry0) : max(0, y1 - ry0), max(0, x0 - rx0) : max(0, x1 - rx0)] = False
    labels = label[ry0:ry1, rx0:rx1][ring]
    if len(labels) == 0:
        return 0.0, None
    on_slope = [int(v) for v in labels if int(v) in slopes]
    if not on_slope:
        return 0.0, None
    return len(on_slope) / len(labels), Counter(on_slope).most_common(1)[0][0]


def locate_roof_windows(
    boxes: list[DetectedElement],
    planes: list[FittedPlane],
    kinds: Mapping[int, str],
    axes: Float,
    camera_height: float,
    focal_px: float,
    principal_point: tuple[float, float],
    pixel_index: NDArray[np.intp],
    shape: tuple[int, int],
) -> tuple[list[RoofWindowSighting], list[str]]:
    """Boîtes `roof_window` → fenêtres de toit sur leur pente, et ce qui a été écarté (dit en
    clair, seulement pour les boîtes assez sûres pour qu'on s'en soucie)."""
    label, grid = _labels(planes, pixel_index, shape)
    slopes = {i for i, k in kinds.items() if k == "slope"}
    # Le repère des combles a son X le long du faîtage dans un sens arbitraire : on le compte
    # vers où regarde la photo (composante sur l'axe optique), Y suit (à gauche de la visée).
    sign = 1.0 if float(axes[0][2]) >= 0.0 else -1.0
    area = float(shape[0] * shape[1])
    out: list[RoofWindowSighting] = []
    notes: list[str] = []
    for box in sorted(boxes, key=lambda b: -b.score):
        if box.category != "roof_window" or box.score < MIN_SCORE:
            continue
        x0, y0, x1, y1 = box.bbox_px
        if (x1 - x0) * (y1 - y0) > MAX_AREA_FRACTION * area:
            notes.append(f"Fenêtre de toit écartée (score {box.score:.2f}) : boîte trop grande.")
            continue
        fraction, index = _ring_slope(box.bbox_px, label, grid, slopes)
        if index is None or fraction < MIN_SLOPE_RING:
            notes.append(
                f"Fenêtre de toit écartée (score {box.score:.2f}) : pas posée sur une pente "
                f"({fraction:.0%} du pourtour)."
            )
            continue
        # La pente qui porte le châssis est la première que rencontre le rayon du centre : la
        # pente majoritaire du pourtour peut être l'autre versant, prolongé derrière l'image.
        first = first_slope(box.bbox_px, planes, slopes, focal_px, principal_point)
        plane = planes[index if first is None else first].plane
        hit = center_on_plane(box.bbox_px, plane, axes, focal_px, principal_point)
        if hit is None:
            notes.append(f"Fenêtre de toit écartée (score {box.score:.2f}) : hors de la pente.")
            continue
        fit = fit_rectangle(box.bbox_px, plane, axes, focal_px, principal_point)
        standard = not (fit is not None and _credible(fit[1], fit[2]))
        if fit is not None and not standard:
            center, width, _length, z_bottom, z_top = fit
        else:
            # Taille non sûre : châssis standard centré sur le point vu au milieu de la boîte.
            center, rise = hit
            width, length = STANDARD_SIZE_M
            z_bottom = float(center[2] - length / 2.0 * rise[2])
            z_top = float(center[2] + length / 2.0 * rise[2])
            notes.append(
                f"Fenêtre de toit (score {box.score:.2f}) : taille standard 78 × 98 cm supposée."
            )
        z_bottom, z_top = z_bottom + camera_height, z_top + camera_height
        sighting = RoofWindowSighting(
            forward_m=float(center[0]) * sign,
            lateral_m=float(center[1]) * sign,
            width_m=width,
            z_bottom_m=z_bottom,
            z_top_m=z_top,
            score=box.score,
            bbox_px=box.bbox_px,
            model_id=box.model_id,
            standard_size=standard,
        )
        if any(_same(sighting, other) for other in out):
            continue
        out.append(sighting)
    return out, notes


FIT_TOLERANCE_PX = 4.0
"""Écart maximal entre la boîte détectée et celle du châssis ajusté (pixels)."""


def _credible(width: float, length: float) -> bool:
    return (
        FITTED_WIDTH_M[0] <= width <= FITTED_WIDTH_M[1]
        and FITTED_LENGTH_M[0] <= length <= FITTED_LENGTH_M[1]
    )


def first_slope(
    bbox: tuple[int, int, int, int],
    planes: list[FittedPlane],
    slopes: set[int],
    focal_px: float,
    principal_point: tuple[float, float],
) -> int | None:
    """Pente rencontrée en premier (distance positive la plus courte) par le rayon du centre."""
    cx, cy = principal_point
    x0, y0, x1, y1 = bbox
    ray = np.array([((x0 + x1) / 2.0 - cx) / focal_px, ((y0 + y1) / 2.0 - cy) / focal_px, 1.0])
    best: tuple[float, int] | None = None
    for i in slopes:
        plane = planes[i].plane
        denom = float(plane.normal @ ray)
        if abs(denom) < 1e-9:
            continue
        t = -plane.distance / denom
        if t > 0.0 and (best is None or t < best[0]):
            best = (t, i)
    return None if best is None else best[1]


def center_on_plane(
    bbox: tuple[int, int, int, int],
    plane: Plane,
    axes: Float,
    focal_px: float,
    principal_point: tuple[float, float],
) -> tuple[Float, Float] | None:
    """Point de la pente vu au centre de la boîte et direction « montée » de la pente (unitaire,
    vers le haut), dans le repère des combles ; None si le rayon ne coupe pas la pente devant."""
    cx, cy = principal_point
    x0, y0, x1, y1 = bbox
    ray = np.array([((x0 + x1) / 2.0 - cx) / focal_px, ((y0 + y1) / 2.0 - cy) / focal_px, 1.0])
    denom = float(plane.normal @ ray)
    if abs(denom) < 1e-9 or -plane.distance / denom <= 0.0:
        return None
    point = axes @ (ray * (-plane.distance / denom))
    normal = axes @ plane.normal
    along = np.array([1.0, 0.0, 0.0]) - normal[0] * normal
    if np.linalg.norm(along) < 1e-6:
        return None
    rise = np.cross(normal, along / np.linalg.norm(along))
    return point, (rise if rise[2] >= 0.0 else -rise)


def _project(points: Float, axes: Float, focal_px: float, cx: float, cy: float) -> Float:
    """Points du repère des combles (lignes) → pixels (u, v) ; NaN derrière l'appareil."""
    cam = points @ axes  # axes orthonormé : repère caméra = axesᵀ · monde
    z = np.where(cam[:, 2] > 1e-6, cam[:, 2], np.nan)
    out: Float = np.column_stack([cx + focal_px * cam[:, 0] / z, cy + focal_px * cam[:, 1] / z])
    return out


def fit_rectangle(
    bbox: tuple[int, int, int, int],
    plane: Plane,
    axes: Float,
    focal_px: float,
    principal_point: tuple[float, float],
) -> tuple[Float, float, float, float, float] | None:
    """Châssis rectangulaire de la pente (côtés le long du faîtage et le long de la pente) dont
    la projection a exactement la boîte détectée pour rectangle englobant : quatre inconnues
    (bords gauche/droit le long du faîtage, bas/haut le long de la pente) pour quatre bords de
    boîte, résolues par Gauss-Newton amorti autour du point vu au centre de la boîte. Rend
    (centre dans le repère des combles, largeur, longueur le long de la pente, hauteurs basse et
    haute par rapport à l'appareil), ou None si rien ne colle à quelques pixels près. Les coins
    de la boîte, eux, ne sont pas des coins du châssis : sur une pente vue de biais ils tombent
    très loin."""
    cx, cy = principal_point
    x0, y0, x1, y1 = bbox
    hit = center_on_plane(bbox, plane, axes, focal_px, principal_point)
    if hit is None:
        return None
    origin, rise = hit
    along = np.cross(rise, axes @ plane.normal)
    along = along / max(float(np.linalg.norm(along)), 1e-12)
    target = np.array([x0, x1, y0, y1], dtype=np.float64)

    def residual(p: Float) -> Float:
        a0, a1, s0, s1 = p
        corners = np.array(
            [origin + a * along + r * rise for a in (a0, a1) for r in (s0, s1)], dtype=np.float64
        )
        uv = _project(corners, axes, focal_px, cx, cy)
        if np.isnan(uv).any():
            return np.full(4, 1e6)
        got = np.array([uv[:, 0].min(), uv[:, 0].max(), uv[:, 1].min(), uv[:, 1].max()])
        out: Float = got - target
        return out

    params = np.array([-0.4, 0.4, -0.5, 0.5])
    damping = 1e-3
    current = residual(params)
    for _ in range(60):
        jac = np.empty((4, 4))
        for k in range(4):
            step = np.zeros(4)
            step[k] = 1e-4
            jac[:, k] = (residual(params + step) - current) / 1e-4
        try:
            delta = np.linalg.solve(jac.T @ jac + damping * np.eye(4), -jac.T @ current)
        except np.linalg.LinAlgError:
            return None
        trial = params + delta
        if trial[1] - trial[0] > 0.05 and trial[3] - trial[2] > 0.05:
            new = residual(trial)
            if float(np.abs(new).max()) < float(np.abs(current).max()):
                params, current, damping = trial, new, max(damping / 3.0, 1e-6)
                continue
        damping *= 4.0
        if damping > 1e6:
            break
    if float(np.abs(current).max()) > FIT_TOLERANCE_PX:
        return None
    a0, a1, s0, s1 = (float(v) for v in params)
    center = origin + (a0 + a1) / 2.0 * along + (s0 + s1) / 2.0 * rise
    z_low = float(origin[2] + s0 * rise[2])
    z_high = float(origin[2] + s1 * rise[2])
    return center, a1 - a0, s1 - s0, z_low, z_high


def _same(a: RoofWindowSighting, b: RoofWindowSighting) -> bool:
    """Deux boîtes pour le même châssis : même côté, centres à moins d'une demi-largeur."""
    return (a.lateral_m > 0) == (b.lateral_m > 0) and abs(a.forward_m - b.forward_m) < 0.5 * max(
        a.width_m, b.width_m
    )
