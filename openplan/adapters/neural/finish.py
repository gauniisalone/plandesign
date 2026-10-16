"""Finition IA locale d'un rendu Blender (D5) : Stable Diffusion 1.5 en img2img, à force
bornée, guidé par ControlNet profondeur (et arêtes Canny quand il est installé).

Poids : les mêmes que l'image d'inspiration (SD 1.5 et ControlNet profondeur), plus
`lllyasviel/control_v11p_sd15_canny`, facultatif ; tous sous **CreativeML OpenRAIL-M**,
acceptée explicitement au téléchargement. Avec des images de référence (A2), l'IP-Adapter
SD 1.5 (`h94/IP-Adapter`, Apache-2.0, facultatif) guide l'allure ; il n'est chargé que pour
une demande qui en porte, et déchargé sinon. L'image produite est toujours `generated` : une
finition sur le rendu des cotes, jamais une mesure.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

from openplan.adapters.neural.idle import IdleRelease
from openplan.adapters.neural.inspiration import (
    CONTROLNET_MODEL_ID,
    CONTROLNET_SENTINEL,
    SD_MODEL_ID,
    SD_SENTINEL,
    control_size,
)
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, require_installed
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    FinishRequest,
    GeneratedImage,
    InspirationRequest,
)

CANNY_MODEL_ID = "controlnet_canny_sd15@1.1"
CANNY_SENTINEL = "diffusion_pytorch_model.fp16.safetensors"
IP_ADAPTER_MODEL_ID = "ip_adapter_sd15@1.0"
IP_ADAPTER_SUBFOLDER = "models"
IP_ADAPTER_WEIGHTS = "ip-adapter_sd15.safetensors"
IP_ADAPTER_SENTINEL = f"{IP_ADAPTER_SUBFOLDER}/{IP_ADAPTER_WEIGHTS}"
DEPTH_SCALE = 0.9
EDGE_SCALE = 0.6
"""Poids des contraintes : la profondeur tient la géométrie, les arêtes les contours."""


class ControlledRefiner:
    """Implémente ImageFinisher. Les modèles ne sont chargés qu'au premier appel."""

    def __init__(self, weights_root: Path = DEFAULT_WEIGHTS_ROOT, device: str = "cpu") -> None:
        self.sd_dir = require_installed(
            SD_MODEL_ID, weights_root, SD_SENTINEL, license_required=True
        )
        self.depth_dir = require_installed(
            CONTROLNET_MODEL_ID, weights_root, CONTROLNET_SENTINEL, license_required=True
        )
        try:
            self.canny_dir: Path | None = require_installed(
                CANNY_MODEL_ID, weights_root, CANNY_SENTINEL, license_required=True
            )
        except AdapterUnavailableError:
            self.canny_dir = None
        try:
            self.ip_adapter_dir: Path | None = require_installed(
                IP_ADAPTER_MODEL_ID, weights_root, IP_ADAPTER_SENTINEL
            )
        except AdapterUnavailableError:
            self.ip_adapter_dir = None
        self.model_ids = [SD_MODEL_ID, CONTROLNET_MODEL_ID] + (
            [CANNY_MODEL_ID] if self.canny_dir else []
        )
        self.requested_device = device
        self.device = device
        self._pipe: Any = None
        self._txt2img: Any = None
        self._references_loaded = False
        self.idle = IdleRelease(self._unload)

    def _unload(self) -> None:
        """Relâche les pipelines (SD, ControlNet, IP-Adapter) : rechargés au calcul suivant."""
        self._pipe = None
        self._txt2img = None
        self._references_loaded = False

    def finish(self, request: FinishRequest) -> GeneratedImage:
        with self.idle.use():
            return self._finish(request)

    def inspire(self, request: InspirationRequest) -> GeneratedImage:
        """Sortie B d'une vue (V1) : voir `_inspire`."""
        with self.idle.use():
            return self._inspire(request)

    def _set_references(self, references: list[np.ndarray], scale: float) -> list[Any]:
        """Charge l'IP-Adapter (paresseusement, ≈ 1,3 Go de plus) pour une demande avec
        références, le décharge pour une demande sans ; renvoie les images de référence
        prêtes pour le pipeline. Sans poids installés, une référence est refusée : rien
        n'est simulé."""
        if not references:
            if self._references_loaded:
                self._pipe.unload_ip_adapter()
                if self.device == "cuda":
                    self._pipe.enable_attention_slicing()
                self._references_loaded = False
                self._txt2img = None
            return []
        if self.ip_adapter_dir is None:
            msg = (
                f"références demandées mais poids absents pour {IP_ADAPTER_MODEL_ID} : "
                f"uv run openplan models pull {IP_ADAPTER_MODEL_ID}"
            )
            raise AdapterUnavailableError(msg)
        from PIL import Image

        if not self._references_loaded:
            # Les processeurs d'attention de l'adaptateur remplacent ceux du découpage
            # d'attention (incompatibles) : le découpage revient au déchargement.
            if self.device == "cuda":
                self._pipe.disable_attention_slicing()
            self._pipe.load_ip_adapter(
                self.ip_adapter_dir,
                subfolder=IP_ADAPTER_SUBFOLDER,
                weight_name=IP_ADAPTER_WEIGHTS,
                image_encoder_folder="image_encoder",
                local_files_only=True,
            )
            self._references_loaded = True
            self._txt2img = None
        self._pipe.set_ip_adapter_scale(scale)
        return [Image.fromarray(r).convert("RGB") for r in references]

    def _used_models(self, references: list[Any]) -> list[str]:
        return list(self.model_ids) + ([IP_ADAPTER_MODEL_ID] if references else [])

    def _load(self) -> None:
        try:
            import torch
            from diffusers import (
                ControlNetModel,
                StableDiffusionControlNetImg2ImgPipeline,
                UniPCMultistepScheduler,
            )
        except ImportError as exc:
            msg = "torch/diffusers absents : make setup-neural"
            raise AdapterUnavailableError(msg) from exc
        if self.requested_device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        nets = [
            ControlNetModel.from_pretrained(  # type: ignore[no-untyped-call]
                d, dtype=dtype, variant="fp16", local_files_only=True
            )
            for d in [self.depth_dir, self.canny_dir]
            if d is not None
        ]
        pipe = StableDiffusionControlNetImg2ImgPipeline.from_pretrained(  # type: ignore[no-untyped-call]
            self.sd_dir,
            controlnet=nets if len(nets) > 1 else nets[0],
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

    def _finish(self, request: FinishRequest) -> GeneratedImage:
        if self._pipe is None:
            self._load()
        import torch
        from PIL import Image

        started = time.perf_counter()
        h, w = request.image.shape[:2]
        size = control_size(w, h, request.max_side)
        image = Image.fromarray(request.image).convert("RGB").resize(size, Image.Resampling.LANCZOS)
        controls, scales = [], []
        depth = request.depth if request.depth is not None else np.full((h, w), 128, np.uint8)
        controls.append(
            Image.fromarray(depth).convert("RGB").resize(size, Image.Resampling.BILINEAR)
        )
        scales.append(DEPTH_SCALE)
        if self.canny_dir is not None and request.edges is not None:
            controls.append(
                Image.fromarray(request.edges).convert("RGB").resize(size, Image.Resampling.NEAREST)
            )
            scales.append(EDGE_SCALE)
        elif self.canny_dir is not None:
            controls.append(Image.new("RGB", size))
            scales.append(0.0)
        generator = torch.Generator(device=self.device).manual_seed(request.seed)
        references = self._set_references([], 0.0)  # la finition d'une vue : texte seul
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        with torch.inference_mode():
            out = self._pipe(
                request.prompt,
                negative_prompt=request.negative_prompt or None,
                image=image,
                control_image=controls if len(controls) > 1 else controls[0],
                strength=request.strength,
                num_inference_steps=request.steps,
                guidance_scale=request.guidance,
                controlnet_conditioning_scale=scales if len(scales) > 1 else scales[0],
                generator=generator,
            )
        result = out.images[0].convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
        pixels = np.asarray(result, dtype=np.uint8)
        peak = torch.cuda.max_memory_allocated() / 1e6 if self.device == "cuda" else None
        return GeneratedImage(
            pixels=pixels,
            seed=request.seed,
            model_ids=self._used_models(references),
            duration_s=time.perf_counter() - started,
            peak_vram_mb=peak,
        )

    def _inspire(self, request: InspirationRequest) -> GeneratedImage:
        """Sortie B d'une vue (V1) : mêmes poids que la finition ; txt2img (pipeline refait
        avec les mêmes composants, sans mémoire de plus) ou img2img à forte force, guidés
        par la profondeur et les arêtes au poids `control_scale`."""
        if self._pipe is None:
            self._load()
        import torch
        from diffusers import StableDiffusionControlNetPipeline
        from PIL import Image

        started = time.perf_counter()
        h, w = request.image.shape[:2]
        size = control_size(w, h, request.max_side)
        depth = request.depth if request.depth is not None else np.full((h, w), 128, np.uint8)
        controls = [Image.fromarray(depth).convert("RGB").resize(size, Image.Resampling.BILINEAR)]
        scales = [request.control_scale]
        if self.canny_dir is not None:
            edges = request.edges if request.edges is not None else np.zeros((h, w), np.uint8)
            controls.append(
                Image.fromarray(edges).convert("RGB").resize(size, Image.Resampling.NEAREST)
            )
            scales.append(request.control_scale if request.edges is not None else 0.0)
        generator = torch.Generator(device=self.device).manual_seed(request.seed)
        references = self._set_references(request.references, request.reference_scale)
        # Une liste par adaptateur : plusieurs références nourrissent le même IP-Adapter.
        extra: dict[str, Any] = {"ip_adapter_image": [references]} if references else {}
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        control: Any = controls if len(controls) > 1 else controls[0]
        scale: Any = scales if len(scales) > 1 else scales[0]
        with torch.inference_mode():
            if request.mode == "img2img":
                image = Image.fromarray(request.image).convert("RGB")
                out = self._pipe(
                    request.prompt,
                    negative_prompt=request.negative_prompt or None,
                    image=image.resize(size, Image.Resampling.LANCZOS),
                    control_image=control,
                    strength=request.strength,
                    num_inference_steps=request.steps,
                    guidance_scale=request.guidance,
                    controlnet_conditioning_scale=scale,
                    generator=generator,
                    **extra,
                )
            else:
                if self._txt2img is None:
                    self._txt2img = StableDiffusionControlNetPipeline(  # type: ignore[no-untyped-call]
                        **self._pipe.components
                    )
                    self._txt2img.set_progress_bar_config(disable=True)
                out = self._txt2img(
                    request.prompt,
                    negative_prompt=request.negative_prompt or None,
                    image=control,
                    width=size[0],
                    height=size[1],
                    num_inference_steps=request.steps,
                    guidance_scale=request.guidance,
                    controlnet_conditioning_scale=scale,
                    generator=generator,
                    **extra,
                )
        result = out.images[0].convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
        peak = torch.cuda.max_memory_allocated() / 1e6 if self.device == "cuda" else None
        return GeneratedImage(
            pixels=np.asarray(result, dtype=np.uint8),
            seed=request.seed,
            model_ids=self._used_models(references),
            duration_s=time.perf_counter() - started,
            peak_vram_mb=peak,
        )
