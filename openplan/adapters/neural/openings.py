"""Portes et fenêtres par OWLv2 (détection zéro-shot conditionnée par le texte).

`google/owlv2-base-patch16-ensemble`, Apache-2.0, 0,2 milliard de paramètres, chargé par
`transformers` depuis le dossier local des poids (`openplan models pull owlv2_base@1.0`), sans
accès réseau. Requêtes fixes en anglais (langue d'entraînement) ; chaque boîte garde la requête
qui l'a produite dans `label`. Ce que le modèle rend : des rectangles image avec un score ;
rien en mètres. La projection sur le mur hôte (largeur, allège) est faite ailleurs
(`pipeline.openings`, 2.4b) et reste `observed`, corrigeable.

Décision PO du 25 sept. 2026 (docs/models/openings-candidates.md) : OWLv2 base d'abord ;
Grounding DINO tiny seulement si le critère de la fixture n'est pas atteint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openplan.adapters.neural.weights import DEFAULT_WEIGHTS_ROOT, require_installed
from openplan.adapters.protocols import (
    AdapterUnavailableError,
    DetectedCategory,
    DetectedElement,
    ImageArray,
)

MODEL_ID = "owlv2_base@1.0"
HF_REPO = "google/owlv2-base-patch16-ensemble"
QUERIES: dict[str, DetectedCategory] = {
    "a door": "door",
    "an open doorway": "door",
    "a window": "window",
}
"""Requêtes texte → catégorie du dépôt. « doorway » rattrape les portes ouvertes."""
ROOF_QUERIES: dict[str, DetectedCategory] = {
    "a roof window": "roof_window",
    "a skylight": "roof_window",
    "a window": "window",
    "a door": "door",
}
"""Passe « fenêtres de toit » (C2) : les classes fenêtre et porte restent en concurrence pour
que chaque boîte prenne la requête qui lui va le mieux (une fenêtre de pignon reste une
fenêtre) ; seules les boîtes `roof_window` sont rendues."""
SCORE_THRESHOLD = 0.2
"""Seuil OWLv2 (scores bas par construction : 0,3 est déjà une détection franche)."""
SAME_BOX_IOU = 0.5
"""Deux boîtes de même catégorie qui se recouvrent plus que cela sont la même ouverture."""
MAX_SIDE = 1600


def box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter)


def merge_detections(elements: list[DetectedElement]) -> list[DetectedElement]:
    """Garde, par catégorie, la meilleure boîte de chaque groupe de boîtes qui se recouvrent
    (les requêtes « a door » et « an open doorway » voient souvent la même porte)."""
    kept: list[DetectedElement] = []
    for element in sorted(elements, key=lambda e: -e.score):
        if any(
            k.category == element.category and box_iou(k.bbox_px, element.bbox_px) > SAME_BOX_IOU
            for k in kept
        ):
            continue
        kept.append(element)
    return kept


def to_elements(
    boxes: list[tuple[float, float, float, float]],
    scores: list[float],
    labels: list[int],
    queries: list[str],
    image_size: tuple[int, int],
    model_id: str = MODEL_ID,
    mapping: dict[str, DetectedCategory] | None = None,
) -> list[DetectedElement]:
    """Sorties brutes (boîtes en pixels x0, y0, x1, y1 ; indices de requête) → éléments,
    bornés à l'image, sans les boîtes dégénérées."""
    width, height = image_size
    out: list[DetectedElement] = []
    for box, score, label in zip(boxes, scores, labels, strict=True):
        query = queries[int(label)]
        category = (QUERIES if mapping is None else mapping).get(query)
        if category is None:
            continue
        x0 = int(max(0, min(width - 1, round(box[0]))))
        y0 = int(max(0, min(height - 1, round(box[1]))))
        x1 = int(max(0, min(width, round(box[2]))))
        y1 = int(max(0, min(height, round(box[3]))))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        out.append(
            DetectedElement(
                category=category,
                bbox_px=(x0, y0, x1, y1),
                score=float(max(0.0, min(1.0, score))),
                label=query,
                model_id=model_id,
            )
        )
    return merge_detections(out)


class OwlV2OpeningDetector:
    """Implémente RoomElementDetector ; le modèle n'est chargé qu'au premier appel."""

    model_id = MODEL_ID

    def __init__(self, weights_root: Path = DEFAULT_WEIGHTS_ROOT, device: str = "cpu") -> None:
        self.weights = require_installed(MODEL_ID, weights_root)
        self.requested_device = device
        self.device = device
        self._model: Any = None
        self._processor: Any = None

    def _load(self) -> None:
        try:
            import torch
            from transformers import Owlv2ForObjectDetection, Owlv2Processor
        except ImportError as exc:
            msg = "torch/transformers absents : make setup-neural"
            raise AdapterUnavailableError(msg) from exc
        if self.requested_device == "cuda" and not torch.cuda.is_available():
            self.device = "cpu"
        load_processor: Any = Owlv2Processor.from_pretrained
        load_model: Any = Owlv2ForObjectDetection.from_pretrained
        self._processor = load_processor(str(self.weights), local_files_only=True)
        model = load_model(str(self.weights), local_files_only=True)
        self._model = model.to(self.device).eval()

    def detect(self, image: ImageArray) -> list[DetectedElement]:
        return self._detect(image, QUERIES)

    def detect_roof_windows(self, image: ImageArray) -> list[DetectedElement]:
        found = self._detect(image, ROOF_QUERIES)
        return [e for e in found if e.category == "roof_window"]

    def _detect(
        self, image: ImageArray, mapping: dict[str, DetectedCategory]
    ) -> list[DetectedElement]:
        if self._model is None:
            self._load()
        import torch
        from PIL import Image

        pil = Image.fromarray(image.pixels)
        queries = list(mapping)
        inputs = self._processor(text=[queries], images=pil, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._model(**inputs)
        # OWLv2 travaille sur une image carrée complétée en bas/à droite : la taille cible
        # est le grand côté, et les boîtes hors image sont bornées ensuite.
        side = max(image.width, image.height)
        results = self._processor.post_process_grounded_object_detection(
            outputs,
            threshold=SCORE_THRESHOLD,
            target_sizes=torch.tensor([[side, side]]),
            text_labels=[queries],
        )[0]
        boxes = [
            (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            for b in results["boxes"].detach().cpu().tolist()
        ]
        return to_elements(
            boxes,
            [float(s) for s in results["scores"].detach().cpu().tolist()],
            [int(v) for v in results["labels"].detach().cpu().tolist()],
            queries,
            (image.width, image.height),
            mapping=mapping,
        )
