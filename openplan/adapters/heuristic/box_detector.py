"""Détecteur de pièce rectangulaire : arêtes sol-mur par famille de points de fuite.

Ce que ce détecteur trouve honnêtement : pour chaque famille horizontale, la ligne du bas
de mur la mieux soutenue, rétro-projetée sur le sol à une hauteur de caméra unitaire. Une
arête sol-mur sépare une couleur de la palette du sol (mesurée en bas de l'image) d'une
couleur qui n'en fait pas partie ; ce critère écarte les joints de carrelage (sol des deux
côtés) et les appuis de fenêtre (mur des deux côtés). Il ne trouve ni les murs cachés
derrière la caméra, ni les portes et fenêtres. Sa sortie est une proposition `estimated`,
jamais une mesure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

from openplan.adapters.heuristic.camera import (
    CameraAnalysis,
    ManhattanFrame,
    VanishingPointCameraEstimator,
    backproject_floor,
)
from openplan.adapters.heuristic.lines import segment_lengths
from openplan.adapters.protocols import CameraParameters, DetectedElement, ImageArray

Float = NDArray[np.float64]
Axis = Literal["x", "y"]
MODEL_ID = "heuristic_box@0.1"
CLUSTER_TOLERANCE = 0.12
"""Écart (en unités de hauteur de caméra) sous lequel deux arêtes sont la même ligne."""
MIN_SUPPORT = 0.15
"""Part minimale des segments au sol de la famille qu'une arête doit rassembler."""
MAX_DISTANCE = 30.0
"""Au-delà (en hauteurs de caméra), un point est trop près de l'horizon pour être fiable."""
MAX_SCORE = 0.6
FLOOR_STRIP = (0.90, 0.25, 0.75)
"""Zone où la palette du sol est mesurée : 10 % du bas de l'image, moitié centrale en
largeur. Hypothèse : le bas de la photo montre le sol."""
PALETTE_MAX_COLORS = 2
PALETTE_MIN_SHARE = 0.15
PALETTE_QUANT = 32
CONTRAST_OFFSET_PX = 6
CONTRAST_SAMPLES = 12
COLOR_TOLERANCE = 40.0
"""Distance RGB (0–255, euclidienne) à la palette sous laquelle une couleur est « du sol ».
Limite connue : un sol et des murs de teintes très proches (blanc sur blanc cassé) ne sont
pas séparés ; l'arête n'est alors pas proposée et l'utilisateur trace le mur."""
MIN_CONTRAST_SCORE = 0.5


@dataclass(frozen=True)
class FloorEdge:
    axis: Axis
    """Direction de l'arête dans le plan (les murs « x » sont parallèles à X)."""
    offset: float
    """Coordonnée perpendiculaire (Y pour une arête x, X pour une arête y), caméra à l'origine
    et à hauteur 1 : à multiplier par la hauteur réelle de la caméra."""
    endpoints_px: tuple[tuple[float, float], tuple[float, float]]
    weight_px: float
    support: float
    """Part des segments au sol de la famille rassemblés par cette arête, dans [0, 1]."""

    @property
    def score(self) -> float:
        return min(MAX_SCORE, 0.3 + 0.3 * self.support)


@dataclass(frozen=True)
class BoxAnalysis:
    camera: CameraParameters
    frame: ManhattanFrame | None
    segments: Float
    families: dict[str, tuple[int, ...]]
    floor_edges: list[FloorEdge]
    warnings: list[str] = field(default_factory=list)

    def edges(self, axis: Axis) -> list[FloorEdge]:
        return [e for e in self.floor_edges if e.axis == axis]

    def elements(self) -> list[DetectedElement]:
        """Arêtes sol-mur au format commun des détecteurs (pixels)."""
        out: list[DetectedElement] = []
        for edge in self.floor_edges:
            (x0, y0), (x1, y1) = edge.endpoints_px
            out.append(
                DetectedElement(
                    category="wall",
                    bbox_px=(
                        int(min(x0, x1)),
                        int(min(y0, y1)),
                        int(max(x0, x1)),
                        int(max(y0, y1)),
                    ),
                    polygon_px=[(x0, y0), (x1, y1)],
                    score=edge.score,
                    label=f"floor_wall_edge:{edge.axis}",
                    model_id=MODEL_ID,
                )
            )
        return out


class HeuristicBoxDetector:
    model_id = MODEL_ID

    def __init__(self, camera: VanishingPointCameraEstimator | None = None) -> None:
        self.camera = camera or VanishingPointCameraEstimator()

    def detect(self, image: ImageArray) -> list[DetectedElement]:
        return self.analyse(image).elements()

    def analyse(self, image: ImageArray) -> BoxAnalysis:
        analysis = self.camera.analyse(image)
        warnings = list(analysis.warnings)
        if analysis.frame is None:
            return BoxAnalysis(analysis.camera, None, analysis.segments, {}, [], warnings)
        families = analysis.frame.families()
        palette = floor_palette(image.pixels)
        contrast = edge_contrast_scores(image.pixels, analysis.segments, palette)
        edges: list[FloorEdge] = []
        for axis in ("x", "y"):
            ids = families.get(axis)
            if not ids:
                warnings.append(f"Aucune famille de lignes pour l'axe {axis} : mur non proposé.")
                continue
            found = floor_edges(analysis, axis, ids, contrast)
            if not found:
                warnings.append(f"Aucune arête sol-mur trouvée pour l'axe {axis}.")
            edges.extend(found)
        return BoxAnalysis(
            analysis.camera, analysis.frame, analysis.segments, families, edges, warnings
        )


def floor_palette(pixels: NDArray[np.uint8]) -> Float:
    """Couleurs dominantes (K, 3) de la bande basse de l'image, supposée montrer le sol.

    Quantification grossière puis bacs les plus peuplés (part ≥ PALETTE_MIN_SHARE) : deux
    tons de carrelage donnent deux couleurs, un sol uni une seule.
    """
    h, w = pixels.shape[:2]
    top, left, right = FLOOR_STRIP
    strip = pixels[int(top * h) :, int(left * w) : int(right * w)].reshape(-1, 3)
    bins = strip // PALETTE_QUANT
    _keys, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(-counts)
    colors: list[Float] = []
    for k in order[:PALETTE_MAX_COLORS]:
        if counts[k] / len(strip) < PALETTE_MIN_SHARE and colors:
            break
        colors.append(strip[inverse.ravel() == k].astype(np.float64).mean(axis=0))
    palette: Float = np.vstack(colors) if colors else np.median(strip, axis=0)[None, :]
    return palette


def edge_contrast_scores(pixels: NDArray[np.uint8], segments: Float, palette: Float) -> Float:
    """Pour chaque segment, part des échantillons où le côté bas est une couleur du sol et
    le côté haut n'en est pas : ≈ 1 pour une arête sol-mur, ≈ 0 pour un joint de carrelage."""
    if len(segments) == 0:
        return np.zeros(0)
    h, w = pixels.shape[:2]
    t = np.linspace(0.1, 0.9, CONTRAST_SAMPLES)
    p0, p1 = segments[:, None, :2], segments[:, None, 2:]
    points = p0 + (p1 - p0) * t[None, :, None]  # (N, S, 2)
    direction = segments[:, 2:] - segments[:, :2]
    normal = np.column_stack([-direction[:, 1], direction[:, 0]])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    normal[normal[:, 1] < 0] *= -1  # orientée vers le bas de l'image
    below = points + CONTRAST_OFFSET_PX * normal[:, None, :]
    above = points - CONTRAST_OFFSET_PX * normal[:, None, :]

    def is_floor(pts: Float) -> NDArray[np.bool_]:
        xs = np.clip(np.rint(pts[..., 0]).astype(int), 0, w - 1)
        ys = np.clip(np.rint(pts[..., 1]).astype(int), 0, h - 1)
        colors = pixels[ys, xs].astype(np.float64)  # (N, S, 3)
        distances = np.linalg.norm(colors[:, :, None, :] - palette[None, None, :, :], axis=-1)
        floor: NDArray[np.bool_] = distances.min(axis=-1) < COLOR_TOLERANCE
        return floor

    ok = is_floor(below) & ~is_floor(above)
    scores: Float = ok.mean(axis=1)
    return scores


def floor_edges(
    analysis: CameraAnalysis, axis: Axis, ids: tuple[int, ...], contrast: Float
) -> list[FloorEdge]:
    """Jusqu'à deux arêtes par axe : la mieux soutenue, et une seconde de l'autre côté
    de la caméra si elle est assez soutenue (les deux murs latéraux sont visibles)."""
    frame = analysis.frame
    assert frame is not None
    index = np.asarray(ids, dtype=np.intp)
    segs = analysis.segments[index]
    if len(segs) == 0:
        return []
    p0 = backproject_floor(frame, segs[:, :2], 1.0)
    p1 = backproject_floor(frame, segs[:, 2:], 1.0)
    perpendicular = 1 if axis == "x" else 0
    along = 1 - perpendicular
    valid = ~np.isnan(p0[:, 0]) & ~np.isnan(p1[:, 0])
    valid &= (np.abs(p0).max(axis=1) < MAX_DISTANCE) & (np.abs(p1).max(axis=1) < MAX_DISTANCE)
    valid &= contrast[index] >= MIN_CONTRAST_SCORE
    if not valid.any():
        return []
    offsets = (p0[valid, perpendicular] + p1[valid, perpendicular]) / 2.0
    lengths = segment_lengths(segs[valid]) * contrast[index][valid]
    total = float(lengths.sum())
    order = np.argsort(offsets)
    clusters: list[list[int]] = []
    for idx in order:
        if clusters and offsets[idx] - offsets[clusters[-1][-1]] <= CLUSTER_TOLERANCE:
            clusters[-1].append(int(idx))
        else:
            clusters.append([int(idx)])
    candidates: list[FloorEdge] = []
    valid_ids = np.flatnonzero(valid)
    for members in clusters:
        w = lengths[members]
        weight = float(w.sum())
        offset = float((offsets[members] * w).sum() / weight)
        span = np.concatenate([p0[valid_ids[members], along], p1[valid_ids[members], along]])
        pts = np.vstack([segs[valid_ids[members], :2], segs[valid_ids[members], 2:]])
        lo, hi = pts[int(np.argmin(span))], pts[int(np.argmax(span))]
        candidates.append(
            FloorEdge(
                axis=axis,
                offset=offset,
                endpoints_px=((float(lo[0]), float(lo[1])), (float(hi[0]), float(hi[1]))),
                weight_px=weight,
                support=weight / total,
            )
        )
    candidates.sort(key=lambda e: -e.weight_px)
    best = candidates[0]
    if best.support < MIN_SUPPORT:
        return []
    out = [best]
    for other in candidates[1:]:
        if other.support >= MIN_SUPPORT and other.offset * best.offset < 0:
            out.append(other)
            break
    return out
