"""Étape « detection » : image → segments, points de fuite, caméra, arêtes sol-mur.

Produit des artefacts inspectables dans <projet>/runs/<run_id>/detection/ : les segments
et familles (JSON), la caméra estimée (JSON) et une image de superposition. Enchaîne avec
l'étape floorplan pour proposer un rectangle.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from uuid import UUID, uuid4

from openplan.adapters.heuristic.box_detector import BoxAnalysis, HeuristicBoxDetector
from openplan.adapters.heuristic.camera import horizon_line
from openplan.pipeline.floorplan import DEFAULT_CAMERA_HEIGHT_M, DetectionError, propose_floorplan
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.multiview import add_view
from openplan.pipeline.quality import quality_warnings
from openplan.schemas.common import StrictModel
from openplan.schemas.elements import Wall
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "detection.heuristic_box"
FAMILY_COLORS = {"x": (220, 40, 40), "y": (40, 160, 60), "z": (40, 90, 220)}


class DetectionRun(StrictModel):
    run_id: UUID
    project: Project
    detection: StepResult
    floorplan: StepResult


def run_detection(
    project: Project, project_dir: Path, image_id: UUID, out_dir: Path
) -> tuple[BoxAnalysis, StepResult]:
    """Analyse une image du projet et écrit les artefacts dans `out_dir`."""
    started = time.perf_counter()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    analysis = HeuristicBoxDetector().analyse(image)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = [
        _write_json(
            out_dir / "segments.json",
            {
                "segments": analysis.segments.round(2).tolist(),
                "families": {k: list(v) for k, v in analysis.families.items()},
                "floor_edges": [
                    {
                        "axis": e.axis,
                        "offset_per_camera_height": e.offset,
                        "endpoints_px": e.endpoints_px,
                        "support": e.support,
                    }
                    for e in analysis.floor_edges
                ],
            },
            "Segments LSD, familles de points de fuite, arêtes sol-mur",
        ),
        _write_json(
            out_dir / "camera.json",
            analysis.camera.model_dump(mode="json"),
            "Caméra estimée (focale, tangage, roulis, lacet relatif aux murs)",
        ),
        _write_overlay(out_dir / "overlay.png", image.pixels, analysis),
    ]
    assumptions = [
        Assumption(
            text="Lignes dominantes supposées alignées sur trois axes orthogonaux.", impact="medium"
        )
    ]
    if analysis.frame is not None and not analysis.frame.focal_from_vps:
        assumptions.append(
            Assumption(
                text="Focale prise dans l'EXIF ou par défaut (points de fuite trop lointains).",
                impact="medium",
            )
        )
    return analysis, StepResult(
        step=STEP,
        confidence=analysis.camera.confidence,
        assumptions=assumptions,
        warnings=[*quality_warnings(ref.quality), *analysis.warnings],
        artifacts=artifacts,
        duration_s=time.perf_counter() - started,
        model_ids=[analysis.camera.model_id, HeuristicBoxDetector.model_id],
    )


def detect_and_propose(
    project: Project,
    project_dir: Path,
    level_id: UUID,
    image_id: UUID,
    runs_dir: Path,
    *,
    camera_height_m: float = DEFAULT_CAMERA_HEIGHT_M,
    run_id: UUID | None = None,
) -> DetectionRun:
    """Détection puis, dans un run identifié : proposition de plan si le niveau est vide,
    confrontation à la scène (`multiview.add_view`) s'il a déjà des murs."""
    rid = run_id or uuid4()
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise DetectionError(msg)
    analysis, detection = run_detection(
        project, project_dir, image_id, runs_dir / str(rid) / "detection"
    )
    step = (
        add_view if any(isinstance(e, Wall) for e in level.elements.values()) else propose_floorplan
    )
    after, floorplan = step(
        project, level_id, analysis, image_id=image_id, camera_height_m=camera_height_m, run_id=rid
    )
    return DetectionRun(run_id=rid, project=after, detection=detection, floorplan=floorplan)


def _write_json(path: Path, data: object, description: str) -> Artifact:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return Artifact(
        name=path.name, path=path, media_type="application/json", description=description
    )


def _write_overlay(path: Path, pixels: object, analysis: BoxAnalysis) -> Artifact:
    """Segments en gris, familles en couleur, arêtes sol-mur en jaune épais, horizon en magenta."""
    from PIL import Image, ImageDraw

    image = Image.fromarray(pixels).convert("RGB")  # type: ignore[arg-type]
    draw = ImageDraw.Draw(image)
    family_of: dict[int, str] = {}
    for name, ids in analysis.families.items():
        for i in ids:
            family_of[i] = name
    for i, (x0, y0, x1, y1) in enumerate(analysis.segments.tolist()):
        color = FAMILY_COLORS.get(family_of.get(i, ""), (150, 150, 150))
        draw.line([(x0, y0), (x1, y1)], fill=color, width=2 if i in family_of else 1)
    if analysis.frame is not None:
        a, b, c = horizon_line(analysis.frame)
        w = image.width
        if abs(b) > 1e-9:
            draw.line([(0, -c / b), (w, -(a * w + c) / b)], fill=(220, 40, 200), width=2)
        elif abs(a) > 1e-9:
            draw.line([(-c / a, 0), (-c / a, image.height)], fill=(220, 40, 200), width=2)
    for edge in analysis.floor_edges:
        draw.line(list(edge.endpoints_px), fill=(255, 210, 0), width=5)
    for vp in analysis.camera.vanishing_points_px:
        x, y = vp
        if math.isfinite(x) and math.isfinite(y) and abs(x) < 1e5 and abs(y) < 1e5:
            draw.ellipse([x - 6, y - 6, x + 6, y + 6], outline=(255, 255, 255), width=2)
    image.save(path, format="PNG")
    return Artifact(
        name=path.name,
        path=path,
        media_type="image/png",
        description="Superposition : segments, familles, horizon, arêtes sol-mur",
    )
