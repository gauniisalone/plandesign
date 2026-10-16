"""Construction des adaptateurs depuis la configuration (variables d'environnement).

Aucun modèle n'est construit sans configuration explicite : sans `OPENPLAN_AI_BASE_URL` et
`OPENPLAN_AI_MODEL`, il n'y a pas d'assistant IA, point. La clé est lue dans
`OPENPLAN_AI_API_KEY`, sinon `OPENAI_API_KEY`, sinon `GEMINI_API_KEY`.

Exemples (docs/contributing/setup.md) :
  Ollama local  : OPENPLAN_AI_BASE_URL=http://127.0.0.1:11434/v1  OPENPLAN_AI_MODEL=llava
  OpenAI        : OPENPLAN_AI_BASE_URL=https://api.openai.com/v1   OPENPLAN_AI_MODEL=gpt-4o-mini
  Gemini        : OPENPLAN_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
                  OPENPLAN_AI_MODEL=gemini-3.5-flash-lite  (les 2.x sont retirés du service)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from openplan.adapters.protocols import (
    DepthEstimator,
    ImageFinisher,
    ImageGenerator,
    LanguageModel,
    RoomElementDetector,
)
from openplan.resources import resource
from openplan.schemas.common import StrictModel

DEFAULT_TIMEOUT_S = 600.0
"""Un modèle local de 7 B sur processeur met plusieurs minutes : le délai doit le permettre."""


@dataclass(frozen=True)
class AiConfig:
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    timeout_s: float = DEFAULT_TIMEOUT_S

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AiConfig:
        key = (
            env.get("OPENPLAN_AI_API_KEY") or env.get("OPENAI_API_KEY") or env.get("GEMINI_API_KEY")
        )
        raw_timeout = env.get("OPENPLAN_AI_TIMEOUT_S", "")
        try:
            timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_S
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        return cls(
            base_url=env.get("OPENPLAN_AI_BASE_URL") or None,
            model=env.get("OPENPLAN_AI_MODEL") or None,
            api_key=key or None,
            timeout_s=timeout if timeout > 0 else DEFAULT_TIMEOUT_S,
        )


REGISTRY_PATH = resource("models", "registry.yaml")


class ModelEntry(StrictModel):
    """Une entrée de models/registry.yaml (champs utiles au code ; le reste est documentaire)."""

    id: str
    role: str
    kind: str
    license: str
    commercial_ok: bool
    remote: bool
    status: str
    module: str | None = None
    hf_repo: str | None = None
    weights_mb: float | None = None
    memory_mb: float | None = None
    device: str | None = None
    notes: str = ""
    requires_consent: bool = False
    requires_license_acceptance: bool = False
    """Poids sous licence à accepter explicitement (OpenRAIL-M) : `models pull --accept-license`."""
    license_url: str | None = None
    sentinel: str = "model.safetensors"
    """Fichier qui signe une installation complète dans le dossier des poids."""
    allow_patterns: list[str] | None = None
    """Fichiers à télécharger (motifs Hugging Face) ; None = configurations et safetensors."""


def load_registry(path: Path = REGISTRY_PATH) -> list[ModelEntry]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [ModelEntry.model_validate(entry) for entry in data["models"]]


def registry_entry(model_id: str, path: Path = REGISTRY_PATH) -> ModelEntry:
    for entry in load_registry(path):
        if entry.id == model_id:
            return entry
    msg = f"modèle inconnu du registre : {model_id}"
    raise KeyError(msg)


DEVICE_AUTO = "auto"


def resolve_device(requested: str | None) -> tuple[str, str]:
    """Périphérique de calcul effectif et son libellé lisible.

    `auto` (défaut) : `cuda` dès que torch voit un GPU, sinon `cpu` ; `cuda` sans GPU retombe
    sur `cpu` ; `cpu` reste `cpu` même avec un GPU (choix explicite, dit dans le libellé).
    Sans torch installé : `cpu`. Ne charge aucun modèle."""
    wanted = (requested or DEVICE_AUTO).strip().lower() or DEVICE_AUTO
    try:
        import torch
    except ImportError:
        return "cpu", "cpu (torch absent : make setup-neural pour les modèles locaux)"
    has_cuda = bool(torch.cuda.is_available())
    if wanted == "cpu":
        note = ", GPU présent mais ignoré (OPENPLAN_DEVICE=cpu)" if has_cuda else " (demandé)"
        return "cpu", f"cpu{note}"
    if has_cuda:
        name = str(torch.cuda.get_device_name(0))
        how = "choisi automatiquement" if wanted == DEVICE_AUTO else "demandé"
        return "cuda", f"cuda ({name}, {how})"
    note = (
        "OPENPLAN_DEVICE=cuda demandé mais aucun GPU CUDA visible"
        if wanted == "cuda"
        else ("aucun GPU CUDA visible")
    )
    return "cpu", f"cpu ({note})"


def build_depth_estimator(weights_root: Path, device: str = DEVICE_AUTO) -> DepthEstimator:
    """Estimateur de profondeur métrique local ; AdapterUnavailableError si poids ou paquets
    manquent, avec la commande à lancer."""
    from openplan.adapters.neural.depth_anything import DepthAnythingMetric

    return DepthAnythingMetric(weights_root, resolve_device(device)[0])


def build_image_generator(weights_root: Path, device: str = DEVICE_AUTO) -> ImageGenerator:
    """Générateur d'images d'inspiration local (diffusion guidée par la profondeur) ;
    AdapterUnavailableError si paquets, poids ou acceptation de licence manquent."""
    from openplan.adapters.neural.inspiration import DepthGuidedDiffusion

    return DepthGuidedDiffusion(weights_root, resolve_device(device)[0])


def build_image_finisher(weights_root: Path, device: str = DEVICE_AUTO) -> ImageFinisher:
    """Finition IA locale des rendus (D5) ; AdapterUnavailableError si paquets, poids ou
    acceptation de licence manquent (le ControlNet arêtes est facultatif)."""
    from openplan.adapters.neural.finish import ControlledRefiner

    return ControlledRefiner(weights_root, resolve_device(device)[0])


def build_opening_detector(weights_root: Path, device: str = DEVICE_AUTO) -> RoomElementDetector:
    """Détecteur local de portes et fenêtres (OWLv2) ; AdapterUnavailableError si poids ou
    paquets manquent, avec la commande à lancer."""
    from openplan.adapters.neural.openings import OwlV2OpeningDetector

    return OwlV2OpeningDetector(weights_root, resolve_device(device)[0])


def build_language_model(config: AiConfig) -> LanguageModel | None:
    """Modèle configuré, ou None. Import à la demande : le client HTTP n'est chargé qu'ici."""
    if not config.configured:
        return None
    assert config.base_url is not None and config.model is not None
    from openplan.adapters.remote.openai_compatible import OpenAICompatibleModel

    return OpenAICompatibleModel(
        config.base_url, config.model, config.api_key, timeout_s=config.timeout_s
    )
