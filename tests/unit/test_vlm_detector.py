"""Détecteur vision-langage : analyse de la réponse, conversion en pixels, garde-fous."""

from __future__ import annotations

from uuid import uuid4

import numpy as np
import pytest

from openplan.adapters.protocols import ImageArray, LlmRequest, LlmResponse
from openplan.adapters.remote.vlm_detector import (
    MAX_SCORE,
    VlmParseError,
    VlmRoomDetector,
    parse_perception,
    to_elements,
)

GOOD = """```json
{"elements": [
   {"category": "door", "box": [0.1, 0.2, 0.3, 0.9], "label": "porte blanche", "confidence": 0.9},
   {"category": "window", "box": [0.8, 0.1, 0.6, 0.5], "confidence": 0.4},
   {"category": "wall", "box": [0.5, 0.5, 0.5, 0.9], "confidence": 0.7}
 ],
 "room": {"shape": "rectangular", "width_m": 3.8, "depth_m": null,
          "ceiling_height_m": 2.5, "confidence": 0.5},
 "notes": "angle serré"}
```"""


class Model:
    model_id = "fake:vision"
    remote = False
    supports_images = True
    destination = "localhost"

    def complete(self, request: LlmRequest) -> LlmResponse:
        raise AssertionError("le détecteur doit passer par `send`, jamais par le modèle direct")


def test_parse_and_convert() -> None:
    perception = parse_perception(GOOD)
    assert perception.room.width_m == 3.8 and perception.room.depth_m is None
    assert perception.notes == "angle serré"
    elements = to_elements(perception, 1000, 500, "vlm:x")
    assert len(elements) == 2, "la boîte dégénérée (largeur nulle) est ignorée"
    door, window = elements
    assert door.bbox_px == (100, 100, 300, 450) and door.score == MAX_SCORE
    assert window.bbox_px == (600, 50, 800, 250), "coins réordonnés"
    assert window.score == 0.4 and window.model_id == "vlm:x"


def test_pixel_boxes_are_rescaled_from_sent_size() -> None:
    """qwen2.5-vl répond en pixels de l'image envoyée (réduite à 1600 px) : on rééchelonne."""
    from openplan.adapters.remote.vlm_detector import sent_size

    assert sent_size(1280, 960) == (1280, 960)
    assert sent_size(4000, 3000) == (1600, 1200)
    p = parse_perception(
        '{"elements": [{"category": "window", "box": [400, 0, 800, 600], "confidence": 0.9},'
        ' {"category": "door", "box": [0.1, 0.1, 0.2, 0.9]}]}'
    )
    window, door = to_elements(p, 4000, 3000, "m")
    assert window.bbox_px == (1000, 0, 2000, 1500), "pixels de l'image 1600×1200 → 4000×3000"
    assert door.bbox_px == (400, 300, 800, 2700), "les fractions restent des fractions"


def test_parse_errors_and_leniency() -> None:
    with pytest.raises(VlmParseError, match="sans objet JSON"):
        parse_perception("Je ne peux pas.")
    with pytest.raises(VlmParseError, match="illisible"):
        parse_perception('{"elements": [oops]}')
    # Élément inconnu écarté, pièce invalide remplacée, clés en trop ignorées : compté dans dropped.
    p = parse_perception(
        '{"elements": [{"category": "spaceship", "box": [0,0,1,1]}, '
        '{"category": "door", "box": [0,0,0.5,1], "extra": 1}], '
        '"room": {"ceiling_height_m": 12}, "type": "rectangular"}'
    )
    assert [e.category for e in p.elements] == ["door"] and p.dropped == 2
    assert p.room.ceiling_height_m is None
    assert parse_perception('texte avant {"elements": []} texte après').elements == []


def test_detector_uses_send_and_caps_scores() -> None:
    sent: list[LlmRequest] = []

    def send(request: LlmRequest) -> LlmResponse:
        sent.append(request)
        return LlmResponse(text=GOOD, model_id="fake:vision")

    detector = VlmRoomDetector(Model(), send)
    image = ImageArray(image_id=uuid4(), pixels=np.zeros((500, 1000, 3), dtype=np.uint8))
    elements = detector.detect(image)
    assert [e.category for e in elements] == ["door", "window"]
    assert all(e.score <= MAX_SCORE for e in elements)
    (request,) = sent
    assert request.purpose == "perception" and request.json_mode and len(request.images) == 1
    assert request.messages[0].role == "system"

    class Blind(Model):
        supports_images = False

    with pytest.raises(ValueError, match="n'accepte pas d'images"):
        VlmRoomDetector(Blind(), send)
