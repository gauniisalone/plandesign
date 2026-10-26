"""KAN-97 : les cartes Inspirations reliées à la carte Rendus, dites en mots pour Z-Image.

Le poste de rendu décrit les images de style (C1, au plus `MAX_IMAGES`) avec SigLIP2 en
zéro-shot, sur ce vocabulaire fermé. Aucun mot ne nomme une matière de surface (sol, murs,
poutres) : ce que les photos disent de la pièce n'est jamais contredit. Les mots retenus forment
le groupe « style » de la description du rendu (KAN-86), au plus `MAX_WORDS`, et reviennent au
serveur pour « Guidé par : … » sur la carte Rendus, en français.

Ce module est pur (aucun modèle) : le choix se teste avec des scores donnés.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Group = Literal["style", "mood", "palette", "touch"]

MAX_IMAGES = 6
MAX_WORDS = 8
CAPS: dict[Group, int] = {"style": 2, "mood": 3, "palette": 2, "touch": 1}
"""Au plus 2 styles, 3 ambiances, 2 palettes, 1 touche : 8 mots."""
MIN_SCORE = 0.10
"""Probabilité zéro-shot moyenne (sigmoïde de SigLIP2) en dessous de laquelle un mot n'est pas
retenu : une image sans style marqué ne force aucun mot."""
PROMPT = "a photo of a {} interior"
"""Gabarit des textes comparés aux images (anglais : la langue d'entraînement de SigLIP2)."""


@dataclass(frozen=True)
class StyleWord:
    key: str
    """Le mot du prompt (anglais), aussi la clé échangée entre le poste et le serveur."""
    french: str
    """Ce que la carte Rendus affiche."""
    group: Group
    colors: tuple[str, ...] = ()
    """Couleurs que le mot nomme : retiré si la scène donne déjà l'une d'elles à une surface."""


VOCABULARY: tuple[StyleWord, ...] = (
    StyleWord("scandinavian", "scandinave", "style"),
    StyleWord("japandi", "japandi", "style"),
    StyleWord("industrial", "industriel", "style"),
    StyleWord("bohemian", "bohème", "style"),
    StyleWord("mid-century modern", "années 50", "style"),
    StyleWord("minimalist", "minimaliste", "style"),
    StyleWord("rustic", "rustique", "style"),
    StyleWord("farmhouse", "maison de campagne", "style"),
    StyleWord("art deco", "art déco", "style"),
    StyleWord("coastal", "bord de mer", "style"),
    StyleWord("mediterranean", "méditerranéen", "style"),
    StyleWord("wabi-sabi", "wabi-sabi", "style"),
    StyleWord("contemporary", "contemporain", "style"),
    StyleWord("classic", "classique", "style"),
    StyleWord("cozy", "chaleureux", "mood"),
    StyleWord("airy", "aéré", "mood"),
    StyleWord("bright", "lumineux", "mood"),
    StyleWord("moody", "intimiste", "mood"),
    StyleWord("warm", "doux", "mood"),
    StyleWord("calm", "calme", "mood"),
    StyleWord("serene", "serein", "mood"),
    StyleWord("lived-in", "vécu", "mood"),
    StyleWord("white and beige", "blanc et beige", "palette", ("white", "beige", "off-white")),
    StyleWord("warm neutrals", "tons chauds", "palette", ("sand", "linen")),
    StyleWord("earthy tones", "tons terre", "palette", ("ochre", "terracotta")),
    StyleWord("sage green accents", "touches de vert sauge", "palette", ("sage green",)),
    StyleWord("terracotta accents", "touches de terracotta", "palette", ("terracotta",)),
    StyleWord("black accents", "touches de noir", "palette", ("black",)),
    StyleWord("soft pastels", "pastels doux", "palette", ("dusty pink",)),
    StyleWord("navy blue accents", "touches de bleu nuit", "palette", ("navy blue",)),
    StyleWord("monochrome", "monochrome", "palette"),
    StyleWord("indoor plants", "plantes", "touch"),
    StyleWord("framed artwork", "tableaux encadrés", "touch"),
    StyleWord("layered rugs", "tapis superposés", "touch"),
    StyleWord("vintage decor", "objets chinés", "touch"),
)
BY_KEY: dict[str, StyleWord] = {w.key: w for w in VOCABULARY}


def prompts() -> list[str]:
    """Les textes zéro-shot, dans l'ordre du vocabulaire."""
    return [PROMPT.format(w.key) for w in VOCABULARY]


def choose(scores: Mapping[str, float], scene_words: Iterable[str] = ()) -> list[str]:
    """Les mots retenus (clés du vocabulaire), du plus probable au moins probable : au-dessus
    de MIN_SCORE, au plus CAPS par groupe et MAX_WORDS en tout ; un mot de palette qui nomme
    une couleur déjà présente dans la description de la scène est écarté."""
    scene = " ".join(scene_words).lower()
    taken: dict[Group, int] = dict.fromkeys(CAPS, 0)
    out: list[str] = []
    for key, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        word = BY_KEY.get(key)
        if word is None or score < MIN_SCORE or len(out) >= MAX_WORDS:
            continue
        if taken[word.group] >= CAPS[word.group]:
            continue
        if any(c in scene for c in word.colors):
            continue
        taken[word.group] += 1
        out.append(key)
    return out


def french(keys: Iterable[str]) -> list[str]:
    """Les mots retenus tels que la carte Rendus les affiche ; une clé inconnue est ignorée
    (un poste plus récent que le serveur)."""
    return [BY_KEY[k].french for k in keys if k in BY_KEY]
