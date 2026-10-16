"""Étape « inspiration.generate » : photo → profondeur → image d'inspiration locale.

Opt-in (bouton), local (rien ne quitte la machine), et toujours marquée `generated` : l'image
suit la structure de la photo par sa carte de profondeur, mais n'est ni une photo retouchée
ni un rendu des cotes. Artefacts dans <projet>/runs/<run_id>/inspiration/.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import Field

from openplan.adapters.protocols import DepthEstimator, ImageGenerationRequest, ImageGenerator
from openplan.pipeline.depth import depth_to_image
from openplan.pipeline.ingestion import ImageNotFoundError, load_image
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.results import Artifact, Assumption, StepResult
from openplan.schemas.scene import Project

STEP = "inspiration.generate"
DEFAULT_PROMPT = (
    "photorealistic interior photograph, renovated room, warm natural light, "
    "clean finishes, high quality, detailed"
)
"""Le modèle (CLIP anglais) répond mieux à l'anglais ; le texte de l'utilisateur est ajouté
tel quel, dans n'importe quelle langue."""
NEGATIVE_PROMPT = "blurry, deformed, distorted geometry, text, watermark, people, low quality"


class InspirationOptions(StrictModel):
    prompt: str = ""
    """Texte libre ajouté à la consigne par défaut (style, matières, usage de la pièce)."""
    seed: int = Field(default=0, ge=0)
    steps: int = Field(default=20, ge=1, le=60)
    max_side: int = Field(default=640, ge=256, le=1024)


class InspirationRun(StrictModel):
    run_id: UUID
    image_id: UUID
    prompt: str
    seed: int
    generation_s: float
    peak_vram_mb: float | None
    source: Source = Source.GENERATED
    result: StepResult


def run_inspiration(
    project: Project,
    project_dir: Path,
    image_id: UUID,
    runs_dir: Path,
    *,
    estimator: DepthEstimator,
    generator: ImageGenerator,
    options: InspirationOptions | None = None,
    run_id: UUID | None = None,
) -> InspirationRun:
    started = time.perf_counter()
    opts = options or InspirationOptions()
    rid = run_id or uuid4()
    ref = project.images.get(image_id)
    if ref is None:
        msg = f"image inconnue : {image_id}"
        raise ImageNotFoundError(msg)
    image = load_image(project_dir, ref)
    depth = estimator.predict(image)
    control = depth_to_image(depth.depth)
    prompt = f"{DEFAULT_PROMPT}, {opts.prompt.strip()}" if opts.prompt.strip() else DEFAULT_PROMPT
    generated = generator.generate(
        ImageGenerationRequest(
            prompt=prompt,
            negative_prompt=NEGATIVE_PROMPT,
            control=control,
            seed=opts.seed,
            steps=opts.steps,
            max_side=opts.max_side,
        )
    )
    out_dir = runs_dir / str(rid) / "inspiration"
    out_dir.mkdir(parents=True, exist_ok=True)
    from PIL import Image

    image_path = out_dir / "inspiration.png"
    Image.fromarray(generated.pixels).save(image_path, format="PNG")
    control_path = out_dir / "control_depth.png"
    Image.fromarray(control).save(control_path, format="PNG")
    prompt_path = out_dir / "prompt.json"
    prompt_path.write_text(
        json.dumps(
            {
                "prompt": prompt,
                "negative_prompt": NEGATIVE_PROMPT,
                "seed": generated.seed,
                "steps": opts.steps,
                "models": generated.model_ids,
                "source": Source.GENERATED.value,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    artifacts = [
        Artifact(
            name=image_path.name,
            path=image_path,
            media_type="image/png",
            description="Image d'inspiration générée (source generated), ne respecte pas les cotes",
        ),
        Artifact(
            name=control_path.name,
            path=control_path,
            media_type="image/png",
            description="Structure suivie : profondeur de la photo (proche = clair)",
        ),
        Artifact(
            name=prompt_path.name,
            path=prompt_path,
            media_type="application/json",
            description="Consigne, graine et modèles utilisés",
        ),
    ]
    assumptions = [
        Assumption(
            text="Image générée par un modèle de diffusion local : une inspiration qui suit la "
            "structure de la photo, pas une photo retouchée ni un rendu des dimensions.",
            impact="high",
        ),
        Assumption(
            text="Filtre de sécurité du modèle non chargé (poids non téléchargés) : le résultat "
            "n'est pas filtré ; il reste sur la machine.",
            impact="medium",
        ),
    ]
    warnings: list[str] = []
    if not depth.metric:
        warnings.append("Profondeur relative : la structure suivie est approximative.")
    return InspirationRun(
        run_id=rid,
        image_id=image_id,
        prompt=prompt,
        seed=generated.seed,
        generation_s=generated.duration_s,
        peak_vram_mb=generated.peak_vram_mb,
        result=StepResult(
            step=STEP,
            confidence=0.2,
            assumptions=assumptions,
            warnings=warnings,
            artifacts=artifacts,
            duration_s=time.perf_counter() - started,
            model_ids=[depth.model_id, *generated.model_ids],
        ),
    )
