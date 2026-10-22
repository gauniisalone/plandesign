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
import os
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT
from openplan.adapters.neural.zimage import ZImageFinisher, ZImageStoppedError, z_size
from openplan.adapters.protocols import AdapterUnavailableError, FinishRequest
from openplan.pipeline.finish import finish_prompt
from openplan.pipeline.guides import view_guides
from openplan.pipeline.scene_prompt import scene_words
from openplan.scene.migrate import load_project
from openplan.schemas.bake import Bake, RenderCamera

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


def models_root() -> Path:
    return Path(os.environ.get("OPENPLAN_MODELS_DIR") or DEFAULT_WEIGHTS_ROOT)


def render_scene(
    bake_path: Path,
    camera_path: Path,
    out_png: Path,
    *,
    project_path: Path | None = None,
    prompt: str | None = None,
    seed: int = DEFAULT_SEED,
    weights_root: Path | None = None,
) -> ZImageResult:
    """Image Z-Image de la vue `camera_path` de la scène `bake_path`, écrite dans `out_png`
    (taille de génération : grand côté 1 024, format de la caméra)."""
    if prompt is None and project_path is None:
        msg = "render_scene : il faut le prompt, ou le projet pour le dériver de la scène"
        raise ValueError(msg)
    started = time.perf_counter()
    try:
        finisher = ZImageFinisher(weights_root or models_root())
    except AdapterUnavailableError as exc:
        raise ZImageUnavailable(str(exc)) from exc
    bake = Bake.model_validate_json(bake_path.read_text(encoding="utf-8"))
    camera = RenderCamera.model_validate_json(camera_path.read_text(encoding="utf-8"))
    if prompt is None:
        assert project_path is not None
        project = load_project(json.loads(project_path.read_text(encoding="utf-8")))
        words = scene_words(bake, project, bake.level_id, camera)
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
    )
