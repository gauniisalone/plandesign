"""Étape « perception.vlm » : une photo → description structurée par un modèle vision-langage.

Aucun patch de scène : la sortie est une proposition (éléments en pixels, dimensions
estimées) que l'interface présente et que l'utilisateur applique ou ignore. Les artefacts
vont dans <projet>/runs/<run_id>/perception/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import DetectedElement, LanguageModel
from openplan.adapters.remote.vlm_detector import (
    VlmParseError,
    VlmPerception,
    VlmRoomDetector,
    parse_perception,
    to_elements,
)
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.pipeline.quality import quality_warnings
from openplan.schemas.common import StrictModel
from openplan.schemas.numbers import decimal
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "perception.vlm"
MAX_CONFIDENCE = 0.6


class PerceptionRun(StrictModel):
    run_id: UUID
    perception: VlmPerception
    elements: list[DetectedElement]
    result: StepResult


def run_vlm_perception(
    project: Project,
    project_dir: Path,
    image_id: UUID,
    runs_dir: Path,
    *,
    model: LanguageModel,
    gateway: ModelGateway,
    run_id: UUID | None = None,
) -> PerceptionRun:
    """Vérifie le consentement (passerelle), appelle le modèle, écrit les artefacts."""
    started = time.perf_counter()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    detector = VlmRoomDetector(model, gateway.bind(model, project))
    out_dir = runs_dir / str(rid) / "perception"
    out_dir.mkdir(parents=True, exist_ok=True)
    response = detector.ask(image)
    # La réponse brute est conservée dans le projet (jamais ailleurs) : c'est ce qui permet
    # de comprendre un échec d'analyse et de comparer les modèles.
    raw_path = out_dir / "vlm_raw.txt"
    raw_path.write_text(response.text, encoding="utf-8")
    try:
        perception = parse_perception(response.text)
    except VlmParseError as exc:
        msg = f"{exc} (réponse brute : {raw_path})"
        raise VlmParseError(msg) from exc
    elements = to_elements(perception, image.width, image.height, detector.model_id)

    perception_path = out_dir / "vlm_perception.json"
    perception_path.write_text(
        json.dumps(
            {
                "perception": perception.model_dump(mode="json"),
                "elements_px": [e.model_dump(mode="json") for e in elements],
                "tokens": {"input": response.input_tokens, "output": response.output_tokens},
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = [
        Artifact(
            name=raw_path.name,
            path=raw_path,
            media_type="text/plain",
            description="Réponse brute du modèle",
        ),
        Artifact(
            name=perception_path.name,
            path=perception_path,
            media_type="application/json",
            description="Réponse du modèle validée, éléments convertis en pixels",
        ),
        _overlay(out_dir / "vlm_overlay.png", image.pixels, elements),
    ]
    if perception.dropped:
        warnings_extra = [f"{perception.dropped} entrée(s) du modèle rejetée(s) par la validation."]
    else:
        warnings_extra = []
    assumptions = [
        Assumption(
            text="Éléments et dimensions déclarés par un modèle vision-langage, sans mesure : "
            "à vérifier un par un.",
            impact="high",
        )
    ]
    warnings = [*quality_warnings(ref.quality), *warnings_extra]
    if perception.notes:
        warnings.append(f"Modèle : {perception.notes[:300]}")
    if not elements:
        warnings.append("Le modèle n'a décrit aucun élément exploitable.")
    return PerceptionRun(
        run_id=rid,
        perception=perception,
        elements=elements,
        result=StepResult(
            step=STEP,
            confidence=min(MAX_CONFIDENCE, perception.room.confidence),
            assumptions=assumptions,
            warnings=warnings,
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[detector.model_id],
        ),
    )


def _overlay(path: Path, pixels: object, elements: list[DetectedElement]) -> Artifact:
    from PIL import Image, ImageDraw

    image = Image.fromarray(pixels).convert("RGB")  # type: ignore[arg-type]
    draw = ImageDraw.Draw(image)
    colors = {
        "door": (220, 60, 60),
        "window": (60, 120, 220),
        "wall": (240, 200, 40),
        "floor": (60, 180, 90),
        "ceiling": (150, 150, 150),
    }
    for e in elements:
        color = colors.get(e.category, (200, 60, 200))
        x0, y0, x1, y1 = e.bbox_px
        draw.rectangle([x0, y0, x1, y1], outline=color, width=4)
        text = f"{e.category} {decimal(e.score)}" + (f" · {e.label}" if e.label else "")
        draw.text((x0 + 4, max(0, y0 - 14)), text[:60], fill=color)
    image.save(path, format="PNG")
    return Artifact(
        name=path.name,
        path=path,
        media_type="image/png",
        description="Boîtes déclarées par le modèle (couleur par catégorie)",
    )
