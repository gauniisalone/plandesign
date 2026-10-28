"""Rendu Z-Image d'une vue depuis la scène seule (KAN-88), point d'entrée du worker de rendu
(KAN-89) : bake + caméra (+ projet pour le prompt) → image, sans Blender ni réseau.

Contrat convenu avec le PO le 22 oct. 2026 :
- `render_scene(bake_path, camera_path, out_png, *, project_path=None, prompt=None)` ;
- les arêtes de guidage sont calculées ici depuis le bake et la caméra (`guides`) ;
- `prompt=None` : description dérivée de la scène (KAN-86), il faut alors `project_path`
  (catégories des meubles, pièce, ambiance) ; sans l'un ni l'autre, ValueError ;
- `ZImageUnavailable` si un poids manque (avec la commande `models pull` à lancer),
  `ZImageVramGuard` si le garde-fou arrête le calcul (au-delà de 5 000 Mio de carte, ou faute
  de mémoire vive, ou trop long) : le processus de génération est tué, rien ne gèle ;
- aucun accès réseau (voir `adapters.neural.zimage`)."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import numpy as np

from openplan.adapters.neural.siglip import SigLipStyleScorer
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT
from openplan.adapters.neural.zimage import ZImageFinisher, ZImageStoppedError, z_size
from openplan.adapters.protocols import AdapterUnavailableError, FinishRequest, ImageArray
from openplan.pipeline import style_words as sw
from openplan.pipeline.finish import finish_prompt
from openplan.pipeline.guides import view_guides
from openplan.pipeline.scene_prompt import SceneWords, scene_words
from openplan.pipeline.style_words import MAX_IMAGES
from openplan.scene.migrate import load_project
from openplan.schemas.bake import Bake, RenderCamera
from openplan.schemas.common import DECODED_IMAGE_FORMATS

DEFAULT_SEED = 1234
"""Graine de l'essai KAN-84b."""


class ZImageUnavailable(AdapterUnavailableError):  # noqa: N818 - nom du contrat KAN-89
    """Poids Z-Image absents (ou paquets de l'extra `neural`)."""


ZImageVramGuard = ZImageStoppedError
"""Le garde-fou a arrêté la génération (mémoire de la carte, mémoire vive, durée)."""


@dataclass(frozen=True)
class ZImageResult:
    seconds: float
    vram_peak_mib: int
    width: int
    height: int
    engine: str = "zimage"
    style_words: tuple[str, ...] | None = None
    """KAN-97 : les mots de style retenus (None sans image de style)."""


STYLE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


def style_images(style_dir: Path | None) -> list[ImageArray]:
    """Les images de style déposées par le poste dans `style_dir` (au plus MAX_IMAGES, dans
    l'ordre de leur nom) ; une image illisible est ignorée."""
    if style_dir is None or not style_dir.is_dir():
        return []
    from PIL import Image, UnidentifiedImageError

    out: list[ImageArray] = []
    for path in sorted(style_dir.iterdir()):
        if path.suffix.lower() not in STYLE_SUFFIXES or len(out) >= MAX_IMAGES:
            continue
        try:
            with Image.open(path, formats=DECODED_IMAGE_FORMATS) as img:
                pixels = np.asarray(img.convert("RGB"), dtype=np.uint8)
        except (OSError, UnidentifiedImageError, ValueError):
            continue
        out.append(ImageArray(image_id=uuid5(NAMESPACE_URL, path.name), pixels=pixels))
    return out


def describe_style(
    images: list[ImageArray], scene: SceneWords, weights_root: Path, scorer: Any = None
) -> list[str]:
    """KAN-97 : les mots de style des images (SigLIP2 sur le processeur, libéré ensuite pour
    laisser la carte à Z-Image), sans contredire les couleurs de la scène."""
    if not images:
        return []
    describer = scorer if scorer is not None else SigLipStyleScorer(weights_root)
    try:
        values = describer.scores(images, sw.prompts())
    finally:
        describer.release()
    scores = {w.key: s for w, s in zip(sw.VOCABULARY, values, strict=True)}
    return sw.choose(scores, [*scene.surfaces, *scene.textiles])


def models_root() -> Path:
    """Dossier des poids : OPENPLAN_MODELS_DIR, sinon `models/weights` du dossier courant s'il
    existe, sinon celui de la copie du code. Le poste de rendu (KAN-89) lance le calcul depuis
    le dossier de la demande : un chemin relatif seul n'y trouvait rien (« unavailable »)."""
    configured = os.environ.get("OPENPLAN_MODELS_DIR")
    if configured:
        return Path(configured)
    if DEFAULT_WEIGHTS_ROOT.is_dir():
        return DEFAULT_WEIGHTS_ROOT
    import openplan

    return Path(openplan.__file__).resolve().parents[1] / DEFAULT_WEIGHTS_ROOT


def render_scene(
    bake_path: Path,
    camera_path: Path,
    out_png: Path,
    *,
    project_path: Path | None = None,
    prompt: str | None = None,
    seed: int = DEFAULT_SEED,
    weights_root: Path | None = None,
    style_dir: Path | None = None,
) -> ZImageResult:
    """Image Z-Image de la vue `camera_path` de la scène `bake_path`, écrite dans `out_png`
    (taille de génération : grand côté 1 024, format de la caméra). KAN-97 : avec `style_dir`
    (images d'inspiration déposées par le poste), leurs mots de style entrent dans la
    description ; SigLIP2 absent, le rendu se fait sans eux (`style_words` None : non lues)."""
    if prompt is None and project_path is None:
        msg = "render_scene : il faut le prompt, ou le projet pour le dériver de la scène"
        raise ValueError(msg)
    started = time.perf_counter()
    root = weights_root or models_root()
    try:
        finisher = ZImageFinisher(root)
    except AdapterUnavailableError as exc:
        raise ZImageUnavailable(str(exc)) from exc
    bake = Bake.model_validate_json(bake_path.read_text(encoding="utf-8"))
    camera = RenderCamera.model_validate_json(camera_path.read_text(encoding="utf-8"))
    images = style_images(style_dir)
    chosen: list[str] | None = None
    if prompt is None:
        assert project_path is not None
        project = load_project(json.loads(project_path.read_text(encoding="utf-8")))
        words = scene_words(bake, project, bake.level_id, camera)
        if images:
            try:
                chosen = describe_style(images, words, root)
            except AdapterUnavailableError:
                # Non lues (None), jamais « aucun style » ([]) : la carte dit « non lues ».
                logging.getLogger(__name__).warning("SigLIP2 non installé : rendu sans mots.")
                chosen = None
            words = replace(words, style=list(chosen or []))
        prompt = finish_prompt(project, None, bake.level_id, scene=words, engine="zimage")
    width, height = z_size(camera.width_px, camera.height_px)
    guides = view_guides(bake, camera, (width, height))
    result = finisher.finish(
        FinishRequest(
            prompt=prompt,
            image=np.zeros((height, width, 3), np.uint8),
            edges=guides.edges,
            seed=seed,
        )
    )
    from PIL import Image

    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result.pixels).save(out_png)
    peak_mb = result.peak_vram_mb or 0.0
    return ZImageResult(
        seconds=round(time.perf_counter() - started, 1),
        vram_peak_mib=round(peak_mb / 1.048576),
        width=width,
        height=height,
        style_words=None if chosen is None else tuple(chosen),
    )
