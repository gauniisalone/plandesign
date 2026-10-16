"""Phrases-types comprises sans modèle."""

from __future__ import annotations

import pytest

from openplan.pipeline.assistant_phrases import understand
from openplan.scene.catalog import load_catalog

CATEGORIES = load_catalog().categories


@pytest.mark.parametrize(
    ("text", "kind", "category", "near", "away", "target"),
    [
        ("ajoute un bureau près de la fenêtre", "add_furniture", "desk", ["window"], [], None),
        (
            "Mets un lit 160 × 200 loin de la porte",
            "add_furniture",
            "bed_double",
            [],
            ["door"],
            None,
        ),
        ("il me faut deux tables de chevet", "add_furniture", "nightstand", [], [], None),
        ("pose une armoire contre le mur", "add_furniture", "wardrobe", ["wall"], [], None),
        ("éloigne l'armoire de la fenêtre", "move_furniture", None, [], ["window"], "l'armoire"),
        (
            "déplace le bureau près de la fenêtre",
            "move_furniture",
            None,
            ["window"],
            [],
            "le bureau",
        ),
        ("enlève la table de chevet", "remove_furniture", None, [], [], "la table de chevet"),
        ("supprime le piano", "remove_furniture", None, [], [], "le piano"),
        ("propose-moi trois aménagements", "propose_layouts", None, [], [], None),
        ("quelle surface fait la pièce ?", "ask", None, [], [], None),
    ],
)
def test_phrases(
    text: str, kind: str, category: str | None, near: list[str], away: list[str], target: str | None
) -> None:
    reply = understand(text, CATEGORIES)
    assert reply is not None, text
    intent = reply.intents[0]
    assert intent.kind == kind
    if category is not None:
        assert intent.category == category
    assert intent.near == near and intent.away_from == away
    if target is not None:
        assert intent.target == target


def test_counts_dimensions_and_unknown() -> None:
    reply = understand("il me faut deux tables de chevet", CATEGORIES)
    assert reply is not None and reply.intents[0].count == 2
    reply = understand("mets un lit 160 × 200 loin de la porte", CATEGORIES)
    assert reply is not None and reply.intents[0].dimensions_m is not None
    assert reply.intents[0].dimensions_m.width == pytest.approx(1.6)
    assert reply.intents[0].dimensions_m.depth == pytest.approx(2.0)
    assert understand("bonjour, il fait beau", CATEGORIES) is None
    assert understand("", CATEGORIES) is None
