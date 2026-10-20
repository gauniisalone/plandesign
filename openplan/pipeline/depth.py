"""Étape « depth.metric » : une photo → carte de profondeur en mètres (artefacts, stats).

Aucun patch de scène ici : la carte alimente l'ajustement de plans (sol, pentes, pignon) de
l'étape suivante et sert de contrôle visuel. Artefacts dans <projet>/runs/<run_id>/depth/.
"""

from __future__ import annotations

import time
from pathlib import Path
from uuid import UUID, uuid4

import numpy as np
from numpy.typing import NDArray
from pydantic import Field

from openplan.adapters.protocols import DepthEstimator, DepthMap
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.schemas.common import StrictModel
from openplan.schemas.numbers import metres
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "depth.metric"
SATURATION_WARN = 0.05
"""Part des pixels au plafond du modèle (≥ 98 % de la profondeur max) qui vaut un avertissement."""
DEPTH_CAP_M = 20.0


class DepthStats(StrictModel):
    min_m: float = Field(ge=0.0)
    p10_m: float = Field(ge=0.0)
    median_m: float = Field(ge=0.0)
    p90_m: float = Field(ge=0.0)
    max_m: float = Field(ge=0.0)
    saturated_fraction: float = Field(ge=0.0, le=1.0)


class DepthRun(StrictModel):
    run_id: UUID
    image_id: UUID
    stats: DepthStats
    result: StepResult


def depth_stats(depth: NDArray[np.float32], cap_m: float = DEPTH_CAP_M) -> DepthStats:
    finite = depth[np.isfinite(depth)]
    if finite.size == 0:
        return DepthStats(
            min_m=0.0, p10_m=0.0, median_m=0.0, p90_m=0.0, max_m=0.0, saturated_fraction=1.0
        )
    p10, p50, p90 = (float(v) for v in np.percentile(finite, [10, 50, 90]))
    return DepthStats(
        min_m=float(finite.min()),
        p10_m=p10,
        median_m=p50,
        p90_m=p90,
        max_m=float(finite.max()),
        saturated_fraction=float((finite >= 0.98 * cap_m).mean()),
    )


def depth_to_image(depth: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Visualisation : proche = clair, loin = sombre, entre les percentiles 2 et 98."""
    finite = depth[np.isfinite(depth)]
    if finite.size == 0:
        return np.zeros(depth.shape, dtype=np.uint8)
    lo, hi = np.percentile(finite, [2, 98])
    span = max(float(hi - lo), 1e-6)
    normalized = np.nan_to_num(np.clip((depth - lo) / span, 0.0, 1.0), nan=1.0)
    image: NDArray[np.uint8] = ((1.0 - normalized) * 255.0).astype(np.uint8)
    return image


def run_depth(
    project: Project,
    project_dir: Path,
    image_id: UUID,
    runs_dir: Path,
    *,
    estimator: DepthEstimator,
    run_id: UUID | None = None,
) -> DepthRun:
    started = time.perf_counter()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    depth_map: DepthMap = estimator.predict(image)
    depth = depth_map.depth.astype(np.float32)
    stats = depth_stats(depth)

    out_dir = runs_dir / str(rid) / "depth"
    out_dir.mkdir(parents=True, exist_ok=True)
    npy_path = out_dir / "depth.npy"
    np.save(npy_path, depth)
    png_path = out_dir / "depth.png"
    from PIL import Image

    Image.fromarray(depth_to_image(depth)).save(png_path, format="PNG")
    artifacts = [
        Artifact(
            name=npy_path.name,
            path=npy_path,
            media_type="application/octet-stream",
            description="Profondeur en mètres (float32, HxW)"
            if depth_map.metric
            else "Profondeur relative",
        ),
        Artifact(
            name=png_path.name,
            path=png_path,
            media_type="image/png",
            description=(
                f"Profondeur : clair = proche ({metres(stats.p10_m, 1)}), "
                f"sombre = loin ({metres(stats.p90_m, 1)})"
            ),
        ),
    ]
    assumptions = [
        Assumption(
            text="Profondeur apprise sur des intérieurs synthétiques : l'échelle est une "
            "estimation (±15 % attendu, non mesuré sur photos réelles), pas une mesure.",
            impact="high",
        )
    ]
    warnings: list[str] = []
    if not depth_map.metric:
        warnings.append("Profondeur relative seulement : aucune distance en mètres n'en découle.")
    if stats.saturated_fraction > SATURATION_WARN:
        warnings.append(
            f"{stats.saturated_fraction:.0%} des pixels au plafond du modèle "
            f"({DEPTH_CAP_M:.0f} m) : scène trop profonde ou ouverture vers l'extérieur."
        )
    return DepthRun(
        run_id=rid,
        image_id=image_id,
        stats=stats,
        result=StepResult(
            step=STEP,
            confidence=0.5 if depth_map.metric else 0.2,
            assumptions=assumptions,
            warnings=warnings,
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[depth_map.model_id],
        ),
    )
