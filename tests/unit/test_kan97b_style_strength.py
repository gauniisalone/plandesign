"""KAN-97b : les inspirations pèsent vraiment sur le rendu Z-Image (essai du 5 nov. : A et B
presque identiques, écart de 14/255). Seuil relatif dans `choose`, et le début fixe du prompt
qui ne contredit plus les mots de style. Scores : les 10 meilleurs de SigLIP2 pour A et B."""

from __future__ import annotations

from openplan.pipeline import style_words as sw
from openplan.pipeline.finish import STYLE_WORDS, finish_prompt
from openplan.pipeline.scene_prompt import SceneWords
from tests.factories import only_level, rect_room_project

SCORES_A = {
    "japandi": 0.6116266846656799,
    "scandinavian": 0.21021239459514618,
    "warm neutrals": 0.13664820790290833,
    "calm": 0.12863701581954956,
    "serene": 0.10066620260477066,
    "earthy tones": 0.07223150134086609,
    "cozy": 0.06959106028079987,
    "airy": 0.05278544872999191,
    "minimalist": 0.0483524389564991,
    "warm": 0.0477481409907341,
}
SCORES_B = {
    "moody": 0.26278215646743774,
    "black accents": 0.045150112360715866,
    "minimalist": 0.03199999779462814,
    "mid-century modern": 0.02209343947470188,
    "monochrome": 0.021832700818777084,
    "cozy": 0.009809826500713825,
    "calm": 0.008847346529364586,
    "contemporary": 0.008188854902982712,
    "vintage decor": 0.007013588212430477,
    "warm": 0.0069603570736944675,
}
DEFAULT_START = ("warm wood", "linen", "soft natural daylight", "natural materials")


def test_choose_keeps_the_strong_words_of_a() -> None:
    """A : les 5 mots de l'essai, rien du bruit qui suit (« earthy tones » à 0,072)."""
    assert sw.choose(SCORES_A) == ["japandi", "scandinavian", "warm neutrals", "calm", "serene"]


def test_choose_gives_b_more_than_one_word() -> None:
    """B : le seuil absolu de 0,10 ne gardait que « moody » ; relatif, « black accents »
    (0,045, 17 % du premier) le rejoint, pas « minimalist » (0,032, 12 %)."""
    assert sw.choose(SCORES_B) == ["moody", "black accents"]


def test_the_floor_still_forces_nothing_on_a_flat_image() -> None:
    flat = {k: s / 20 for k, s in SCORES_B.items()}  # meilleur à 0,013 : sous le plancher
    assert sw.choose(flat) == []


def test_a_disabled_best_word_does_not_set_the_cut() -> None:
    """KAN-130 : sans « moody » (désactivé), le seuil se lit sur « black accents »."""
    assert sw.choose(SCORES_B, ignored=["moody"]) == [
        "black accents",
        "minimalist",
        "mid-century modern",
        "monochrome",
    ]


def _prompts(words: list[str], style: str | None = None) -> tuple[str, str]:
    project = rect_room_project()
    level = only_level(project)
    plain = SceneWords(surfaces=["white plaster walls"], furniture=["a double bed"])
    styled = SceneWords(surfaces=plain.surfaces, furniture=plain.furniture, style=words)
    return (
        finish_prompt(project, style, level.id, scene=plain, engine="zimage"),
        finish_prompt(project, style, level.id, scene=styled, engine="zimage"),
    )


def test_without_style_words_the_default_start_is_unchanged() -> None:
    before, _ = _prompts([])
    assert STYLE_WORDS["naturel"] in before


def test_style_words_replace_the_default_start_and_come_first() -> None:
    """B : « moody, black accents » juste après la pièce, plus de bois chaud ni de lin ni de
    lumière douce ; les surfaces réelles et les meubles restent."""
    _, after = _prompts(sw.choose(SCORES_B))
    lead = "photorealistic interior photograph of a"
    assert after.startswith(lead)
    assert after.index("moody, black accents") == after.index(",") + 2, "juste après la pièce"
    for word in DEFAULT_START:
        assert word not in after, word
    assert "white plaster walls" in after and "a double bed" in after


def test_a_and_b_now_differ_from_their_start() -> None:
    _, a = _prompts(sw.choose(SCORES_A))
    _, b = _prompts(sw.choose(SCORES_B))
    assert a.split(",")[1].strip() == "japandi" and b.split(",")[1].strip() == "moody"


def test_a_style_chosen_for_the_room_is_kept() -> None:
    """Un style choisi (ici « industriel » forcé) n'est pas le début fixe : il reste."""
    _, after = _prompts(["japandi"], style="industriel")
    assert STYLE_WORDS["industriel"] in after
    assert after.index("japandi") < after.index("industrial loft")
