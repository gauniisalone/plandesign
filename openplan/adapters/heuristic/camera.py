"""Repère Manhattan et pose de caméra à partir des points de fuite.

Repère caméra : x à droite, y vers le bas, z vers l'avant (pixels). Repère plan : X et Y
le long des deux familles horizontales de murs, Z vers le haut. `axes` donne, en lignes,
les vecteurs X, Y, Z du plan exprimés dans le repère caméra.

Focale : si deux points de fuite orthogonaux sont finis, f² = −(v1 − c)·(v2 − c) avec c le
point principal (centre de l'image). Sinon, la focale a priori (EXIF ou défaut) est gardée.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.exif_camera import ExifCameraEstimator
from openplan.adapters.heuristic.lines import detect_segments, segment_lengths
from openplan.adapters.heuristic.vanishing import VanishingPoint, find_vanishing_points
from openplan.adapters.protocols import CameraEstimator, CameraParameters, ImageArray

Float = NDArray[np.float64]
MODEL_ID = "vp_camera@0.1"
VERTICAL_MIN_Y = 0.8
"""|composante y| minimale d'une direction pour être la verticale (pitch ≤ ~37°)."""
HORIZONTAL_MAX_Y = 0.5
FOCAL_RANGE = (0.35, 1.6)
"""Focale plausible, en fraction du grand côté de l'image : d'un ultra grand-angle de téléphone
(≈ 0,4) à un zoom ×2 (≈ 1,6). Jusqu'au 4 oct. 2026 la borne haute était 3,0 : sous des pentes,
les lignes convergentes passaient pour des familles orthogonales et donnaient 2,5 à 3 fois le
grand côté (champ de 20°), d'où des combles aplatis de 13 à 31 m de long sur la photo de
référence et nos rendus (essai C2)."""
ORTHOGONALITY_WARN = math.radians(10.0)
MAX_CONFIDENCE = 0.6


@dataclass(frozen=True)
class ManhattanFrame:
    focal_px: float
    principal_point: tuple[float, float]
    axes: Float
    """3×3, lignes = X, Y, Z (haut) du plan dans le repère caméra."""
    focal_from_vps: bool
    vertical: VanishingPoint | None
    horizontal: tuple[VanishingPoint, ...]
    """Une ou deux familles ; la première définit l'axe X du plan."""
    warnings: list[str] = field(default_factory=list)

    @property
    def up(self) -> Float:
        up: Float = self.axes[2]
        return up

    @property
    def pitch(self) -> float:
        """Positif quand la caméra regarde vers le haut."""
        return math.asin(float(np.clip(self.up[2], -1.0, 1.0)))

    @property
    def roll(self) -> float:
        return math.atan2(float(self.up[0]), float(-self.up[1]))

    @property
    def yaw_hint(self) -> float:
        """Azimut de la visée dans le plan, mesuré depuis l'axe X, antihoraire vu du dessus."""
        forward = np.array([0.0, 0.0, 1.0])
        horizontal = forward - float(forward @ self.up) * self.up
        horizontal /= max(np.linalg.norm(horizontal), 1e-12)
        e_x = self.axes[0]
        return math.atan2(float(self.up @ np.cross(e_x, horizontal)), float(e_x @ horizontal))

    def families(self) -> dict[str, tuple[int, ...]]:
        out: dict[str, tuple[int, ...]] = {"x": self.horizontal[0].inliers}
        if len(self.horizontal) > 1:
            out["y"] = self.horizontal[1].inliers
        if self.vertical is not None:
            out["z"] = self.vertical.inliers
        return out


def vp_direction(
    vp: VanishingPoint, focal_px: float, principal_point: tuple[float, float]
) -> Float:
    """Direction unitaire (repère caméra) d'un point de fuite, orientée vers l'avant."""
    x, y, w = vp.homogeneous
    cx, cy = principal_point
    if abs(w) < 1e-6:
        d = np.array([x, y, 0.0])
    else:
        d = np.array([(x / w - cx) / focal_px, (y / w - cy) / focal_px, 1.0])
    d = d / np.linalg.norm(d)
    if d[2] < 0 or (d[2] == 0 and d[0] < 0):
        d = -d
    direction: Float = d
    return direction


def _focal_from_pair(
    a: VanishingPoint, b: VanishingPoint, principal_point: tuple[float, float], long_side: float
) -> float | None:
    pa, pb = a.point, b.point
    if pa is None or pb is None:
        return None
    cx, cy = principal_point
    f2 = -((pa[0] - cx) * (pb[0] - cx) + (pa[1] - cy) * (pb[1] - cy))
    if f2 <= 0:
        return None
    f = math.sqrt(f2)
    if not FOCAL_RANGE[0] * long_side <= f <= FOCAL_RANGE[1] * long_side:
        return None
    return f


def manhattan_frame(
    vps: list[VanishingPoint],
    focal_prior_px: float,
    principal_point: tuple[float, float],
    long_side: float,
) -> ManhattanFrame | None:
    """Repère plan à partir des points de fuite ; None si l'information est insuffisante.

    Il faut au moins une famille horizontale et, en plus, soit la verticale, soit une seconde
    famille horizontale (la verticale est alors leur produit vectoriel).
    """
    if not vps:
        return None
    directions = [vp_direction(vp, focal_prior_px, principal_point) for vp in vps]
    vertical_idx = max(
        (i for i, d in enumerate(directions) if abs(d[1]) >= VERTICAL_MIN_Y),
        key=lambda i: abs(directions[i][1]),
        default=None,
    )
    horizontal_idx = sorted(
        (
            i
            for i, d in enumerate(directions)
            if i != vertical_idx and abs(d[1]) <= HORIZONTAL_MAX_Y
        ),
        key=lambda i: -vps[i].weight,
    )[:2]
    if not horizontal_idx or (vertical_idx is None and len(horizontal_idx) < 2):
        return None

    focal = focal_prior_px
    focal_from_vps = False
    pairs: list[tuple[int, int]] = []
    if len(horizontal_idx) == 2:
        pairs.append((horizontal_idx[0], horizontal_idx[1]))
    if vertical_idx is not None:
        pairs.extend((vertical_idx, h) for h in horizontal_idx)
    # La paire la mieux soutenue (poids minimal le plus élevé) donne la focale la plus fiable.
    pairs.sort(key=lambda ij: -min(vps[ij[0]].weight, vps[ij[1]].weight))
    for i, j in pairs:
        estimate = _focal_from_pair(vps[i], vps[j], principal_point, long_side)
        if estimate is not None:
            focal, focal_from_vps = estimate, True
            break

    warnings: list[str] = []
    horizontal = [vp_direction(vps[i], focal, principal_point) for i in horizontal_idx]
    if vertical_idx is not None:
        up = vp_direction(vps[vertical_idx], focal, principal_point)
    else:
        up = np.cross(horizontal[0], horizontal[1])
        up /= max(np.linalg.norm(up), 1e-12)
    if up[1] > 0:
        up = -up
    e_x = horizontal[0] - float(horizontal[0] @ up) * up
    e_x /= max(np.linalg.norm(e_x), 1e-12)
    e_y = np.cross(up, e_x)
    if len(horizontal) == 2:
        deviation = math.acos(min(1.0, abs(float(horizontal[1] @ e_y))))
        if deviation > ORTHOGONALITY_WARN:
            warnings.append(
                f"Les deux familles horizontales ne sont pas orthogonales "
                f"({math.degrees(deviation):.0f}°) : pièce non rectangulaire ou lignes parasites."
            )
    return ManhattanFrame(
        focal_px=focal,
        principal_point=principal_point,
        axes=np.vstack([e_x, e_y, up]),
        focal_from_vps=focal_from_vps,
        vertical=vps[vertical_idx] if vertical_idx is not None else None,
        horizontal=tuple(vps[i] for i in horizontal_idx),
        warnings=warnings,
    )


def backproject_floor(frame: ManhattanFrame, points_px: Float, camera_height: float) -> Float:
    """Coordonnées plan (N, 2) des pixels vus sur le sol, NaN pour ceux au-dessus de l'horizon.

    La caméra est à l'origine du plan, à `camera_height` au-dessus du sol.
    """
    cx, cy = frame.principal_point
    rays = np.column_stack(
        [
            (points_px[:, 0] - cx) / frame.focal_px,
            (points_px[:, 1] - cy) / frame.focal_px,
            np.ones(len(points_px)),
        ]
    )
    world = rays @ frame.axes.T
    out = np.full((len(points_px), 2), np.nan)
    below = world[:, 2] < -1e-3
    t = -camera_height / world[below, 2]
    out[below] = world[below, :2] * t[:, None]
    result: Float = out
    return result


def horizon_line(frame: ManhattanFrame) -> tuple[float, float, float]:
    """Droite d'horizon (a, b, c) en pixels : a·u + b·v + c = 0."""
    cx, cy = frame.principal_point
    ux, uy, uz = (float(v) for v in frame.up)
    return (ux, uy, uz * frame.focal_px - ux * cx - uy * cy)


@dataclass(frozen=True)
class CameraAnalysis:
    camera: CameraParameters
    frame: ManhattanFrame | None
    segments: Float
    vanishing_points: list[VanishingPoint]
    warnings: list[str]


class VanishingPointCameraEstimator:
    """Estime focale, tangage, roulis et lacet relatif aux murs à partir des lignes."""

    model_id = MODEL_ID

    def __init__(self, prior: CameraEstimator | None = None) -> None:
        self.prior = prior or ExifCameraEstimator()

    def estimate(self, image: ImageArray) -> CameraParameters:
        return self.analyse(image).camera

    def analyse(self, image: ImageArray) -> CameraAnalysis:
        prior = self.prior.estimate(image)
        segments = detect_segments(image.pixels)
        vps = find_vanishing_points(segments)
        frame = manhattan_frame(
            vps, prior.focal_px, prior.principal_point, float(max(image.width, image.height))
        )
        points = [vp.point for vp in vps if vp.point is not None]
        warnings: list[str] = []
        if frame is None:
            warnings.append(
                f"Points de fuite insuffisants ({len(vps)} trouvé(s) sur {len(segments)} "
                "segments) : orientation de la caméra non estimée."
            )
            camera = prior.model_copy(update={"vanishing_points_px": points})
            return CameraAnalysis(camera, None, segments, vps, warnings)
        warnings.extend(frame.warnings)
        total = float(segment_lengths(segments).sum()) or 1.0
        support = sum(vp.weight for vp in vps) / total
        confidence = min(MAX_CONFIDENCE, 0.25 + 0.35 * min(1.0, support))
        camera = CameraParameters(
            focal_px=frame.focal_px,
            principal_point=frame.principal_point,
            pitch=frame.pitch,
            roll=frame.roll,
            yaw_hint=frame.yaw_hint,
            vanishing_points_px=points,
            confidence=confidence,
            model_id=self.model_id,
        )
        return CameraAnalysis(camera, frame, segments, vps, warnings)
