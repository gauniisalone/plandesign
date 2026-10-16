"""Image d'inspiration locale : Stable Diffusion 1.5 guidé par la profondeur (ControlNet).

Poids : `stable-diffusion-v1-5/stable-diffusion-v1-5` (miroir du modèle RunwayML retiré) et
`lllyasviel/control_v11f1p_sd15_depth`, tous deux sous **CreativeML OpenRAIL-M** : gratuit,
usage commercial permis, mais restrictions d'usage (pas de contenu haineux, trompeur, sexuel
non consenti, etc., annexe A de la licence) que l'utilisateur accepte explicitement au
téléchargement (`openplan models pull <id> --accept-license`). Seule la variante fp16 est
téléchargée (≈ 2,9 Go) ; le filtre de sécurité du dépôt n'est pas chargé (1,2 Go de moins),
la sortie n'est donc pas filtrée : elle reste locale et marquée `generated`.

Ce que l'image vaut : une **inspiration** qui suit la structure de la photo (profondeur), pas
un rendu de la géométrie mesurée ; elle ne respecte aucune cote.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from openplan.adapters.neural.idle import IdleRelease
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, require_installed
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    GeneratedImage,
    ImageGenerationRequest,
)

SD_MODEL_ID = "stable_diffusion_1_5@1.5"
CONTROLNET_MODEL_ID = "controlnet_depth_sd15@1.1"
SD_SENTINEL = "unet/diffusion_pytorch_model.fp16.safetensors"
CONTROLNET_SENTINEL = "diffusion_pytorch_model.fp16.safetensors"
LICENSE = "CreativeML OpenRAIL-M"
CONDITIONING_SCALE = 0.8
"""Poids de la contrainte de profondeur : 1 colle à la structure, 0 l'ignore."""


def control_size(width: int, height: int, max_side: int) -> tuple[int, int]:
    """Taille de génération : grand côté ≤ max_side, dimensions multiples de 8."""
    scale = min(1.0, max_side / max(width, height))
    w = max(64, round(width * scale / 8) * 8)
    h = max(64, round(height * scale / 8) * 8)
    return w, h


class DepthGuidedDiffusion:
    """Implémente ImageGenerator. Les modèles ne sont chargés qu'au premier appel."""

    def __init__(self, weights_root: Path = DEFAULT_WEIGHTS_ROOT, device: str = "cpu") -> None:
        self.model_ids: list[str] = [SD_MODEL_ID, CONTROLNET_MODEL_ID]
        self.sd_dir = require_installed(
            SD_MODEL_ID, weights_root, SD_SENTINEL, license_required=True
        )
        self.controlnet_dir = require_installed(
            CONTROLNET_MODEL_ID, weights_root, CONTROLNET_SENTINEL, license_required=True
        )
        self.requested_device = device
        self.device = device
        self._pipe: Any = None
        self.idle = IdleRelease(self._unload)

    def _unload(self) -> None:
        self._pipe = None

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        with self.idle.use():
            return self._generate(request)

    def _load(self) -> None:
        try:
            import torch
            from diffusers import (
                ControlNetModel,
                StableDiffusionControlNetPipeline,
                UniPCMultistepScheduler,
            )
        except ImportError as exc:
            msg = "torch/diffusers absents : make setup-neural"
            raise AdapterUnavailableError(msg) from exc
        if self.requested_device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        controlnet = ControlNetModel.from_pretrained(  # type: ignore[no-untyped-call]
            self.controlnet_dir, dtype=dtype, variant="fp16", local_files_only=True
        )
        pipe = StableDiffusionControlNetPipeline.from_pretrained(  # type: ignore[no-untyped-call]
            self.sd_dir,
            controlnet=controlnet,
            dtype=dtype,
            variant="fp16",
            safety_checker=None,
            requires_safety_checker=False,
            local_files_only=True,
        )
        pipe.scheduler = UniPCMultistepScheduler.from_config(  # type: ignore[no-untyped-call]
            pipe.scheduler.config
        )
        pipe.set_progress_bar_config(disable=True)
        pipe.to(self.device)
        if self.device == "cuda":
            pipe.enable_attention_slicing()
        self._pipe = pipe

    def _generate(self, request: ImageGenerationRequest) -> GeneratedImage:
        if self._pipe is None:
            self._load()
        import torch
        from PIL import Image

        started = time.perf_counter()
        h, w = request.control.shape[:2]
        size = control_size(w, h, request.max_side)
        control = (
            Image.fromarray(request.control).convert("RGB").resize(size, Image.Resampling.BILINEAR)
        )
        generator = torch.Generator(device=self.device).manual_seed(request.seed)
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            out = self._pipe(
                request.prompt,
                negative_prompt=request.negative_prompt or None,
                image=control,
                num_inference_steps=request.steps,
                guidance_scale=request.guidance,
                controlnet_conditioning_scale=CONDITIONING_SCALE,
                generator=generator,
            )
        pixels = np.asarray(out.images[0].convert("RGB"), dtype=np.uint8)
        peak = torch.cuda.max_memory_allocated() / 1e6 if self.device == "cuda" else None
        return GeneratedImage(
            pixels=pixels,
            seed=request.seed,
            model_ids=list(self.model_ids),
            duration_s=time.perf_counter() - started,
            peak_vram_mb=peak,
        )
