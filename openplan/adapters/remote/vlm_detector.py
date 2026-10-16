"""Rôle « Perception » : un modèle vision-langage décrit la pièce en JSON structuré.

Le modèle reçoit la photo (EXIF déjà retiré) via une fonction d'appel fournie par la
passerelle de consentement ; ce module ne sait pas s'il parle à un serveur local ou distant.
Sa sortie est validée par Pydantic et convertie en `DetectedElement` (pixels). Toute
dimension renvoyée est une estimation non mesurée : score plafonné, source `estimated`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openplan.adapters.protocols import (
    DetectedCategory,
    DetectedElement,
    ImageArray,
    LanguageModel,
    LlmMessage,
    LlmRequest,
    LlmResponse,
)
from openplan.adapters.remote.openai_compatible import MAX_IMAGE_SIDE
from openplan.schemas.common import Confidence, StrictModel

MAX_SCORE = 0.6
"""Une confiance auto-déclarée par un modèle ne vaut jamais plus qu'une estimation."""

SYSTEM_PROMPT = (
    "Tu es un assistant de relevé d'intérieur. Tu réponds uniquement par un objet JSON, "
    "sans texte autour. Tu ne donnes une dimension que si des indices visibles la "
    "soutiennent (porte standard ≈ 2,04 m de haut, carrelage, mobilier) ; sinon null. "
    "Tu n'inventes rien."
)

USER_PROMPT = """Analyse cette photo d'une pièce et renvoie exactement cette structure :
{
  "elements": [
    {"category": "<wall|floor|ceiling|door|window|radiator|beam|stairs|outlet|fixture|furniture>",
     "box": [x0, y0, x1, y1],
     "label": "<description courte>",
     "confidence": <0..1>}
  ],
  "room": {
    "shape": "<rectangular|l_shaped|other>",
    "width_m": <nombre ou null>,
    "depth_m": <nombre ou null>,
    "ceiling_height_m": <nombre ou null>,
    "confidence": <0..1>
  },
  "notes": "<ce qui limite ta lecture : angle, flou, occlusions>"
}
Les coordonnées de "box" sont des fractions de la largeur et de la hauteur de l'image,
entre 0 et 1, coin haut-gauche puis bas-droit. Liste chaque mur visible, chaque porte et
chaque fenêtre séparément."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class VlmParseError(ValueError):
    """Le modèle n'a pas renvoyé le JSON attendu."""


class LenientModel(BaseModel):
    """Sortie de modèle : les clés inconnues sont ignorées (les petits modèles en ajoutent)."""

    model_config = ConfigDict(extra="ignore")


class VlmElement(LenientModel):
    category: DetectedCategory
    box: tuple[float, float, float, float]
    label: str | None = None
    confidence: Confidence = 0.5


class VlmRoom(LenientModel):
    shape: Literal["rectangular", "l_shaped", "other"] = "other"
    width_m: float | None = Field(default=None, gt=0.0, lt=50.0)
    depth_m: float | None = Field(default=None, gt=0.0, lt=50.0)
    ceiling_height_m: float | None = Field(default=None, gt=1.5, lt=8.0)
    confidence: Confidence = 0.3


class VlmPerception(StrictModel):
    elements: list[VlmElement] = Field(default_factory=list)
    room: VlmRoom = Field(default_factory=VlmRoom)
    notes: str = ""
    dropped: int = 0
    """Entrées de `elements` ou champs de `room` rejetés par la validation."""


def parse_perception(text: str) -> VlmPerception:
    """Extrait et valide le JSON. Tolérant : un élément invalide est écarté (compté dans
    `dropped`), une pièce invalide devient la pièce par défaut ; seul un JSON absent ou
    illisible est une erreur."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        msg = "réponse sans objet JSON"
        raise VlmParseError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = f"JSON de perception illisible : {str(exc)[:160]}"
        raise VlmParseError(msg) from exc
    dropped = 0
    elements: list[VlmElement] = []
    raw_elements = data.get("elements")
    for item in raw_elements if isinstance(raw_elements, list) else []:
        try:
            elements.append(VlmElement.model_validate(item))
        except ValidationError:
            dropped += 1
    raw_room = data.get("room")
    try:
        room = VlmRoom.model_validate(raw_room if isinstance(raw_room, dict) else {})
    except ValidationError:
        room = VlmRoom()
        dropped += 1
    notes = data.get("notes")
    return VlmPerception(
        elements=elements,
        room=room,
        notes=notes if isinstance(notes, str) else "",
        dropped=dropped,
    )


class VlmRoomDetector:
    """Implémente RoomElementDetector par un modèle vision-langage."""

    def __init__(self, model: LanguageModel, send: Callable[[LlmRequest], LlmResponse]) -> None:
        if not model.supports_images:
            msg = f"{model.model_id} n'accepte pas d'images"
            raise ValueError(msg)
        self.model = model
        self.send = send
        self.model_id = f"vlm:{model.model_id}"

    def ask(self, image: ImageArray) -> LlmResponse:
        """Envoie la photo et les consignes ; renvoie la réponse brute."""
        request = LlmRequest(
            purpose="perception",
            messages=[
                LlmMessage(role="system", content=SYSTEM_PROMPT),
                LlmMessage(role="user", content=USER_PROMPT),
            ],
            images=[image],
            json_mode=True,
            temperature=0.1,
            max_tokens=1500,
        )
        return self.send(request)

    def perceive(self, image: ImageArray) -> tuple[VlmPerception, LlmResponse]:
        response = self.ask(image)
        return parse_perception(response.text), response

    def detect(self, image: ImageArray) -> list[DetectedElement]:
        perception, _ = self.perceive(image)
        return to_elements(perception, image.width, image.height, self.model_id)


PIXEL_THRESHOLD = 1.5
"""Au-delà, les coordonnées d'une boîte sont des pixels, pas des fractions : certains modèles
(qwen2.5-vl) répondent en pixels de l'image reçue malgré la consigne."""


def sent_size(width: int, height: int, max_side: int = MAX_IMAGE_SIDE) -> tuple[int, int]:
    """Taille de l'image telle qu'envoyée au modèle (réduite au grand côté `max_side`)."""
    longest = max(width, height)
    if longest <= max_side:
        return width, height
    scale = max_side / longest
    return max(1, round(width * scale)), max(1, round(height * scale))


def to_elements(
    perception: VlmPerception, width: int, height: int, model_id: str
) -> list[DetectedElement]:
    """Boîtes → pixels de l'image d'origine. Fractions 0–1 attendues ; des pixels (de l'image
    envoyée, éventuellement réduite) sont reconnus et rééchelonnés."""
    out: list[DetectedElement] = []
    sw, sh = sent_size(width, height)
    for e in perception.elements:
        x0, y0, x1, y1 = e.box
        if max(abs(v) for v in e.box) > PIXEL_THRESHOLD:
            x0, x1 = x0 / sw, x1 / sw
            y0, y1 = y0 / sh, y1 / sh
        px = (
            round(min(x0, x1) * width),
            round(min(y0, y1) * height),
            round(max(x0, x1) * width),
            round(max(y0, y1) * height),
        )
        px = (
            max(0, min(width, px[0])),
            max(0, min(height, px[1])),
            max(0, min(width, px[2])),
            max(0, min(height, px[3])),
        )
        if px[2] <= px[0] or px[3] <= px[1]:
            continue
        out.append(
            DetectedElement(
                category=e.category,
                bbox_px=px,
                score=min(MAX_SCORE, e.confidence),
                label=e.label,
                model_id=model_id,
            )
        )
    return out
