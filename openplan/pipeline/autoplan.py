"""Chaîne automatique à l'import (directives d'augus du 29 septembre 2026).

L'utilisateur dépose un fichier et arrive sur Éditer avec un plan construit ; il ne choisit
aucune méthode d'analyse et ne répond à aucune question. Deux entrées :

- **photo d'une pièce** : lignes de fuite → plan candidat ; profondeur (si les poids sont là)
  → plan candidat recalé sur une hauteur d'appareil supposée de 1,50 m ; combles si deux pentes
  opposées sont vues, sinon pièce à plafond plat ; le meilleur candidat selon un score de
  confiance est appliqué, l'autre est gardé en secours dans les artefacts ; puis portes et
  fenêtres (si les poids sont là).
- **image d'un plan** (reconnue à son fond clair et peu saturé) : redressement, mise en fond,
  échelle **devinée** (cotes lues par OCR si Tesseract est installé, sinon épaisseur des murs
  ≈ 20 cm, affinée par la largeur de porte standard ≈ 0,80 m, sinon taille plausible),
  vectorisation, murs `estimated`.

Tout ce qui est produit est `estimated` ou `inferred` : l'utilisateur corrige et recalibre
ensuite. Une étape impossible (poids absents, rien de vu) est dite, jamais bloquante.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.protocols import (
    DepthEstimator,
    DepthMap,
    ImageArray,
    RoofWindowDetector,
    RoomElementDetector,
)
from openplan.pipeline.attic import AtticEstimate, Estimate, run_attic
from openplan.pipeline.attic_scene import AtticGeometry, apply_attic
from openplan.pipeline.confidence import EstimateConfidence, assess, assess_plan_image
from openplan.pipeline.depthplan import run_depthplan
from openplan.pipeline.detection import detect_and_propose
from openplan.pipeline.floorplan import ACTOR, DEFAULT_CAMERA_HEIGHT_M, DetectionError
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.openings import run_openings
from openplan.pipeline.plan_import import PlanApplyBody, apply_segments, preview_image
from openplan.pipeline.reference import camera_basis, save_depth, with_basis
from openplan.scene import history
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.elements import Opening, RoofWindow, Wall
from openplan.schemas.numbers import decimal, metres
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import ConfidenceInputs, Project
from openplan.validation.invariants import validate_invariants

STEP = "autoplan"
Method = Literal["lines", "depth", "attic", "plan_image", "view", "none"]
ImageKind = Literal["photo", "plan"]
StageStatus = Literal["done", "skipped", "failed"]
Progress = Callable[[str], None]

PLAN_WHITE_FRACTION = 0.55
PLAN_MAX_SATURATION = 0.15
WALL_THICKNESS_M = 0.20
"""Épaisseur de mur supposée pour deviner l'échelle d'une image de plan."""
DOOR_WIDTH_M = 0.80
DOOR_RANGE_M = (0.5, 1.3)
PLAUSIBLE_EXTENT_M = (2.5, 60.0)
FALLBACK_EXTENT_M = 8.0
DESKEW_RANGE_DEG = (0.3, 15.0)
SCALE_TRUST = (0.5, 2.0)
"""Au-delà de ce facteur entre la hauteur d'appareil trouvée par la profondeur et 1,50 m, le
plan depuis la profondeur perd de la confiance."""
CEILING_RANGE_M = (1.8, 4.0)
LOW_CONFIDENCE = 0.5
ATTIC_SCORE = 0.6
"""Confiance affichée pour des combles déduits des pentes : géométrie complétée par des
valeurs supposées, jamais « sûre »."""
"""En dessous, le plan est annoncé « estimé, vérifiez l'échelle »."""


class AutoStage(StrictModel):
    key: str
    label: str
    status: StageStatus
    detail: str = ""


class AutoPlanRun(StrictModel):
    run_id: UUID
    image_id: UUID
    kind: ImageKind
    method: Method
    backup: Method | None = None
    """Méthode gardée en secours (son plan est dans les artefacts du run)."""
    scores: dict[str, float] = Field(default_factory=dict)
    stages: list[AutoStage] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    """À dire une fois à l'utilisateur : poids absents et leur commande, échelle devinée."""
    low_confidence: bool = False
    """Plan construit mais peu sûr : l'éditeur affiche « plan estimé, vérifiez l'échelle »
    avec le bouton Recalibrer."""
    estimate_confidence: EstimateConfidence | None = None
    """KAN-31 : niveau affiché (« fiable », « à vérifier », « approximatif ») et sa raison
    principale, dérivés de facteurs chiffrés sur le banc (pipeline/confidence.py). None quand
    rien n'a été estimé (méthode « none ») ou quand la photo est posée sur un plan existant."""
    project: Project
    result: StepResult


# ------------------------------------------------------------------ nature de l'image
def classify_image(pixels: NDArray[np.uint8]) -> ImageKind:
    """Plan dessiné (fond clair, peu de couleur, traits sombres) ou photo d'une pièce."""
    rgb = pixels[::4, ::4].astype(np.float32) / 255.0
    high = rgb.max(axis=2)
    low = rgb.min(axis=2)
    saturation = np.where(high > 1e-3, (high - low) / np.maximum(high, 1e-3), 0.0)
    paper = float(np.mean((high > 0.82) & (saturation < 0.12)))
    if paper >= PLAN_WHITE_FRACTION and float(saturation.mean()) <= PLAN_MAX_SATURATION:
        return "plan"
    return "photo"


class _MemoDepth:
    """Une seule inférence de profondeur par image, partagée entre les étapes."""

    def __init__(self, inner: DepthEstimator) -> None:
        self.inner = inner
        self.model_id = inner.model_id
        self._cache: dict[UUID, tuple[ImageArray, DepthMap]] = {}

    def predict(self, image: ImageArray) -> DepthMap:
        if image.image_id not in self._cache:
            self._cache[image.image_id] = (image, self.inner.predict(image))
        return self._cache[image.image_id][1]

    def seen(self, image_id: UUID) -> tuple[ImageArray, DepthMap] | None:
        return self._cache.get(image_id)


@dataclass
class _Candidate:
    method: Method
    project: Project
    score: float
    step: StepResult


def _walls(project: Project, level_id: UUID) -> list[Wall]:
    return [e for e in project.levels[level_id].elements.values() if isinstance(e, Wall)]


def _seen_fraction(project: Project, level_id: UUID) -> float:
    walls = _walls(project, level_id)
    if not walls:
        return 0.0
    return sum(w.source is not Source.INFERRED for w in walls) / len(walls)


def _focal_from_vps(image: ImageArray) -> bool:
    """KAN-31 : la focale vient-elle des lignes de fuite (sinon EXIF ou repli 0,8 × grand côté) ?"""
    from openplan.adapters.heuristic.camera import VanishingPointCameraEstimator

    try:
        frame = VanishingPointCameraEstimator().analyse(image).frame
    except (ValueError, RuntimeError):
        return False
    return frame is not None and frame.focal_from_vps


def _roof_detector(detector: object) -> RoofWindowDetector | None:
    """Le détecteur d'ouvertures sait-il aussi chercher les fenêtres de toit (C2) ?"""
    return detector if isinstance(detector, RoofWindowDetector) else None


def _valid(project: Project) -> bool:
    return validate_invariants(project).valid


# ------------------------------------------------------------------ combles
def _inferred(value: float, note: str) -> Estimate:
    return Estimate(value=value, sigma=max(0.5, 0.2 * value), source=Source.INFERRED, note=note)


def attic_geometry(estimate: AtticEstimate, image_id: UUID, run_id: UUID) -> AtticGeometry | None:
    """Géométrie de combles depuis l'estimation, sans rien demander : ce qui n'est pas vu est
    déduit et marqué comme tel. None sans faîtage vu (pas de combles)."""
    if estimate.ridge_height is None:
        return None
    eave = next(
        (e for e in estimate.eave_heights if e.source is Source.ESTIMATED),
        estimate.eave_heights[0] if estimate.eave_heights else None,
    )
    if estimate.length is not None:
        length = estimate.length
    elif estimate.gable_distance is not None:
        length = _inferred(
            estimate.gable_distance.value + 0.5,
            "pignon opposé non vu : supposé 0,5 m derrière l'appareil",
        )
    else:
        length = _inferred(5.0, "aucun pignon vu : longueur supposée")
    if estimate.width is not None:
        width = estimate.width
    elif estimate.usable_width is not None:
        width = _inferred(estimate.usable_width.value + 1.0, "égouts non vus")
    else:
        width = _inferred(4.0, "largeur supposée")
    bound = estimate.eave_bound
    if eave is None and bound is not None and 0.0 < bound.value < estimate.ridge_height.value:
        # KAN-61 : égout non vu (sol hors champ) : la borne tirée de la photo plutôt que 0.
        eave = _inferred(
            bound.value,
            f"égout non vu sur la photo : au plus {metres(bound.value)} (bas de pente le plus "
            "bas vu)",
        )
    unseen = (
        f"{estimate.eave_rejected} ; pentes jusqu'au sol"  # KAN-73
        if estimate.eave_rejected
        else "murs latéraux non vus : pentes jusqu'au sol"
    )
    eave_height = eave or _inferred(0.0, unseen)
    if eave_height.value >= estimate.ridge_height.value:
        eave_height = _inferred(0.0, "égout incohérent avec le faîtage : pentes jusqu'au sol")
    try:
        return AtticGeometry(
            length=length,
            width=width,
            ridge_height=estimate.ridge_height,
            eave_height=eave_height,
            camera_height=estimate.camera_height,
            gable_distance=estimate.gable_distance,
            image_id=image_id,
            run_id=run_id,
            roof_windows=estimate.roof_windows,
        )
    except ValueError:
        return None


def attic_notices(estimate: AtticEstimate, geometry: AtticGeometry) -> list[str]:
    """KAN-45/KAN-61 : ce que l'écran doit dire des combles devinés sur une photo levée (les
    avertissements de l'estimation ne remontent pas jusqu'au bandeau « Plan estimé »)."""
    if estimate.eave_rejected and not estimate.floor_assumed:
        # KAN-73 : égout trouvé puis écarté (plan voisin pris pour une jambette).
        return [
            f"Hauteur d'égout inconnue ({estimate.eave_rejected}) : pentes prolongées jusqu'au "
            "sol, à corriger dans l'inspecteur."
        ]
    if estimate.symmetric_roof and estimate.ridge_height is not None:
        # KAN-72b : faîtage et égouts tirés d'un toit supposé symétrique.
        note = estimate.ridge_height.note or "toit supposé symétrique"
        return [f"{note[:1].upper()}{note[1:]} : à corriger dans l'inspecteur s'il ne l'est pas."]
    if not estimate.floor_assumed:
        return []
    out: list[str] = []
    if estimate.camera_height is not None:
        out.append(
            f"Sol hors champ (photo prise en levant l'appareil) : verticale prise entre les deux "
            f"pentes, sol supposé {metres(estimate.camera_height.value)} sous l'appareil."
        )
    if not estimate.eave_heights:
        eave = geometry.eave_height.value
        guess = (
            f"supposée au plus {metres(eave)}" if eave > 0.0 else "pentes prolongées jusqu'au sol"
        )
        out.append(
            f"Hauteur d'égout non visible sur la photo : {guess}, à corriger dans l'inspecteur."
        )
    return out


# ------------------------------------------------------------------ image de plan
def _ink(gray: NDArray[np.uint8]) -> tuple[NDArray[np.uint8], int]:
    import cv2

    h, w = gray.shape[:2]
    ink = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15
    )
    k = max(3, round(min(h, w) / 220))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    thick = np.asarray(cv2.morphologyEx(ink, cv2.MORPH_OPEN, kernel), dtype=np.uint8)
    return thick, k


def plan_skew_rad(gray: NDArray[np.uint8]) -> float:
    """Inclinaison du dessin (repère y vers le haut), 0 s'il est droit ou trop incliné pour
    être un simple scan de travers."""
    import cv2

    thick, k = _ink(gray)
    h, w = gray.shape[:2]
    lines = cv2.HoughLinesP(
        cv2.Canny(thick, 50, 150),
        1,
        np.pi / 360,
        threshold=max(40, int(min(h, w) / 12)),
        minLineLength=max(20, int(min(h, w) / 10)),
        maxLineGap=k * 3,
    )
    if lines is None or len(lines) == 0:
        return 0.0
    angles: list[float] = []
    weights: list[float] = []
    for x0, y0, x1, y1 in np.asarray(lines).reshape(-1, 4).astype(float):
        angle = math.degrees(math.atan2(-(y1 - y0), x1 - x0))
        folded = (angle + 45.0) % 90.0 - 45.0
        angles.append(folded)
        weights.append(math.hypot(x1 - x0, y1 - y0))
    order = np.argsort(angles)
    cumulative = np.cumsum(np.asarray(weights)[order])
    median = float(np.asarray(angles)[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
    if DESKEW_RANGE_DEG[0] <= abs(median) <= DESKEW_RANGE_DEG[1]:
        return math.radians(median)
    return 0.0


def stroke_thickness_px(gray: NDArray[np.uint8]) -> tuple[float | None, float]:
    """Épaisseur médiane des traits épais (px) et plus grand côté de leur emprise (px)."""
    import cv2

    thick, _ = _ink(gray)
    ys, xs = np.nonzero(thick)
    if len(xs) == 0:
        return None, float(max(gray.shape))
    extent = float(max(int(xs.max() - xs.min()), int(ys.max() - ys.min()), 1))
    dist = cv2.distanceTransform(thick, cv2.DIST_L2, 3)
    ridge = dist[(dist >= cv2.dilate(dist, np.ones((3, 3), np.uint8)) - 1e-6) & (dist > 1.0)]
    if ridge.size == 0:
        return None, extent
    return float(2.0 * np.median(ridge)), extent


_NUMBER = re.compile(r"^(\d{1,2})[.,](\d{1,2})\s*m?$")
_CENTIMETRES = re.compile(r"^(\d{3,4})$")


def parse_dimension(text: str) -> float | None:
    """« 3,20 », « 3.2 m » → 3,2 ; « 320 » → 3,2 (centimètres) ; sinon None."""
    token = text.strip().lower().replace(" ", "")
    match = _NUMBER.match(token)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    match = _CENTIMETRES.match(token)
    if match:
        value = int(match.group(1)) / 100.0
        return value if 0.5 <= value <= 30.0 else None
    return None


PixelSegment = tuple[tuple[float, float], tuple[float, float]]
OcrToken = tuple[float, float, float]
"""(valeur en mètres, x, y) du centre du texte lu, en pixels."""


def scale_from_dimensions(tokens: list[OcrToken], segments: list[PixelSegment]) -> float | None:
    """Mètres par pixel : chaque cote lue est rapportée au trait parallèle le plus proche de
    son centre ; médiane des rapports cohérents."""
    ratios: list[float] = []
    for value, x, y in tokens:
        best: tuple[float, float] | None = None
        for (x0, y0), (x1, y1) in segments:
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 20:
                continue
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            ux, uy = (x1 - x0) / length, (y1 - y0) / length
            along = abs((x - mx) * ux + (y - my) * uy)
            across = abs(-(x - mx) * uy + (y - my) * ux)
            if along > 0.35 * length or across > max(80.0, 0.25 * length):
                continue
            if best is None or across < best[0]:
                best = (across, length)
        if best is not None:
            ratios.append(value / best[1])
    if not ratios:
        return None
    return float(np.median(ratios))


def ocr_tokens(path: Path) -> list[OcrToken]:
    """Cotes lues par Tesseract s'il est installé (binaire local, Apache-2.0) ; sinon rien."""
    binary = shutil.which("tesseract")
    if binary is None:
        return []
    try:
        out = subprocess.run(
            [binary, str(path), "stdout", "--psm", "11", "tsv"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    tokens: list[OcrToken] = []
    for line in out.splitlines()[1:]:
        cells = line.split("\t")
        if len(cells) < 12:
            continue
        value = parse_dimension(cells[11])
        if value is None:
            continue
        try:
            left, top, width, height = (float(c) for c in cells[6:10])
        except ValueError:
            continue
        tokens.append((value, left + width / 2, top + height / 2))
    return tokens


def _empty_runs(
    ink: NDArray[np.uint8], start: tuple[float, float], end: tuple[float, float]
) -> list[float]:
    """Longueurs (px) des trous sans encre le long d'une droite, bornés d'encre des deux côtés."""
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    count = int(length)
    if count < 2:
        return []
    h, w = ink.shape[:2]
    xs = np.clip(np.linspace(start[0], end[0], count).round().astype(int), 0, w - 1)
    ys = np.clip(np.linspace(start[1], end[1], count).round().astype(int), 0, h - 1)
    filled = ink[ys, xs] > 0
    inked = np.flatnonzero(filled)
    if inked.size < 2:
        return []
    runs: list[float] = []
    run = 0
    for value in filled[inked[0] : inked[-1] + 1]:
        if value:
            if run:
                runs.append(run * length / count)
            run = 0
        else:
            run += 1
    return runs


def _door_gaps_m(
    segments: list[PixelSegment], m_per_px: float, ink: NDArray[np.uint8]
) -> list[float]:
    """Trous dans un mur, en mètres à l'échelle courante : pour deux traits colinéaires, le
    trou est mesuré sur l'encre du dessin (les extrémités vectorisées sont trop imprécises)."""
    import cv2

    solid = np.asarray(cv2.dilate(ink, np.ones((3, 3), np.uint8)), dtype=np.uint8)
    gaps: list[float] = []
    for i, ((x0, y0), (x1, y1)) in enumerate(segments):
        length = math.hypot(x1 - x0, y1 - y0)
        if length < 1e-6:
            continue
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        for (a0, b0), (a1, b1) in segments[i + 1 :]:
            other = math.hypot(a1 - a0, b1 - b0)
            if other < 1e-6 or abs(ux * (a1 - a0) + uy * (b1 - b0)) / other < 0.998:
                continue
            across = [abs(-uy * (px - x0) + ux * (py - y0)) for px, py in ((a0, b0), (a1, b1))]
            if max(across) * m_per_px > 0.15:
                continue
            points = [(x0, y0), (x1, y1), (a0, b0), (a1, b1)]
            points.sort(key=lambda pt: ux * (pt[0] - x0) + uy * (pt[1] - y0))
            gaps.extend(run * m_per_px for run in _empty_runs(solid, points[0], points[-1]))
    return gaps


@dataclass
class ScaleGuess:
    m_per_px: float
    reason: str


def guess_plan_scale(
    gray: NDArray[np.uint8], tokens: list[OcrToken], segments: list[PixelSegment]
) -> ScaleGuess:
    """Échelle devinée d'une image de plan, du plus sûr au moins sûr."""
    thickness, extent = stroke_thickness_px(gray)
    lo, hi = PLAUSIBLE_EXTENT_M
    read = scale_from_dimensions(tokens, segments)
    if read is not None and lo <= read * extent <= hi:
        return ScaleGuess(read, f"{len(tokens)} cote(s) lue(s) sur le plan")
    if thickness is not None and lo <= WALL_THICKNESS_M / thickness * extent <= hi:
        scale = WALL_THICKNESS_M / thickness
        ink, _ = _ink(gray)
        doors = [
            g for g in _door_gaps_m(segments, scale, ink) if DOOR_RANGE_M[0] <= g <= DOOR_RANGE_M[1]
        ]
        if doors:
            refined = scale * DOOR_WIDTH_M / float(np.median(doors))
            if lo <= refined * extent <= hi:
                return ScaleGuess(refined, f"largeur de porte standard ≈ {metres(DOOR_WIDTH_M)}")
        return ScaleGuess(scale, "épaisseur des murs supposée ≈ 20 cm")
    return ScaleGuess(
        FALLBACK_EXTENT_M / extent,
        f"taille plausible (plus grand côté ≈ {FALLBACK_EXTENT_M:.0f} m)",
    )


def _with_background(
    project: Project, level_id: UUID, image_id: UUID, m_per_px: float, rotation: float
) -> Project:
    level = project.levels[level_id]
    new_level = level.model_copy(
        update={
            "background_image": image_id,
            "background_transform": {
                "scale": m_per_px,
                "tx": 0.0,
                "ty": 0.0,
                "rotation": rotation,
                "opacity": 0.5,
            },
        }
    )
    return project.model_copy(update={"levels": {**project.levels, level_id: new_level}})


def _pixel_segments(
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    height_px: int,
    m_per_px: float,
) -> list[PixelSegment]:
    """Traits vectorisés, ramenés en pixels de l'image (fond sans rotation ni décalage)."""
    flat = _with_background(project, level_id, image_id, m_per_px, 0.0)
    preview = preview_image(flat, project_dir, level_id, image_id)
    return [
        (
            (s.start.x / m_per_px, height_px - s.start.y / m_per_px),
            (s.end.x / m_per_px, height_px - s.end.y / m_per_px),
        )
        for s in preview.segments
    ]


# ------------------------------------------------------------------ chaîne
class _Chain:
    def __init__(self, progress: Progress | None) -> None:
        self.progress = progress
        self.stages: list[AutoStage] = []

    def say(self, text: str) -> None:
        if self.progress is not None:
            self.progress(text)

    def stage(self, key: str, label: str, status: StageStatus, detail: str = "") -> None:
        self.stages.append(AutoStage(key=key, label=label, status=status, detail=detail))


def _plan_chain(
    chain: _Chain,
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image: ImageArray,
    out_dir: Path,
    rid: UUID,
) -> tuple[Project, Method, list[str], float]:
    import cv2

    notices: list[str] = []
    if _walls(project, level_id):
        chain.stage("plan", "Image de plan", "skipped", "le niveau a déjà des murs")
        notices.append(
            "Cette image ressemble à un plan, mais le projet a déjà des murs : elle est gardée "
            "comme photo, rien n'a été modifié."
        )
        return project, "none", notices, 0.0
    gray = np.asarray(
        cv2.cvtColor(np.ascontiguousarray(image.pixels), cv2.COLOR_RGB2GRAY), dtype=np.uint8
    )
    chain.say("Redressement du plan…")
    skew = plan_skew_rad(gray)
    chain.stage(
        "deskew",
        "Redressement",
        "done",
        f"dessin tourné de {decimal(math.degrees(skew), 1)}°, redressé" if skew else "dessin droit",
    )
    chain.say("Lecture des traits et de l'échelle…")
    provisional = 4.0 / image.width
    try:
        segments = _pixel_segments(
            project, project_dir, level_id, image.image_id, image.height, provisional
        )
    except DetectionError as exc:
        chain.stage("vectorize", "Vectorisation", "failed", str(exc))
        notices.append(f"Plan non vectorisé : {exc}. L'image est mise en fond, à retracer.")
        return (
            _commit_background(project, level_id, image.image_id, provisional, -skew),
            "none",
            notices,
            0.0,
        )
    tokens = ocr_tokens(project_dir / project.images[image.image_id].path)
    guess = guess_plan_scale(gray, tokens, segments)
    chain.stage("scale", "Échelle", "done", f"devinée : {guess.reason}")
    notices.append(
        f"Échelle devinée ({guess.reason}) : vérifiez une longueur connue avec « Recalibrer »."
    )
    if not tokens and shutil.which("tesseract") is None:
        notices.append(
            "Cotes du plan non lues : Tesseract n'est pas installé (`sudo apt install "
            "tesseract-ocr`, gratuit) ; l'échelle vient de la géométrie."
        )
    chain.say("Création des murs…")
    staged = _commit_background(project, level_id, image.image_id, guess.m_per_px, -skew)
    preview = preview_image(staged, project_dir, level_id, image.image_id)
    after, step = apply_segments(
        staged,
        level_id,
        PlanApplyBody(segments=preview.segments, kind="image", source="estimated"),
        run_id=rid,
    )
    chain.stage("vectorize", "Vectorisation", "done", f"{len(preview.segments)} mur(s) estimés")
    (out_dir / "plan_scale.json").write_text(
        json.dumps(
            {"m_per_px": guess.m_per_px, "reason": guess.reason, "skew_deg": math.degrees(skew)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return after, "plan_image", notices, step.confidence


def _commit_background(
    project: Project, level_id: UUID, image_id: UUID, m_per_px: float, rotation: float
) -> Project:
    after = _with_background(project, level_id, image_id, m_per_px, rotation)
    return history.commit(project, after, actor=ACTOR, label="Mettre l'image du plan en fond")


def _photo_candidates(  # noqa: PLR0913, PLR0912
    chain: _Chain,
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    rid: UUID,
    depth: DepthEstimator | None,
    camera_height_m: float,
    roof_windows: RoofWindowDetector | None = None,
    *,
    camera_height_entered: bool,
) -> tuple[list[_Candidate], AtticEstimate | None, dict[str, float]]:
    """Les plans candidats, l'estimation des combles, et (KAN-66) les mètres par unité du
    modèle de profondeur de chaque méthode qui l'a utilisée ; la profondeur est gardée."""
    candidates: list[_Candidate] = []
    factors: dict[str, float] = {}
    chain.say("Lecture de la photo…")
    try:
        lines = detect_and_propose(
            project,
            project_dir,
            level_id,
            image_id,
            runs_dir,
            camera_height_m=camera_height_m,
            run_id=rid,
        )
        score = lines.detection.confidence * _seen_fraction(lines.project, level_id)
        if _walls(lines.project, level_id) and _valid(lines.project):
            candidates.append(_Candidate("lines", lines.project, score, lines.floorplan))
            chain.stage("lines", "Lignes de fuite", "done", f"confiance {decimal(score)}")
        else:
            chain.stage("lines", "Lignes de fuite", "failed", "aucun mur proposé")
    except (DetectionError, ValueError) as exc:
        chain.stage("lines", "Lignes de fuite", "failed", str(exc)[:160])
    if depth is None:
        chain.stage("depth", "Profondeur", "skipped", "poids absents")
        return candidates, None, factors
    memo = _MemoDepth(depth)
    estimate: AtticEstimate | None = None
    chain.say("Estimation des distances…")
    try:
        estimate = run_attic(
            project,
            project_dir,
            image_id,
            runs_dir,
            estimator=memo,
            camera_height_m=camera_height_m,
            camera_height_entered=camera_height_entered,
            run_id=rid,
            roof_windows=roof_windows,
        ).estimate
        if estimate.camera_height is not None and not estimate.floor_assumed:
            factors["attic"] = estimate.scale_factor
    except (DetectionError, ValueError) as exc:
        chain.stage("attic", "Pentes de toit", "failed", str(exc)[:160])
    chain.say("Recherche des murs…")
    try:
        run = run_depthplan(
            project,
            project_dir,
            level_id,
            image_id,
            runs_dir,
            estimator=memo,
            camera_height_m=camera_height_m,
            camera_height_entered=camera_height_entered,
            run_id=rid,
        )
        found = run.camera_height_m or 0.0
        factor = camera_height_m / found if found > 0 else 0.0
        trusted = SCALE_TRUST[0] <= factor <= SCALE_TRUST[1]
        if factor > 0:
            factors["depth"] = factor
        score = 0.6 * _seen_fraction(run.project, level_id) * (1.0 if trusted else 0.7)
        if run.walls_seen > 0 and _walls(run.project, level_id) and _valid(run.project):
            candidates.append(_Candidate("depth", run.project, score, run.result))
            chain.stage("depth", "Profondeur", "done", f"confiance {decimal(score)}")
        else:
            chain.stage("depth", "Profondeur", "failed", "aucun mur vu dans la profondeur")
    except (DetectionError, ValueError) as exc:
        chain.stage("depth", "Profondeur", "failed", str(exc)[:160])
    seen = memo.seen(image_id)
    if seen is not None and factors:
        try:
            if not save_depth(project_dir, *seen):
                factors.clear()
        except OSError:
            factors.clear()  # sans profondeur gardée, pas de mesure de référence possible
    return candidates, estimate, factors


def _ceiling(project: Project, level_id: UUID, estimate: AtticEstimate | None) -> Project:
    """Pièce à plafond plat : la hauteur vue dans la profondeur remplace le défaut."""
    if estimate is None or estimate.ceiling_height is None:
        return project
    height = estimate.ceiling_height.value
    level = project.levels[level_id]
    if not CEILING_RANGE_M[0] <= height <= CEILING_RANGE_M[1]:
        return project
    if level.height_source in (Source.MEASURED, Source.USER_DEFINED):
        return project
    new_level = level.model_copy(
        update={"height": round(height, 2), "height_source": Source.ESTIMATED}
    )
    after = project.model_copy(update={"levels": {**project.levels, level_id: new_level}})
    return history.commit(
        project, after, actor=ACTOR, label=f"Hauteur sous plafond estimée ({metres(height)})"
    )


def _openings(
    chain: _Chain,
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    rid: UUID,
    detector: RoomElementDetector | None,
) -> Project:
    if detector is None:
        chain.stage("openings", "Portes et fenêtres", "skipped", "poids absents")
        return project
    chain.say("Portes et fenêtres…")
    try:
        run = run_openings(
            project, project_dir, level_id, image_id, runs_dir, detector=detector, run_id=rid
        )
    except (DetectionError, ValueError) as exc:
        chain.stage("openings", "Portes et fenêtres", "failed", str(exc)[:160])
        return project
    if not _valid(run.project):
        chain.stage("openings", "Portes et fenêtres", "failed", "ouvertures incohérentes, écartées")
        return project
    count = sum(isinstance(e, Opening) for e in run.project.levels[level_id].elements.values())
    chain.stage("openings", "Portes et fenêtres", "done", f"{count} ouverture(s) dans le plan")
    return run.project


def run_autoplan(  # noqa: PLR0913, PLR0912, PLR0915
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    *,
    depth: DepthEstimator | None = None,
    openings: RoomElementDetector | None = None,
    notices: list[str] | None = None,
    progress: Progress | None = None,
    camera_height_m: float | None = None,
    kind: ImageKind | None = None,
    run_id: UUID | None = None,
) -> AutoPlanRun:
    """Du fichier déposé au plan construit. `notices` : ce que l'appelant sait déjà devoir
    dire (poids absents et leur commande). `kind` : lecture imposée par l'utilisateur (« ce
    n'est pas une photo », « ce n'est pas un plan ») ; sinon devinée. `camera_height_m` :
    hauteur de prise de vue saisie ; sans elle, DEFAULT_CAMERA_HEIGHT_M, dite « supposée »
    et jamais « saisie » (KAN-62)."""
    entered = camera_height_m is not None
    height = camera_height_m if camera_height_m is not None else DEFAULT_CAMERA_HEIGHT_M
    started = time.perf_counter()
    rid = run_id or uuid4()
    if level_id not in project.levels:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    out_dir = runs_dir / str(rid) / "autoplan"
    out_dir.mkdir(parents=True, exist_ok=True)
    chain = _Chain(progress)
    said = list(notices or [])
    chain.say("Lecture du fichier…")
    image = load_image(project_dir, ref)
    kind = kind or classify_image(image.pixels)
    method: Method = "none"
    backup: Method | None = None
    scores: dict[str, float] = {}
    confidence = 0.0
    trust: EstimateConfidence | None = None
    artifacts: list[Artifact] = []
    assumptions: list[Assumption] = []
    after = project

    if kind == "plan":
        after, method, plan_notices, confidence = _plan_chain(
            chain, project, project_dir, level_id, image, out_dir, rid
        )
        said.extend(plan_notices)
        if method == "plan_image":
            # « n cote(s) lue(s) sur le plan » : échelle tirée des cotes (guess_plan_scale).
            trust = assess_plan_image(any("cote(s) lue(s)" in n for n in plan_notices))
        assumptions.append(
            Assumption(
                text="Image reconnue comme un plan dessiné : traits épais vectorisés, échelle "
                "devinée. Tout est estimé, à vérifier.",
                impact="high",
            )
        )
    elif _walls(project, level_id):
        chain.say("Comparaison de la photo avec le plan…")
        try:
            view = detect_and_propose(
                project,
                project_dir,
                level_id,
                image_id,
                runs_dir,
                camera_height_m=height,
                run_id=rid,
            )
            if _valid(view.project):
                after, method, confidence = view.project, "view", view.floorplan.confidence
                chain.stage("view", "Photo confrontée au plan", "done")
            else:
                chain.stage("view", "Photo confrontée au plan", "failed", "plan incohérent")
        except (DetectionError, ValueError) as exc:
            chain.stage("view", "Photo confrontée au plan", "failed", str(exc)[:160])
        after = _openings(chain, after, project_dir, level_id, image_id, runs_dir, rid, openings)
    else:
        assumptions.append(
            Assumption(
                text=f"Hauteur de l'appareil {'saisie' if entered else 'supposée'} "
                f"{metres(height)} : toutes les distances en dépendent. « Recalibrer » sur une "
                "longueur connue corrige tout.",
                impact="high",
            )
        )
        roof_detector = _roof_detector(openings)
        candidates, estimate, factors = _photo_candidates(
            chain,
            project,
            project_dir,
            level_id,
            image_id,
            runs_dir,
            rid,
            depth,
            height,
            roof_detector,
            camera_height_entered=entered,
        )
        scores = {c.method: round(c.score, 3) for c in candidates}
        geometry = attic_geometry(estimate, image_id, rid) if estimate else None
        if geometry is not None:
            try:
                attic_project, attic_step = apply_attic(project, level_id, geometry)
                if _valid(attic_project):
                    candidates.append(_Candidate("attic", attic_project, ATTIC_SCORE, attic_step))
                    scores["attic"] = ATTIC_SCORE
                    if estimate is not None:
                        said.extend(attic_notices(estimate, geometry))
                    chain.stage("attic", "Pentes de toit", "done", "deux pentes vues : combles")
                    seen = sum(
                        isinstance(e, RoofWindow)
                        for e in attic_project.levels[level_id].elements.values()
                    )
                    if roof_detector is None:
                        chain.stage("roof_windows", "Fenêtres de toit", "skipped", "poids absents")
                    else:
                        chain.stage(
                            "roof_windows",
                            "Fenêtres de toit",
                            "done",
                            f"{seen} fenêtre(s) de toit vue(s) sur la photo",
                        )
            except (DetectionError, ValueError) as exc:
                chain.stage("attic", "Pentes de toit", "failed", str(exc)[:160])
        elif estimate is not None:
            chain.stage("attic", "Pentes de toit", "done", "aucune pente : plafond plat")
        # Deux pentes vues : les combles passent devant, quel que soit le score des murs.
        ranked = sorted(
            candidates,
            key=lambda c: (c.method == "attic", c.score, c.method == "depth"),
            reverse=True,
        )
        if ranked:
            best = ranked[0]
            after, method, confidence = best.project, best.method, min(best.score, 0.9)
            for other in ranked[1:]:
                path = out_dir / f"secours_{other.method}.json"
                path.write_text(other.project.model_dump_json(), encoding="utf-8")
                artifacts.append(
                    Artifact(
                        name=path.name,
                        path=path,
                        media_type="application/json",
                        description=(
                            f"Plan de secours ({other.method}, score {decimal(other.score)})"
                        ),
                    )
                )
            backup = ranked[1].method if len(ranked) > 1 else None
            inputs = ConfidenceInputs(
                focal_from_vps=_focal_from_vps(image),
                floor_out_of_view=estimate is not None and estimate.floor_assumed,
                eave_unseen=method == "attic"
                and estimate is not None
                and not estimate.eave_heights,
            )
            trust = assess(
                "entered" if entered else "assumed",
                camera_height_m=height if entered else None,
                focal_from_vps=inputs.focal_from_vps,
                floor_out_of_view=inputs.floor_out_of_view,
                eave_unseen=inputs.eave_unseen,
            )
            if method != "attic":
                after = _ceiling(after, level_id, estimate)
            after = _openings(
                chain, after, project_dir, level_id, image_id, runs_dir, rid, openings
            )
            # KAN-66 : l'origine de l'échelle, dite à l'utilisateur ; sans profondeur gardée
            # (facteur None), la photo est à réestimer avant de pouvoir y mesurer.
            factor = factors.get(method) or factors.get("depth") or factors.get("attic")
            basis = camera_basis(image_id, factor, height, entered=entered, inputs=inputs)
            after = with_basis(after, level_id, basis)
        else:
            said.append(
                "Aucun mur n'a pu être deviné sur cette photo (trop peu de lignes ou de "
                "profondeur) : tracez la pièce à la main, la photo reste dans le projet."
            )

    summary = out_dir / "autoplan.json"
    summary.write_text(
        json.dumps(
            {
                "kind": kind,
                "method": method,
                "backup": backup,
                "scores": scores,
                "stages": [s.model_dump() for s in chain.stages],
                "notices": said,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts.append(
        Artifact(
            name=summary.name,
            path=summary,
            media_type="application/json",
            description="Ce que la chaîne automatique a fait, étape par étape",
        )
    )
    return AutoPlanRun(
        run_id=rid,
        image_id=image_id,
        kind=kind,
        method=method,
        backup=backup,
        scores=scores,
        stages=chain.stages,
        notices=said,
        low_confidence=method != "none" and confidence < LOW_CONFIDENCE,
        estimate_confidence=trust if method != "none" else None,
        project=after,
        result=StepResult(
            step=STEP,
            confidence=max(0.0, min(1.0, confidence)),
            assumptions=assumptions,
            warnings=[f"{s.label} : {s.detail}" for s in chain.stages if s.status == "failed"],
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[m for m in (getattr(depth, "model_id", None),) if m],
        ),
    )
