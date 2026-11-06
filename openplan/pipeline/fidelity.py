"""KAN-154 (étape 2, squelette) : le rendu respecte-t-il la géométrie exacte de sa vue ?

`check_fidelity(rendu, guides)` passe le rendu (RGB, à la taille des guides) à une liste de
mesures ENFICHABLES (protocole `Measure`) et rend le verdict : conforme si chaque mesure faite
est dans son seuil. Premières mesures :

- `edge_f1` : F1 des arêtes du rendu (Canny) contre `guides.edges`, à `EDGE_TOLERANCE_PX` près ;
- `edge_scale` : l'échelle qui aligne le mieux les arêtes du rendu sur celles de la scène
  (balayage autour du centre de l'image), écart |s − 1| : un rendu « zoomé » de 5 % y lit 5 % ;
- `openings_iou` : les portes et fenêtres vues par le détecteur (OWLv2) contre les rectangles
  projetés des ouvertures (masques door / window des guides), IoU moyenne.

La mesure par élément de la TMA se branchera ici (même protocole). Les SEUILS sont provisoires :
ils seront calibrés par le banc de fidélité (KAN-153) avant d'allumer OPENPLAN_FIDELITY_CHECK.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage  # type: ignore[import-untyped]

from openplan.adapters.protocols import ImageArray, RoomElementDetector
from openplan.pipeline.guides import OPENING_KINDS, ViewGuides
from openplan.schemas.fidelity import MAX_ATTEMPTS, FidelityVerdict, MeasureResult

FLAG = "OPENPLAN_FIDELITY_CHECK"
"""Éteint par défaut : le poste ne contrôle ni ne relance (seuils à calibrer d'abord)."""
EDGE_TOLERANCE_PX = 3.0
"""Une arête du rendu à 3 px au plus d'une arête de la scène compte (méthode du doc 11)."""
MIN_EDGE_F1 = 0.30
"""PROVISOIRE (banc KAN-153) : F1 des arêtes en deçà duquel le rendu n'est pas conforme."""
MAX_SCALE_ERROR = 0.025
"""PROVISOIRE (banc KAN-153) : écart d'échelle |s − 1| admis (la directive vise ±5 % en tout)."""
SCALE_RANGE = 0.08
SCALE_STEP = 0.0025
MIN_OPENING_IOU = 0.5
"""PROVISOIRE (banc KAN-153) : IoU moyenne des ouvertures en deçà de laquelle c'est refusé."""
MIN_OPENING_PX = 400
"""Une ouverture vue sur moins de pixels (coupée par le cadre, au loin) n'est pas mesurée."""
SEED_STEP = 7919
"""Écart entre les graines des relances (premier tirage + 2 au plus)."""


class Measure(Protocol):
    """Une mesure enfichable (la mesure par élément de la TMA suivra ce protocole)."""

    @property
    def name(self) -> str: ...

    def measure(self, render: NDArray[np.uint8], guides: ViewGuides) -> MeasureResult: ...


def render_edges(render: NDArray[np.uint8]) -> NDArray[np.bool_]:
    from openplan.pipeline.finish import edges_of

    return np.asarray(edges_of(render) > 0)


def _distance_to(edges: NDArray[np.bool_]) -> NDArray[np.float64]:
    """Distance (px) de chaque pixel à l'arête la plus proche (inf sans arête)."""
    if not edges.any():
        return np.full(edges.shape, np.inf)
    out: NDArray[np.float64] = ndimage.distance_transform_edt(~edges)
    return out


def edge_f1(found: NDArray[np.bool_], truth: NDArray[np.bool_], tolerance: float) -> float:
    if not found.any() or not truth.any():
        return 0.0
    precision = float((_distance_to(truth)[found] <= tolerance).mean())
    recall = float((_distance_to(found)[truth] <= tolerance).mean())
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def best_scale(found: NDArray[np.bool_], truth: NDArray[np.bool_]) -> float:
    """L'échelle s (autour du centre de l'image) qui rapproche le plus les arêtes du rendu de
    celles de la scène : distance moyenne tronquée, balayée de 1 − 8 % à 1 + 8 %."""
    h, w = truth.shape
    ys, xs = np.nonzero(found)
    if len(xs) == 0 or not truth.any():
        return 1.0
    dist = np.minimum(_distance_to(truth), 4 * EDGE_TOLERANCE_PX)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    best, best_cost = 1.0, np.inf
    for s in np.arange(1 - SCALE_RANGE, 1 + SCALE_RANGE + 1e-9, SCALE_STEP):
        # Un rendu à l'échelle s montre en (x, y) ce que la scène montre en cx + (x − cx) / s.
        px = np.clip(np.rint(cx + (xs - cx) / s), 0, w - 1).astype(int)
        py = np.clip(np.rint(cy + (ys - cy) / s), 0, h - 1).astype(int)
        cost = float(dist[py, px].mean())
        if cost < best_cost - 1e-12:
            best, best_cost = float(s), cost
    return best


@dataclass(frozen=True)
class EdgeF1:
    name: str = "edge_f1"
    tolerance_px: float = EDGE_TOLERANCE_PX
    minimum: float = MIN_EDGE_F1

    def measure(self, render: NDArray[np.uint8], guides: ViewGuides) -> MeasureResult:
        f1 = edge_f1(render_edges(render), guides.edges > 0, self.tolerance_px)
        return MeasureResult(
            name=self.name, value=round(f1, 4), threshold=self.minimum, ok=f1 >= self.minimum
        )


@dataclass(frozen=True)
class EdgeScale:
    name: str = "edge_scale"
    maximum: float = MAX_SCALE_ERROR

    def measure(self, render: NDArray[np.uint8], guides: ViewGuides) -> MeasureResult:
        error = abs(best_scale(render_edges(render), guides.edges > 0) - 1.0)
        return MeasureResult(
            name=self.name, value=round(error, 4), threshold=self.maximum, ok=error <= self.maximum
        )


def projected_openings(guides: ViewGuides) -> list[tuple[str, tuple[int, int, int, int]]]:
    """(classe, rectangle x0, y0, x1, y1) de chaque porte ou fenêtre assez visible."""
    out: list[tuple[str, tuple[int, int, int, int]]] = []
    for index, kind in enumerate(guides.kinds):
        if kind not in OPENING_KINDS:
            continue
        ys, xs = np.nonzero(guides.element == index)
        if len(xs) < MIN_OPENING_PX:
            continue
        out.append((kind, (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)))
    return out


@dataclass(frozen=True)
class OpeningsIoU:
    detector: RoomElementDetector | None
    name: str = "openings_iou"
    minimum: float = MIN_OPENING_IOU

    def measure(self, render: NDArray[np.uint8], guides: ViewGuides) -> MeasureResult:
        from uuid import uuid4

        from openplan.adapters.neural.openings import box_iou

        projected = projected_openings(guides)
        if self.detector is None or not projected:
            return MeasureResult(name=self.name, threshold=self.minimum)  # non mesurée
        found = self.detector.detect(ImageArray(image_id=uuid4(), pixels=render))
        boxes = {
            k: [d.bbox_px for d in found if d.category == ("window" if k == "roof_window" else k)]
            for k, _ in projected
        }
        scores = [max((box_iou(rect, b) for b in boxes[k]), default=0.0) for k, rect in projected]
        mean = float(np.mean(scores))
        return MeasureResult(
            name=self.name, value=round(mean, 4), threshold=self.minimum, ok=mean >= self.minimum
        )


def default_measures(detector: RoomElementDetector | None = None) -> list[Measure]:
    return [EdgeF1(), EdgeScale(), OpeningsIoU(detector)]


def check_fidelity(
    render: NDArray[np.uint8],
    guides: ViewGuides,
    measures: Sequence[Measure] | None = None,
    *,
    attempts: int = 1,
) -> FidelityVerdict:
    """Le verdict de `render` (H×W×3, à la taille des guides) contre la géométrie `guides`."""
    if render.shape[:2] != guides.edges.shape:
        msg = f"rendu {render.shape[:2]} ≠ guides {guides.edges.shape} : même taille attendue"
        raise ValueError(msg)
    results = [m.measure(render, guides) for m in (measures or default_measures())]
    failed = [r.name for r in results if r.ok is False]
    return FidelityVerdict(ok=not failed, failed=failed, measures=results, attempts=attempts)


def enabled() -> bool:
    return os.environ.get(FLAG, "").strip() == "1"


@dataclass
class Checked:
    """Le tirage gardé, et son verdict."""

    seed: int
    verdict: FidelityVerdict
    seeds: list[int] = field(default_factory=list)


def render_checked(
    render_once: Callable[[int], None],
    check: Callable[[int], FidelityVerdict],
    seed: int,
) -> Checked:
    """La boucle du poste : un tirage, son contrôle ; hors seuil, une autre graine, deux fois au
    plus. Le dernier tirage reste en place ; non conforme, le verdict le dit (`ok` faux)."""
    seeds: list[int] = []
    verdict: FidelityVerdict | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        current = seed + (attempt - 1) * SEED_STEP
        seeds.append(current)
        render_once(current)
        verdict = check(attempt)
        if verdict.ok:
            break
    assert verdict is not None
    return Checked(seed=seeds[-1], verdict=verdict, seeds=seeds)
