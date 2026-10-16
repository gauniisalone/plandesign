"""Protocoles des adaptateurs de modèles (docs/architecture/02 §2).

Aucun de ces protocoles ne mentionne torch, ONNX, OpenCV ni un modèle précis : une
implémentation heuristique, un réseau local ou un service distant (avec consentement)
exposent la même interface. Les entrées sont des tableaux en mémoire, jamais des chemins :
un adaptateur ne lit pas le disque et ne sait pas d'où vient l'image.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator

from openplan.schemas.common import Confidence, Radians, StrictModel


class AdapterUnavailableError(RuntimeError):
    """Paquet ou poids absents ; le message indique comment installer."""


class ImageArray(BaseModel):
    """Image RGB uint8 HxWx3 et son identifiant dans le projet."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    image_id: UUID
    pixels: NDArray[np.uint8]
    focal_35mm: float | None = Field(default=None, gt=0.0)
    """Focale équivalente 35 mm lue dans l'EXIF, si conservée."""

    @model_validator(mode="after")
    def _rgb_uint8(self) -> ImageArray:
        if self.pixels.ndim != 3 or self.pixels.shape[2] != 3 or self.pixels.dtype != np.uint8:
            msg = f"image attendue en HxWx3 uint8, reçu {self.pixels.shape} {self.pixels.dtype}"
            raise ValueError(msg)
        return self

    @property
    def width(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def height(self) -> int:
        return int(self.pixels.shape[0])


DetectedCategory = Literal[
    "wall",
    "floor",
    "ceiling",
    "door",
    "window",
    "roof_window",
    "radiator",
    "beam",
    "stairs",
    "outlet",
    "fixture",
    "furniture",
]


class DetectedElement(StrictModel):
    """Élément architectural détecté dans le plan image (pixels)."""

    category: DetectedCategory
    bbox_px: tuple[int, int, int, int]
    """x0, y0, x1, y1."""
    polygon_px: list[tuple[float, float]] | None = None
    score: Confidence
    label: str | None = None
    """Sous-catégorie libre du modèle, ex. "floor_wall_edge"."""
    model_id: str

    @model_validator(mode="after")
    def _bbox_ordered(self) -> DetectedElement:
        x0, y0, x1, y1 = self.bbox_px
        if x1 < x0 or y1 < y0:
            msg = f"bbox désordonnée : {self.bbox_px}"
            raise ValueError(msg)
        return self


class CameraParameters(StrictModel):
    """Intrinsèques et orientation estimées d'une photo.

    Convention : pitch positif = caméra qui regarde vers le haut ; roll positif = horizon
    tourné dans le sens antihoraire sur l'image. `yaw_hint` est relatif à la direction
    dominante des murs, pas au nord.
    """

    focal_px: float = Field(gt=0.0)
    principal_point: tuple[float, float]
    pitch: Radians
    roll: Radians
    yaw_hint: Radians | None = None
    height_m: float | None = Field(default=None, gt=0.0)
    vanishing_points_px: list[tuple[float, float]] = Field(default_factory=list)
    confidence: Confidence
    model_id: str


class DepthMap(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    depth: NDArray[np.float32]
    """HxW ; relative (disparité normalisée) ou métrique selon `metric`."""
    metric: bool
    confidence: NDArray[np.float32] | None = None
    model_id: str


class SegmentationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    labels: NDArray[np.int32]
    """HxW, index dans `classes`."""
    classes: list[str]
    scores: NDArray[np.float32] | None = None
    model_id: str


class ReconstructionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    points: NDArray[np.float32]
    """Nx3."""
    colors: NDArray[np.uint8] | None = None
    cameras: list[CameraParameters]
    metric: bool
    model_id: str


class FinishRequest(BaseModel):
    """Finition IA d'un rendu (D5) : img2img à force bornée, guidé par la profondeur et les
    arêtes du rendu des cotes pour ne rien déplacer."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    image: NDArray[np.uint8]
    """H×W×3, le rendu Blender."""
    depth: NDArray[np.uint8] | None = None
    """H×W, proche = clair (profondeur inversée du rendu)."""
    edges: NDArray[np.uint8] | None = None
    """H×W, arêtes du rendu (Canny)."""
    strength: float = Field(default=0.35, ge=0.05, le=0.55)
    seed: int = Field(default=0, ge=0)
    steps: int = Field(default=30, ge=1, le=100)
    guidance: float = Field(default=7.0, ge=1.0, le=30.0)
    max_side: int = Field(default=768, ge=256, le=1024)


@runtime_checkable
class ImageFinisher(Protocol):
    model_ids: list[str]

    def finish(self, request: FinishRequest) -> GeneratedImage: ...


InspirationMode = Literal["txt2img", "img2img"]


class InspirationRequest(BaseModel):
    """Image d'inspiration d'une vue (V1, sortie B) : la diffusion refait l'image, guidée
    fortement par la profondeur et les arêtes du rendu des cotes (géométrie tenue), en partant
    du bruit (`txt2img`) ou de la couleur du rendu à forte force (`img2img`, matières tenues).
    Toujours `generated` : cotes non garanties."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    image: NDArray[np.uint8]
    """H×W×3, le rendu Blender (point de départ en img2img ; taille de sortie)."""
    depth: NDArray[np.uint8] | None = None
    edges: NDArray[np.uint8] | None = None
    mode: InspirationMode = "txt2img"
    strength: float = Field(default=0.8, ge=0.2, le=1.0)
    """img2img seulement : part refaite par la diffusion (0,45 pour la variante conservatrice
    du rendu contextualisé, 0,8 pour l'inspiration d'une vue)."""
    control_scale: float = Field(default=1.2, ge=0.2, le=1.5)
    """Poids des contraintes profondeur et arêtes (0,4 pour la variante radicale d'une photo,
    A4 ; 1,2 pour l'inspiration d'une vue)."""
    seed: int = Field(default=0, ge=0)
    steps: int = Field(default=30, ge=1, le=100)
    guidance: float = Field(default=7.0, ge=1.0, le=30.0)
    max_side: int = Field(default=768, ge=256, le=1024)
    references: list[NDArray[np.uint8]] = Field(default_factory=list, max_length=4)
    """Images de référence (A2) : leur allure (matières, ambiance) guide la génération par
    l'IP-Adapter, en plus du texte ; jamais la géométrie. Vide : texte seul."""
    reference_scale: float = Field(default=0.6, ge=0.0, le=1.0)
    """Poids des références face au texte (0,6 : l'allure sans copier)."""


class ImageInspirer(Protocol):
    """Implémenté par la finition locale (mêmes poids) : la sortie B d'une vue."""

    model_ids: list[str]

    def inspire(self, request: InspirationRequest) -> GeneratedImage: ...


class ImageGenerationRequest(BaseModel):
    """Image d'inspiration guidée par une carte de profondeur (ControlNet ou équivalent)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    control: NDArray[np.uint8]
    """HxW, proche = clair : la structure à respecter (profondeur inversée)."""
    seed: int = Field(default=0, ge=0)
    steps: int = Field(default=20, ge=1, le=100)
    guidance: float = Field(default=7.5, ge=1.0, le=30.0)
    max_side: int = Field(default=640, ge=256, le=1024)
    """Grand côté de l'image générée (multiple de 8 imposé par le modèle)."""


class GeneratedImage(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    pixels: NDArray[np.uint8]
    seed: int
    model_ids: list[str]
    duration_s: float = Field(ge=0.0)
    peak_vram_mb: float | None = None
    """Mémoire GPU maximale allouée pendant la génération, None sur processeur."""


class DepthEstimator(Protocol):
    model_id: str

    def predict(self, image: ImageArray) -> DepthMap: ...


class ImageGenerator(Protocol):
    """Générateur d'images local ; sa sortie est toujours de source `generated`."""

    model_ids: list[str]

    def generate(self, request: ImageGenerationRequest) -> GeneratedImage: ...


class RoomElementDetector(Protocol):
    model_id: str

    def detect(self, image: ImageArray) -> list[DetectedElement]: ...


@runtime_checkable
class RoofWindowDetector(Protocol):
    """Fenêtres de toit (velux) vues sur une photo de combles (C2) : boîtes `roof_window`."""

    model_id: str

    def detect_roof_windows(self, image: ImageArray) -> list[DetectedElement]: ...


class SegmentationModel(Protocol):
    model_id: str

    def segment(self, image: ImageArray) -> SegmentationResult: ...


class CameraEstimator(Protocol):
    model_id: str

    def estimate(self, image: ImageArray) -> CameraParameters: ...


class MultiViewReconstructor(Protocol):
    model_id: str

    def reconstruct(self, images: list[ImageArray]) -> ReconstructionResult: ...


# ---------------------------------------------------------------- modèles de langage
LlmPurpose = Literal["perception", "intention", "critique", "assistant", "test"]
"""Rôle d'un appel (docs/STATUS.md, « Assistant IA ») : journalisé, jamais le contenu."""


class LlmMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str


class LlmRequest(BaseModel):
    """Requête à un modèle de langage, éventuellement avec des images (vision).

    Les images sont jointes au dernier message utilisateur. `json_mode` demande une réponse
    JSON ; le schéma attendu est décrit dans le prompt et validé par l'appelant.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    purpose: LlmPurpose
    messages: list[LlmMessage] = Field(min_length=1)
    images: list[ImageArray] = Field(default_factory=list)
    json_mode: bool = False
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, gt=0)


class LlmResponse(StrictModel):
    text: str
    model_id: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LanguageModel(Protocol):
    """Modèle de langage, local ou distant. `remote` conditionne la garde de consentement."""

    model_id: str
    remote: bool
    supports_images: bool
    destination: str
    """Hôte joint (journal réseau), ex. "api.openai.com" ou "localhost:11434"."""

    def complete(self, request: LlmRequest) -> LlmResponse: ...
