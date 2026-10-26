"""KAN-97 : images de style → scores zéro-shot par SigLIP2, sur le CPU seulement.

`google/siglip2-base-patch16-224`, Apache-2.0, chargé par `transformers` depuis le dossier
local des poids (`openplan models pull siglip2_base@1.0`), sans accès réseau. Le poste de
rendu l'appelle avant Z-Image, sur le processeur, puis le libère : la carte reste à Z-Image
(garde VRAM de KAN-88 inchangée). Les textes comparés sont ceux du vocabulaire fermé
(`pipeline.style_words.prompts`) ; le score d'un texte est la sigmoïde de SigLIP, moyennée sur
les images.
"""

from __future__ import annotations

import gc
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, require_installed
from openplan.adapters.protocols import AdapterUnavailableError, ImageArray

MODEL_ID = "siglip2_base@1.0"
HF_REPO = "google/siglip2-base-patch16-224"
TEXT_LENGTH = 64
"""Longueur des textes de SigLIP2 (complétés à cette longueur, comme à l'entraînement)."""


class SigLipStyleScorer:
    """Scores zéro-shot (0 à 1) de textes sur des images, sur le CPU."""

    model_id = MODEL_ID

    def __init__(self, weights_root: Path = DEFAULT_WEIGHTS_ROOT) -> None:
        self.weights = require_installed(MODEL_ID, weights_root)
        self._model: Any = None
        self._processor: Any = None

    def _load(self) -> None:
        try:
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            msg = "torch/transformers absents : make setup-neural"
            raise AdapterUnavailableError(msg) from exc
        load_processor: Any = AutoProcessor.from_pretrained
        load_model: Any = AutoModel.from_pretrained
        self._processor = load_processor(str(self.weights), local_files_only=True)
        self._model = load_model(str(self.weights), local_files_only=True).to("cpu").eval()

    def release(self) -> None:
        """Libère le modèle (avant Z-Image)."""
        self._model = None
        self._processor = None
        gc.collect()

    def scores(self, images: Sequence[ImageArray], texts: Sequence[str]) -> list[float]:
        """Pour chaque texte, la probabilité sigmoïde moyenne sur les images ; [] sans image."""
        if not images or not texts:
            return []
        if self._model is None:
            self._load()
        import torch
        from PIL import Image

        pils = [Image.fromarray(i.pixels).convert("RGB") for i in images]
        inputs = self._processor(
            text=list(texts),
            images=pils,
            padding="max_length",
            max_length=TEXT_LENGTH,
            return_tensors="pt",
        )
        with torch.no_grad():
            outputs = self._model(**inputs)
        probs = torch.sigmoid(outputs.logits_per_image)  # images × textes
        return [float(v) for v in probs.mean(dim=0).tolist()]
