"""Profondeur métrique par Depth Anything V2 Small, variante intérieure (Hypersim).

Licence Apache-2.0 (variante Small ; les Base/Large sont CC-BY-NC et exclues), 24,8 M de
paramètres, 99 Mo de poids, sortie en mètres bornée à 20 m. Chargé par `transformers`
depuis le dossier local des poids, sans accès réseau (`local_files_only`).

Ce que la carte vaut : une échelle apprise sur des intérieurs synthétiques, pas une mesure.
Erreur d'échelle attendue de l'ordre de 10 à 15 % sur des pièces ordinaires, davantage sur un
grenier ou une pièce vide. Toute dimension qui en découle est `estimated` avec son sigma.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from openplan.adapters.neural.idle import IdleRelease
from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, require_installed
from openplan.adapters.protocols import AdapterUnavailableError, DepthMap, ImageArray

MODEL_ID = "depth_anything_v2_metric_indoor_small@1.0"
HF_REPO = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
MAX_DEPTH_M = 20.0
EXPECTED_SCALE_ERROR = 0.15
"""Fraction : incertitude relative annoncée sur les distances déduites (non mesurée ici)."""


class DepthAnythingMetric:
    """Implémente DepthEstimator. Le modèle n'est chargé qu'au premier appel."""

    model_id = MODEL_ID

    def __init__(self, weights_root: Path = DEFAULT_WEIGHTS_ROOT, device: str = "cpu") -> None:
        self.weights = require_installed(MODEL_ID, weights_root)
        self.requested_device = device
        self.device = device
        self._model: Any = None
        self._processor: Any = None
        self.idle = IdleRelease(self._unload)

    def _unload(self) -> None:
        self._model = None
        self._processor = None

    def predict(self, image: ImageArray) -> DepthMap:
        with self.idle.use():
            return self._predict(image)

    def _load(self) -> None:
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        except ImportError as exc:
            msg = "torch/transformers absents : make setup-neural"
            raise AdapterUnavailableError(msg) from exc
        if self.requested_device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        try:
            self._processor = AutoImageProcessor.from_pretrained(  # type: ignore[no-untyped-call]
                self.weights, local_files_only=True
            )
            model = AutoModelForDepthEstimation.from_pretrained(self.weights, local_files_only=True)
        except ImportError as exc:  # transformers réclame torchvision pour le prétraitement
            msg = f"dépendance manquante pour {MODEL_ID} : make setup-neural ({exc})"
            raise AdapterUnavailableError(msg) from exc
        self._model = model.to(self.device).eval()

    def _predict(self, image: ImageArray) -> DepthMap:
        if self._model is None:
            self._load()
        import torch
        from PIL import Image

        pil = Image.fromarray(image.pixels)
        inputs = self._processor(images=pil, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(**inputs)
        post = self._processor.post_process_depth_estimation(
            outputs, target_sizes=[(image.height, image.width)]
        )
        depth = post[0]["predicted_depth"].detach().cpu().numpy().astype(np.float32)
        return DepthMap(depth=depth, metric=True, model_id=self.model_id)
