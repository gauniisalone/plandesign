"""Adaptateur OWLv2 : la partie sans modèle (conversion des sorties, fusion des boîtes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from openplan.adapters.neural.openings import (
    MODEL_ID,
    QUERIES,
    OwlV2OpeningDetector,
    box_iou,
    merge_detections,
    to_elements,
)
from openplan.adapters.protocols import AdapterUnavailableError, DetectedElement


def test_box_iou() -> None:
    assert box_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert box_iou((0, 0, 10, 10), (5, 0, 15, 10)) == pytest.approx(1 / 3)
    assert box_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_to_elements_maps_queries_clips_and_merges() -> None:
    queries = list(QUERIES)
    boxes = [
        (100.0, 200.0, 180.0, 400.0),  # porte
        (104.0, 205.0, 176.0, 398.0),  # même porte vue par « doorway »
        (-10.0, 50.0, 60.0, 120.0),  # fenêtre qui déborde à gauche : bornée
        (300.0, 300.0, 302.0, 320.0),  # dégénérée : ignorée
        (500.0, 100.0, 700.0, 250.0),  # fenêtre, hors image en x (image 640 px) : bornée
    ]
    scores = [0.31, 0.45, 0.25, 0.9, 1.2]
    labels = [0, 1, 2, 2, 2]
    elements = to_elements(boxes, scores, labels, queries, (640, 480))
    doors = [e for e in elements if e.category == "door"]
    windows = [e for e in elements if e.category == "window"]
    assert len(doors) == 1 and doors[0].label == "an open doorway" and doors[0].score == 0.45
    assert len(windows) == 2
    left = min(windows, key=lambda w: w.bbox_px[0])
    assert left.bbox_px == (0, 50, 60, 120)
    right = max(windows, key=lambda w: w.bbox_px[0])
    assert right.bbox_px == (500, 100, 640, 250) and right.score == 1.0
    assert all(e.model_id == MODEL_ID for e in elements)


def test_merge_keeps_best_per_overlap_group_and_category() -> None:
    def element(category: str, box: tuple[int, int, int, int], score: float) -> DetectedElement:
        return DetectedElement(category=category, bbox_px=box, score=score, model_id="x")  # type: ignore[arg-type]

    merged = merge_detections(
        [
            element("door", (0, 0, 10, 20), 0.3),
            element("door", (1, 1, 10, 20), 0.5),
            element("window", (1, 1, 10, 20), 0.4),  # même boîte, autre catégorie : gardée
            element("door", (50, 0, 60, 20), 0.2),
        ]
    )
    assert [(e.category, e.score) for e in merged] == [
        ("door", 0.5),
        ("window", 0.4),
        ("door", 0.2),
    ]


def test_detector_requires_installed_weights(tmp_path: Path) -> None:
    with pytest.raises(AdapterUnavailableError, match=r"models pull owlv2_base@1\.0"):
        OwlV2OpeningDetector(tmp_path)
