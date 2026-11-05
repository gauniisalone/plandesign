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
RELATIVE = 0.15
"""KAN-97b : un mot est retenu s'il atteint cette part du score du meilleur mot. Les scores de
SigLIP2 (sigmoïde, moyennée sur les images) n'ont pas la même échelle d'une image à l'autre :
essai du 5 nov., A (japandi) en tête à 0,61, B (industriel) à 0,26 avec tout le reste sous
0,05. Le seuil absolu de 0,10 gardait 5 mots pour A et le seul « moody » pour B. À 0,15 du
premier : A garde les mêmes 5 mots (le 5e, « serene », à 0,101 pour un seuil de 0,092 ; le 6e,
« earthy tones », à 0,072, reste dehors), B en gagne un (« black accents », 0,045 pour 0,039)
sans le bruit qui suit (« minimalist », 0,032, et tout ce qui est sous 0,01)."""
MIN_SCORE = 0.02
"""Le plancher : en dessous, un mot n'est jamais retenu, même premier. Une image sans style
marqué (meilleur score bas) ne force donc aucun mot ; dans l'essai, le bruit est sous 0,01."""
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
    StyleWord("framed artwork", "cadres aux murs", "touch"),
    StyleWord("layered rugs", "tapis superposés", "touch"),
    StyleWord("vintage decor", "objets chinés", "touch"),
)
BY_KEY: dict[str, StyleWord] = {w.key: w for w in VOCABULARY}


def prompts() -> list[str]:
    """Les textes zéro-shot, dans l'ordre du vocabulaire."""
    return [PROMPT.format(w.key) for w in VOCABULARY]


TAG_PREFIX = "s:"
"""KAN-130 : un mot désactivé sur une image d'inspiration (`BoardItem.ignored_tags`), au motif
du schéma (`s:<style>`, sans espace) : les espaces de la clé deviennent « _ », qu'aucune clé du
vocabulaire ne contient."""


def tag_of(key: str) -> str:
    """L'étiquette d'`ignored_tags` d'un mot du vocabulaire (`s:white_and_beige`)."""
    return TAG_PREFIX + key.replace(" ", "_")


def key_of(tag: str) -> str | None:
    """Le mot du vocabulaire d'une étiquette d'`ignored_tags`, ou None (autre étiquette)."""
    if not tag.startswith(TAG_PREFIX) or " " in tag:
        return None
    key = tag[len(TAG_PREFIX) :].replace("_", " ")
    return key if key in BY_KEY else None


def choose(
    scores: Mapping[str, float], scene_words: Iterable[str] = (), ignored: Iterable[str] = ()
) -> list[str]:
    """Les mots retenus (clés du vocabulaire), du plus probable au moins probable : au moins
    RELATIVE du meilleur score et MIN_SCORE (KAN-97b), au plus CAPS par groupe et MAX_WORDS en
    tout ; un mot de palette qui nomme une couleur déjà présente dans la description de la
    scène est écarté. KAN-130 : un mot désactivé (`ignored`) n'est jamais retenu ; le suivant
    de son groupe prend sa place, et le meilleur score se lit sans lui."""
    scene = " ".join(scene_words).lower()
    off = frozenset(ignored)
    best = max((s for k, s in scores.items() if k in BY_KEY and k not in off), default=0.0)
    cut = max(MIN_SCORE, RELATIVE * best)
    taken: dict[Group, int] = dict.fromkeys(CAPS, 0)
    out: list[str] = []
    for key, score in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])):
        word = BY_KEY.get(key)
        if word is None or key in off or score < cut or len(out) >= MAX_WORDS:
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
