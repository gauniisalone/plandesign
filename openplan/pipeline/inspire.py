"""Sortie B d'une vue (V1, décision PO du 6 octobre) : l'image d'inspiration.

A reste la référence (rendu Blender + finition légère, cotes tenues). B refait l'image par
la diffusion, guidée fortement par la profondeur et les arêtes du rendu : la géométrie et les
meubles restent à leur place (essai A/B du 6 octobre), l'allure devient celle d'une photo
d'intérieur de catalogue. B est toujours `generated` et marquée **« Inspiration · cotes non
garanties »**, sur l'image elle-même et dans ses métadonnées. L'image Blender et la finition
restent à côté.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import numpy as np
from pydantic import Field

if TYPE_CHECKING:
    from PIL import ImageFont

from openplan.adapters.protocols import ImageInspirer, InspirationMode, InspirationRequest
from openplan.pipeline.finish import NEGATIVE, depth_control, edges_of, finish_prompt
from openplan.schemas.common import Source, StrictModel
from openplan.schemas.scene import Project

INSPIRATION = "inspiration.png"
INSPIRATION_SIDECAR = "inspiration.json"
LABEL = "Inspiration · cotes non garanties"
CATALOGUE = "catalogue.png"
CATALOGUE_SIDECAR = "catalogue.json"
CATALOGUE_LABEL = "Inspiration · matières libres"
CATALOGUE_OPTIONS_MODE: InspirationMode = "txt2img"
"""« Version catalogue » (V1.7b) : à la demande seulement, jamais dans le chemin automatique ;
txt2img guidé à 1,2 : allure la plus « catalogue », matières libres (essai du 6 octobre)."""
STAMP_SHARE = 1 / 25
"""Hauteur du texte du bandeau : 1/25 de la hauteur de l'image au moins (PO, 6 octobre)."""
DEFAULT_MODE: InspirationMode = "img2img"
DEFAULT_STRENGTH = 0.8
DEFAULT_SCALE = 1.2
"""Réglage retenu après l'essai du 6 octobre : img2img depuis la couleur Blender à 0,8,
profondeur et arêtes à 1,2. Il tient les matières (écart de couleur moyen ΔE 6 à 7 contre 16
à 26 en txt2img) et les arêtes, avec une allure plus photo que la finition ; le txt2img,
plus « catalogue » mais infidèle aux matières, reste disponible (`mode`)."""


class InspirationOptions(StrictModel):
    mode: InspirationMode = DEFAULT_MODE
    strength: float = Field(default=DEFAULT_STRENGTH, ge=0.5, le=1.0)
    control_scale: float = Field(default=DEFAULT_SCALE, ge=0.5, le=1.5)
    seed: int = Field(default=0, ge=0)


class InspirationEntry(StrictModel):
    run_id: UUID
    image: str = INSPIRATION
    source: Source = Source.GENERATED
    label: str = LABEL
    mode: InspirationMode
    strength: float | None
    """img2img seulement."""
    control_scale: float
    prompt: str
    seed: int
    model_ids: list[str]
    duration_s: float
    peak_vram_mb: float | None = None
    created_at: datetime
    note: str = (
        "Image refaite par un modèle d'images, guidée par la profondeur et les arêtes du rendu "
        "des cotes : allure de photo d'intérieur, cotes et matières non garanties."
    )


STAMP_FONTS = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)
"""Polices avec accents (« contextualisé », « estimées ») ; sinon la police par défaut."""


def _stamp_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    from PIL import ImageFont

    for path in STAMP_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow ancien : police par défaut sans taille
        return ImageFont.load_default()


def stamp(pixels: np.ndarray, text: str = LABEL) -> np.ndarray:
    """Marque l'image elle-même : un bandeau lisible en bas à gauche, texte au moins 1/25 de
    la hauteur, sur fond noir semi-opaque."""
    from PIL import Image, ImageDraw

    image = Image.fromarray(pixels).convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    size = max(14, round(image.height * STAMP_SHARE))
    font = _stamp_font(size)
    box = draw.textbbox((0, 0), text, font=font)
    pad = size // 2
    w, h = box[2] - box[0] + 2 * pad, box[3] - box[1] + 2 * pad
    y = image.height - h - pad
    draw.rectangle((pad, y, pad + w, y + h), fill=(0, 0, 0, 170))
    draw.text((2 * pad, y + pad - box[1]), text, fill=(255, 255, 255, 235), font=font)
    return np.asarray(image, dtype=np.uint8)


def inspire_render(
    project: Project,
    render_dir: Path,
    run_id: UUID,
    options: InspirationOptions,
    *,
    inspirer: ImageInspirer,
    catalogue: bool = False,
) -> InspirationEntry:
    """Écrit `inspiration.png` (marquée) et `inspiration.json` à côté du rendu Blender, qui
    reste ; même prompt que la finition, dérivé de l'ambiance de la pièce. `catalogue` :
    la « Version catalogue » à la demande (`catalogue.png`, « matières libres »)."""
    from PIL import Image

    image_name, sidecar, label = (
        (CATALOGUE, CATALOGUE_SIDECAR, CATALOGUE_LABEL)
        if catalogue
        else (INSPIRATION, INSPIRATION_SIDECAR, LABEL)
    )

    with Image.open(render_dir / "preview.png") as img:
        pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
    depth_npy = render_dir / "depth_z.npy"
    depth = depth_control(np.load(depth_npy)) if depth_npy.is_file() else None
    prompt = finish_prompt(project, None)
    result = inspirer.inspire(
        InspirationRequest(
            prompt=prompt,
            negative_prompt=NEGATIVE,
            image=pixels,
            depth=depth,
            edges=edges_of(pixels),
            mode=options.mode,
            strength=options.strength,
            control_scale=options.control_scale,
            seed=options.seed,
        )
    )
    Image.fromarray(stamp(result.pixels, label)).save(render_dir / image_name)
    entry = InspirationEntry(
        run_id=run_id,
        image=image_name,
        label=label,
        mode=options.mode,
        strength=options.strength if options.mode == "img2img" else None,
        control_scale=options.control_scale,
        prompt=prompt,
        seed=options.seed,
        model_ids=result.model_ids,
        duration_s=round(result.duration_s, 1),
        peak_vram_mb=result.peak_vram_mb,
        created_at=datetime.now(UTC),
    )
    if catalogue:
        entry = entry.model_copy(
            update={
                "note": "Version catalogue demandée : image refaite librement (txt2img), "
                "guidée par la profondeur et les arêtes ; matières et cotes non garanties."
            }
        )
    (render_dir / sidecar).write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return entry


def read_inspiration(render_dir: Path, *, catalogue: bool = False) -> InspirationEntry | None:
    image_name, sidecar = (
        (CATALOGUE, CATALOGUE_SIDECAR) if catalogue else (INSPIRATION, INSPIRATION_SIDECAR)
    )
    path = render_dir / sidecar
    if not path.is_file() or not (render_dir / image_name).is_file():
        return None
    try:
        return InspirationEntry.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return None
