"""Consigne libre → intention de design (V1, note 10, point 5).

« grenier chaleureux, bois et lin, deux lits » devient des champs de `DesignIntent` :
1. par le **modèle** configuré quand il y en a un (texte seul, jamais la photo ; consentement
   du projet pour un modèle distant), dont la réponse JSON est validée champ par champ ;
2. sinon, et pour compléter ce que le modèle a oublié, par des **phrases simples** : un petit
   vocabulaire français fermé (styles, humeurs, couleurs, matières par surface, densité,
   lumière, type de pièce, meubles voulus ou à éviter, zones, format).

Rien n'est deviné : un mot inconnu n'est pas traduit, et ce qui n'a pas d'équivalent
(« cheminée ») est dit dans les notes. Une couleur dite seule (une ou deux) est complétée par
la palette du style, et c'est dit. La traduction est appliquée en une fois, champs marqués
`user_defined` (l'utilisateur les a écrits), annulable ; la consigne est gardée dans
l'intention pour être relue dans la fiche « Ambiance ».
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationError

from openplan.adapters.gateway import ModelGateway
from openplan.adapters.protocols import LanguageModel, LlmMessage, LlmRequest
from openplan.pipeline.assistant_phrases import SYNONYMS, fold
from openplan.schemas.assistant import ROOM_TYPES
from openplan.schemas.common import StrictModel
from openplan.schemas.intent import (
    COLORS,
    INTENT_FIELDS,
    MOOD_LABELS,
    STYLES,
    ZONE_LABELS,
    DesignIntent,
)
from openplan.schemas.scene import Project

PHRASES = "phrases"
MAX_INSTRUCTION = 300

STYLE_WORDS: dict[str, tuple[str, ...]] = {
    "scandinave": ("scandinave", "scandinaves", "scandi", "nordique", "nordiques", "hygge"),
    "industriel": ("industriel", "industrielle", "industriels", "loft", "atelier", "usine"),
    "chalet": ("chalet", "montagne", "rustique", "refuge", "savoyard"),
    "contemporain": ("contemporain", "contemporaine", "moderne"),
    "naturel": ("naturel", "naturelle", "nature", "boheme", "boho", "japandi"),
}
MOOD_WORDS: dict[str, tuple[str, ...]] = {
    "chaleureux": ("chaleureux", "chaleureuse", "chaleureuses", "accueillant", "accueillante"),
    "lumineux": ("lumineux", "lumineuse", "lumineuses", "baigne de lumiere"),
    "epure": ("epure", "epuree", "epures", "epurees", "minimaliste", "minimal"),
    "cosy": ("cosy", "cocooning", "cocon", "douillet", "douillette", "intime"),
    "brut": ("brut", "brute", "bruts", "brutes"),
}
COLOR_WORDS: dict[str, str] = {
    "blanc casse": "blanc casse",
    "blanc": "blanc casse",
    "blancs": "blanc casse",
    "ecru": "blanc casse",
    "creme": "blanc casse",
    "lin": "lin",
    "beige": "sable",
    "sable": "sable",
    "terracotta": "terracotta",
    "terre cuite": "terracotta",
    "rouille": "terracotta",
    "ocre": "ocre",
    "moutarde": "ocre",
    "jaune": "ocre",
    "vert sauge": "vert sauge",
    "sauge": "vert sauge",
    "vert foret": "vert foret",
    "vert sapin": "vert foret",
    "vert fonce": "vert foret",
    "vert": "vert sauge",
    "bleu nuit": "bleu nuit",
    "bleu marine": "bleu nuit",
    "bleu gris": "bleu gris",
    "bleu": "bleu gris",
    "gris anthracite": "gris anthracite",
    "anthracite": "gris anthracite",
    "gris": "gris anthracite",
    "noir": "noir",
    "chene clair": "chene clair",
    "bois clair": "chene clair",
    "noyer": "noyer",
    "rose poudre": "rose poudre",
    "rose": "rose poudre",
}
STYLE_PALETTES: dict[str, list[str]] = {
    "naturel": ["lin", "sable", "chene clair"],
    "chalet": ["noyer", "terracotta", "lin"],
    "scandinave": ["blanc casse", "lin", "chene clair"],
    "industriel": ["gris anthracite", "noyer", "terracotta"],
    "contemporain": ["blanc casse", "gris anthracite", "noir"],
}
"""Palette de chaque style, pour compléter une ou deux couleurs dites (3 au moins)."""
MATERIAL_WORDS: dict[str, tuple[str, str]] = {
    "beton cire": ("floor", "concrete_floor"),
    "sol en beton": ("floor", "concrete_floor"),
    "beton": ("floor", "concrete_floor"),
    "parquet clair": ("floor", "wood_floor"),
    "plancher clair": ("floor", "wood_floor"),
    "parquet fonce": ("floor", "oak_dark"),
    "chene fonce": ("floor", "oak_dark"),
    "parquet ancien": ("floor", "wood_dark"),
    "vieux parquet": ("floor", "wood_dark"),
    "parquet": ("floor", "wood_floor"),
    "pierre apparente": ("gables", "stone"),
    "murs en pierre": ("gables", "stone"),
    "moellons": ("gables", "stone_rubble"),
    "pierre": ("gables", "stone"),
    "murs blancs a la chaux": ("walls", "limewash"),
    "murs blancs": ("walls", "limewash"),
    "chaux": ("walls", "limewash"),
    "enduit blanc": ("walls", "limewash"),
    "murs en bois": ("walls", "wood_boards"),
    "lambris blanc": ("slopes", "painted_wood"),
    "lambris peint": ("slopes", "painted_wood"),
    "bois peint": ("slopes", "painted_wood"),
    "lambris": ("slopes", "wood_boards"),
    "poutres blanches": ("timber", "painted_wood"),
    "charpente blanche": ("timber", "painted_wood"),
    "poutres peintes": ("timber", "painted_wood"),
    "poutres sombres": ("timber", "oak_dark"),
    "poutres foncees": ("timber", "oak_dark"),
    "charpente sombre": ("timber", "oak_dark"),
    "poutres apparentes": ("timber", "timber"),
    "charpente apparente": ("timber", "timber"),
}
"""Matière par surface (sol, murs, pignons, pentes, charpente) ; le plus long l'emporte."""
DENSITY_WORDS: dict[str, tuple[str, ...]] = {
    "sobre": ("sobre", "sobres", "peu de deco", "peu d'objets", "minimaliste", "minimal"),
    "riche": (
        "riche",
        "charge",
        "chargee",
        "boheme",
        "boho",
        "foisonnant",
        "beaucoup de deco",
        "plein de deco",
        "beaucoup de plantes",
    ),
}
TIME_WORDS: dict[str, tuple[str, ...]] = {
    "heure_doree": (
        "heure doree",
        "golden hour",
        "coucher de soleil",
        "fin d'apres-midi",
        "fin de journee",
    ),
    "matin": ("matin", "matinale", "lever du soleil"),
    "midi": ("midi", "plein jour", "plein soleil"),
    "soir": ("soir", "soiree", "nuit", "tamisee", "tamise"),
}
WARMTH_WORDS: dict[str, tuple[str, ...]] = {
    "chaude": ("lumiere chaude", "eclairage chaud", "lumieres chaudes", "lumiere douce"),
    "froide": ("lumiere froide", "eclairage froid"),
    "neutre": ("lumiere neutre", "eclairage neutre"),
}
ROOM_WORDS: list[tuple[str, str, str | None]] = [
    ("chambre d'adolescent", "bedroom", "chambre d'ado"),
    ("chambre d'ado", "bedroom", "chambre d'ado"),
    ("chambre d'amis", "bedroom", "chambre d'amis"),
    ("chambre d'enfant", "bedroom", "chambre d'enfant"),
    ("chambre de bebe", "bedroom", "chambre de bébé"),
    ("suite parentale", "bedroom", "suite parentale"),
    ("chambre parentale", "bedroom", "suite parentale"),
    ("chambre", "bedroom", None),
    ("salle de bain", "bathroom", None),
    ("salle d'eau", "bathroom", None),
    ("piece a vivre", "living_room", None),
    ("salon", "living_room", None),
    ("sejour", "living_room", None),
    ("living", "living_room", None),
    ("cuisine", "kitchen", None),
    ("entree", "hallway", None),
    ("couloir", "hallway", None),
    ("combles", "attic", None),
    ("grenier", "attic", None),
    ("sous les toits", "attic", None),
    ("sous les combles", "attic", None),
]
"""Type de pièce (et usage précis) ; le premier nommé dans la consigne l'emporte."""
ZONE_WORDS: dict[str, tuple[str, ...]] = {
    "lecture": ("coin lecture", "espace lecture", "zone lecture", "pour lire", "liseuse"),
    "bureau": (
        "coin bureau",
        "espace bureau",
        "espace de travail",
        "coin travail",
        "pour travailler",
        "teletravail",
    ),
    "detente": ("coin detente", "coin salon", "espace detente", "coin tele"),
    "rangement": ("rangements", "beaucoup de rangement", "coin rangement"),
    "couchage": ("coin nuit", "espace nuit", "pour dormir", "couchage"),
}
MAIN_ZONE: dict[str, str] = {
    "bedroom": "couchage",
    "attic": "couchage",
    "living_room": "detente",
    "office": "bureau",
}
"""Zone principale ajoutée quand la consigne nomme des zones (« chambre avec coin lecture »
garde son couchage)."""
PORTRAIT_WORDS = ("format portrait", "portrait", "pinterest", "4:5", "format vertical")
LANDSCAPE_WORDS = ("format paysage", "paysage", "4:3")
UNKNOWN_THINGS: dict[str, str] = {
    "cheminee": "cheminée",
    "poele": "poêle",
    "piano": "piano",
    "plantes": "plantes",
    "baignoire ilot": "baignoire îlot",
    "verriere": "verrière",
}
"""Ce qui se dit souvent mais n'est pas au catalogue : noté, pas inventé."""
_AVOID = re.compile(r"\b(sans|pas de|pas d'|aucun|aucune|ni)\s+$")
RUG_FAMILY = ("rug", "rug_round")


class Translation(StrictModel):
    """Ce que la consigne a donné : les champs de l'intention, ce qui a été compris (une
    phrase par champ), les notes (complété, pas au catalogue), ce que le modèle a proposé et
    qui a été écarté, et qui a traduit."""

    fields: dict[str, JsonValue] = Field(default_factory=dict)
    understood: list[str] = Field(default_factory=list)
    summary: list[str] = Field(default_factory=list)
    """Les mêmes choses en mots simples, dans l'ordre de la consigne (« grenier ·
    chaleureux · lin · 2 lits simples ») : la ligne « Compris » de l'accueil."""
    notes: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    by: str = PHRASES


# ------------------------------------------------------------------ mots du résumé
ROOM_LABELS: dict[str, str] = {
    "attic": "grenier",
    "bedroom": "chambre",
    "living_room": "séjour",
    "kitchen": "cuisine",
    "bathroom": "salle de bain",
    "office": "bureau",
    "hallway": "entrée",
}
_ACCENTS: dict[str, str] = {
    "a": "à",
    "beton": "béton",
    "casse": "cassé",
    "chene": "chêne",
    "cire": "ciré",
    "doree": "dorée",
    "epure": "épuré",
    "epuree": "épurée",
    "lumiere": "lumière",
    "pres": "près",
}
_PREPOSITIONS = ("de", "d'", "à", "a", "en", "pour", "sans")


def accented(word: str) -> str:
    """Un mot replié (« chene clair ») remis en français (« chêne clair »)."""
    return " ".join(_ACCENTS.get(token, token) for token in word.split(" "))


def _plural(name: str) -> str:
    """« lit simple » → « lits simples », « table de nuit » → « tables de nuit »."""
    out: list[str] = []
    stop = False
    for token in name.split(" "):
        if token in _PREPOSITIONS or token.startswith("d'"):
            stop = True
        if stop or token.endswith(("s", "x", "z")):
            out.append(token)
        elif token.endswith(("eau", "au", "eu")):
            out.append(token + "x")
        else:
            out.append(token + "s")
    return " ".join(out)


def furniture_words(category: str, count: int) -> str:
    """« 2 lits simples », « une armoire » : le nom courant de la catégorie."""
    names = SYNONYMS.get(category)
    name = accented(names[0]) if names else category.replace("_", " ")
    return name if count <= 1 else f"{count} {_plural(name)}"


# ------------------------------------------------------------------ phrases simples
def _spans(text: str, word: str) -> list[tuple[int, int]]:
    return [m.span() for m in re.finditer(rf"(?<![\w']){re.escape(word)}(?![\w])", text)]


class _Reader:
    """La consigne repliée (minuscules, sans accents) et les passages déjà lus : un mot pris
    par une matière (« murs blancs ») n'est pas relu comme couleur."""

    def __init__(self, text: str) -> None:
        self.text = fold(text)
        self.used: list[tuple[int, int]] = []
        self.marks: list[tuple[int, str]] = []
        """(position, mot simple) de ce qui a été compris, pour le résumé."""

    def mark(self, pos: int, label: str) -> None:
        self.marks.append((max(0, pos), label))

    def position(self, word: str) -> int:
        spans = _spans(self.text, word)
        return spans[0][0] if spans else len(self.text)

    def free(self, span: tuple[int, int]) -> bool:
        return not any(span[0] < b and a < span[1] for a, b in self.used)

    def find(self, words: tuple[str, ...] | list[str], *, take: bool = True) -> tuple[int, str]:
        """(position, mot) du plus long mot présent et libre ; (-1, "") sinon."""
        for word in sorted(words, key=len, reverse=True):
            for span in _spans(self.text, word):
                if self.free(span):
                    if take:
                        self.used.append(span)
                    return span[0], word
        return -1, ""

    def all(self, table: dict[str, Any]) -> list[tuple[int, str, Any]]:
        """Tous les mots d'une table, dans l'ordre de la consigne, sans chevauchement."""
        found: list[tuple[int, str, Any]] = []
        for word in sorted(table, key=len, reverse=True):
            for span in _spans(self.text, word):
                if self.free(span):
                    self.used.append(span)
                    found.append((span[0], word, table[word]))
        return sorted(found)


def _first_key(reader: _Reader, table: dict[str, tuple[str, ...]]) -> tuple[str | None, str]:
    best: tuple[int, str, str] | None = None
    for key, words in table.items():
        pos, word = reader.find(words, take=False)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, key, word)
    if best is None:
        return None, ""
    reader.find((best[2],))
    return best[1], best[2]


def _room(reader: _Reader, out: Translation) -> str | None:
    text = reader.text
    # « bureau » est une pièce quand la consigne commence par lui ; sinon c'est un meuble.
    if re.match(r"^(un |le |mon )?bureau\b", text):
        reader.used.append(_spans(text, "bureau")[0])
        out.fields["room_type"] = "office"
        out.understood.append("pièce : bureau")
        reader.mark(0, "bureau")
        return "office"
    best: tuple[int, str, str, str | None] | None = None
    for word, room_type, usage in ROOM_WORDS:
        spans = [s for s in _spans(text, word) if reader.free(s)]
        if spans and (
            best is None
            or spans[0][0] < best[0]
            or (spans[0][0] == best[0] and len(word) > len(best[1]))
        ):
            best = (spans[0][0], word, room_type, usage)
    if best is None:
        return None
    _, word, room_type, usage = best
    reader.find((word,))
    out.fields["room_type"] = room_type
    out.understood.append(f"pièce : {word}")
    reader.mark(best[0], usage or ROOM_LABELS.get(room_type, word))
    if usage:
        out.fields["usage"] = usage
    # Les autres mots de pièce (« sous les toits » après « bureau ») ne changent rien.
    for other, _, _ in ROOM_WORDS:
        reader.find((other,))
    return room_type


def _furniture(reader: _Reader, out: Translation, categories: list[str]) -> None:
    words = sorted(
        ((w, c) for c, ws in SYNONYMS.items() if c in categories for w in ws),
        key=lambda wc: -len(wc[0]),
    )
    wanted: dict[str, int] = {}
    first: dict[str, int] = {}
    avoid: list[JsonValue] = []
    found: list[tuple[int, int, str, str]] = []
    for word, category in words:
        for m in re.finditer(rf"\b{re.escape(word)}s?\b", reader.text):
            if reader.free(m.span()):
                reader.used.append(m.span())
                found.append((m.start(), m.end(), category, word))
    for start, _, named, word in sorted(found):
        head = reader.text[:start]
        if _AVOID.search(head[-12:]):
            family = RUG_FAMILY if named in RUG_FAMILY else (named,)
            avoid.extend(c for c in family if c in categories and c not in avoid)
            reader.mark(start, f"sans {furniture_words(named, 1)}")
            continue
        count = _count(head)
        twins = named == "bed_double" and word == "lit" and count >= 2
        # « deux lits » : des lits simples.
        category = "bed_single" if twins and "bed_single" in categories else named
        wanted[category] = max(wanted.get(category, 0), count)
        first.setdefault(category, start)
    for category, n in wanted.items():
        reader.mark(first[category], furniture_words(category, n))
    if wanted:
        out.fields["furniture"] = [{"category": c, "count": n} for c, n in wanted.items()]
        out.understood.append("meubles : " + ", ".join(f"{n} × {c}" for c, n in wanted.items()))
    if avoid:
        out.fields["avoid"] = avoid
        out.understood.append("à éviter : " + ", ".join(str(a) for a in avoid))


_COUNTS = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6}


def _count(head: str) -> int:
    tail = head[-14:]
    for token, value in _COUNTS.items():
        if re.search(rf"\b{token}\s+(\w+\s+)?$", tail):
            return value
    m = re.search(r"\b(\d{1,2})\s+$", tail)
    return max(1, min(12, int(m.group(1)))) if m else 1


def _materials(reader: _Reader, out: Translation) -> None:
    materials: dict[str, JsonValue] = {}
    for pos, word, (surface, finish) in reader.all(MATERIAL_WORDS):
        if surface not in materials:
            materials[surface] = finish
            reader.mark(pos, accented(word))
            out.understood.append(f"{word} → {surface} : {finish}")
    if materials:
        out.fields["materials"] = materials


def _style_and_mood(reader: _Reader, out: Translation) -> str | None:
    style, word = _first_key(reader, STYLE_WORDS)
    if style:
        out.fields["style"] = style
        out.understood.append(f"style : {STYLES[style]} ({word})")
        reader.mark(reader.position(word), STYLES[style].lower())
    moods: list[tuple[int, str]] = []
    for mood, words in MOOD_WORDS.items():
        pos, _ = reader.find(words, take=False)
        if pos >= 0:
            moods.append((pos, mood))
    if moods:
        picked: list[JsonValue] = [m for _, m in sorted(moods)[:2]]
        for pos, m in sorted(moods)[:2]:
            reader.mark(pos, MOOD_LABELS[m])
        out.fields["mood"] = picked
        out.understood.append("humeur : " + " et ".join(MOOD_LABELS[str(m)] for m in picked))
    density, _ = _first_key(reader, DENSITY_WORDS)
    if density:
        out.fields["decor_density"] = density
        out.understood.append(f"décor : {density}")
    return style


def _light(reader: _Reader, out: Translation) -> None:
    light: dict[str, JsonValue] = {}
    warmth, warmth_word = _first_key(reader, WARMTH_WORDS)
    if warmth:
        light["warmth"] = warmth
    time, time_word = _first_key(reader, TIME_WORDS)
    if time:
        light["time"] = time
    if light:
        for word in (warmth_word, time_word):
            if word:
                label = accented(word)
                reader.mark(reader.position(word), label)
        out.fields["light"] = light
        out.understood.append("lumière : " + ", ".join(str(v) for v in light.values()))


def _zones(reader: _Reader, out: Translation, room_type: str | None) -> None:
    zones: list[JsonValue] = []
    for pos, _, zone in reader.all({w: z for z, ws in ZONE_WORDS.items() for w in ws}):
        if zone not in zones:
            zones.append(zone)
            reader.mark(pos, f"coin {ZONE_LABELS[str(zone)]}")
    if zones:
        main = MAIN_ZONE.get(room_type or "")
        if main and main not in zones:
            zones.insert(0, main)
        out.fields["zones"] = zones
        out.understood.append("zones : " + ", ".join(ZONE_LABELS[str(z)] for z in zones))


def _colors(reader: _Reader, out: Translation, style: str | None) -> None:
    colors: list[str] = []
    for pos, _, color in reader.all(COLOR_WORDS):
        if color not in colors:
            colors.append(color)
            reader.mark(pos, accented(color))
    if not colors:
        return
    palette = colors[:5]
    filler = STYLE_PALETTES[style or "naturel"]
    added = [c for c in filler if c not in palette][: max(0, 3 - len(palette))]
    out.fields["palette"] = [*palette, *added]
    out.understood.append("couleurs : " + ", ".join(palette))
    if added:
        out.notes.append(
            "palette complétée par le style (trois couleurs au moins) : " + ", ".join(added)
        )


def _format_and_unknowns(reader: _Reader, out: Translation) -> None:
    if reader.find(PORTRAIT_WORDS)[0] >= 0:
        out.fields["views"] = {"aspect": "4:5"}
        out.understood.append("format : 4:5 portrait")
        reader.mark(len(reader.text), "portrait 4:5")
    elif reader.find(LANDSCAPE_WORDS)[0] >= 0:
        out.fields["views"] = {"aspect": "4:3"}
        out.understood.append("format : 4:3 paysage")
    for word, label in UNKNOWN_THINGS.items():
        if _spans(reader.text, word):
            out.notes.append(f"« {label} » : pas au catalogue, rien de posé")


def translate_phrases(text: str, categories: list[str]) -> Translation:
    """Consigne → champs de l'intention, par le vocabulaire fermé (hors ligne). Les matières
    d'abord : « murs blancs » est une matière, pas une couleur de la palette."""
    out = Translation()
    reader = _Reader(text[:MAX_INSTRUCTION])
    room_type = _room(reader, out)
    _materials(reader, out)
    style = _style_and_mood(reader, out)
    _light(reader, out)
    _furniture(reader, out, categories)
    _zones(reader, out, room_type)
    _colors(reader, out, style)
    _format_and_unknowns(reader, out)
    out.summary = [label for _, label in sorted(reader.marks)]
    return out


# ------------------------------------------------------------------ modèle
SYSTEM_PROMPT = (
    "Tu traduis la consigne d'un utilisateur qui décrit l'ambiance voulue pour une pièce en "
    "champs d'une fiche, en JSON strict, sans texte autour. Tu n'utilises que les valeurs "
    "permises ; tu n'ajoutes rien que l'utilisateur n'a pas dit : un champ non mentionné est "
    "absent. Jamais de position ni de coordonnée."
)
USER_TEMPLATE = """Champs et valeurs permises :
- room_type : {room_types}
- usage : texte court en français (« chambre d'amis »), seulement s'il est dit
- style : {styles}
- mood : liste de 2 au plus parmi {moods}
- palette : 3 à 5 couleurs parmi {colors}
- materials : objet avec floor, walls, gables, slopes, timber, chacun parmi {finishes}
- decor_density : sobre | normal | riche
- light : objet avec time (matin | midi | heure_doree | soir)
  et warmth (froide | neutre | chaude)
- furniture : liste d'objets {{"category": <catégorie>, "count": <entier>}}
  parmi les catégories : {categories}
- avoid : liste de catégories à ne pas proposer
- zones : liste parmi {zones}
- views : objet avec aspect (4:3 | 4:5)

Consigne de l'utilisateur :
« {text} »

Réponds avec un seul objet JSON :
{{"fields": {{...}}, "notes": ["<ce qui n'a pas d'équivalent>"]}}"""
FINISH_NAMES = (
    "plaster",
    "limewash",
    "wood_floor",
    "wood_dark",
    "wood_boards",
    "oak_dark",
    "painted_wood",
    "timber",
    "stone",
    "stone_rubble",
    "concrete_floor",
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class InstructionParseError(ValueError):
    """Le modèle n'a pas renvoyé l'objet JSON attendu."""


def instruction_prompt(text: str, categories: list[str]) -> str:
    return USER_TEMPLATE.format(
        room_types=" | ".join(ROOM_TYPES),
        styles=" | ".join(STYLES),
        moods=", ".join(MOOD_LABELS),
        colors=", ".join(COLORS),
        finishes=" | ".join(FINISH_NAMES),
        categories=", ".join(categories),
        zones=", ".join(ZONE_LABELS),
        text=text.strip()[:MAX_INSTRUCTION],
    )


def parse_translation(raw: str, categories: list[str]) -> Translation:
    """Le JSON du modèle, validé champ par champ : un champ invalide est écarté et dit."""
    cleaned = _FENCE.sub("", raw.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        msg = "réponse sans objet JSON"
        raise InstructionParseError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = f"JSON illisible : {str(exc)[:160]}"
        raise InstructionParseError(msg) from exc
    fields = data.get("fields", data) if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        msg = "réponse JSON qui n'est pas un objet"
        raise InstructionParseError(msg)
    out = Translation()
    for name, given in fields.items():
        if name not in INTENT_FIELDS or given in (None, "", [], {}):
            if name not in INTENT_FIELDS:
                out.dropped.append(f"« {name} » : champ inconnu, écarté")
            continue
        value = (
            _known_categories(name, given, categories, out)
            if name in ("furniture", "avoid")
            else given
        )
        if not value:
            continue
        try:
            DesignIntent.model_validate({name: value})
        except ValidationError as exc:
            out.dropped.append(f"« {name} » : {str(exc.errors()[0]['msg'])[:80]}, écarté")
            continue
        out.fields[name] = value
    notes = data.get("notes") if isinstance(data, dict) else None
    if isinstance(notes, list):
        out.notes.extend(str(n)[:120] for n in notes[:4] if isinstance(n, str) and n.strip())
    return out


def _known_categories(name: str, value: Any, categories: list[str], out: Translation) -> Any:
    if not isinstance(value, list):
        return None
    kept: list[Any] = []
    for item in value:
        category = item.get("category") if name == "furniture" and isinstance(item, dict) else item
        if isinstance(category, str) and category in categories:
            kept.append(item)
        else:
            out.dropped.append(f"« {name} » : « {category} » hors catalogue, écarté")
    return kept


def translate_with_model(
    text: str,
    categories: list[str],
    *,
    model: LanguageModel,
    gateway: ModelGateway,
    project: Project | None,
) -> Translation:
    """Consigne → champs par le modèle (texte seul), un réessai si la réponse n'est pas du
    JSON ; les champs que le modèle a oubliés et que les phrases lisent sont ajoutés. Sans
    projet (aperçu de l'accueil), l'accord est celui de la machine (`bind_text_only`)."""
    send = gateway.bind(model, project) if project is not None else gateway.bind_text_only(model)
    messages = [
        LlmMessage(role="system", content=SYSTEM_PROMPT),
        LlmMessage(role="user", content=instruction_prompt(text, categories)),
    ]
    request = LlmRequest(
        purpose="intention", messages=messages, json_mode=True, temperature=0.1, max_tokens=600
    )
    response = send(request)
    try:
        out = parse_translation(response.text, categories)
    except InstructionParseError:
        retry = [
            *messages,
            LlmMessage(role="assistant", content=response.text[:400]),
            LlmMessage(
                role="user",
                content="Ta réponse n'était pas l'objet JSON demandé. Renvoie uniquement "
                "l'objet JSON, sans texte autour.",
            ),
        ]
        response = send(request.model_copy(update={"messages": retry}))
        out = parse_translation(response.text, categories)
    out.by = f"llm:{model.model_id}"
    local = translate_phrases(text, categories)
    for name, value in local.fields.items():
        if name not in out.fields:
            out.fields[name] = value
            out.notes.append(f"« {name} » lu dans la consigne (oublié par le modèle)")
    out.understood = [*describe(out.fields)]
    out.summary = summary_of(out.fields)
    out.notes.extend(n for n in local.notes if n not in out.notes)
    return out


def describe(fields: dict[str, JsonValue]) -> list[str]:
    """Une phrase par champ traduit, pour la fiche « Ambiance »."""
    out: list[str] = []
    for name in INTENT_FIELDS:
        if name in fields:
            out.append(f"{name} : {json.dumps(fields[name], ensure_ascii=False)}")
    return out


def summary_of(fields: dict[str, Any]) -> list[str]:
    """Les mêmes mots simples que les phrases (« grenier · chaleureux · deux lits »), pour une
    traduction du modèle, qui n'a pas de positions dans le texte."""
    out: list[str] = []
    room_type = fields.get("room_type")
    if isinstance(room_type, str):
        out.append(str(fields.get("usage") or ROOM_LABELS.get(room_type, room_type)))
    style = fields.get("style")
    if isinstance(style, str) and style in STYLES:
        out.append(STYLES[style].lower())
    out += [MOOD_LABELS.get(m, m) for m in fields.get("mood") or [] if isinstance(m, str)]
    light = fields.get("light")
    if isinstance(light, dict):
        out += [accented(str(v)) for k, v in light.items() if k in ("warmth", "time") and v]
    for item in fields.get("furniture") or []:
        if isinstance(item, dict) and isinstance(item.get("category"), str):
            out.append(furniture_words(item["category"], int(item.get("count") or 1)))
    out += [
        f"sans {furniture_words(c, 1)}" for c in fields.get("avoid") or [] if isinstance(c, str)
    ]
    zones = [z for z in fields.get("zones") or [] if isinstance(z, str)]
    out += [f"coin {ZONE_LABELS.get(z, z)}" for z in zones]
    views = fields.get("views")
    if isinstance(views, dict) and views.get("aspect") == "4:5":
        out.append("portrait 4:5")
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------ application
def intent_fields(current: DesignIntent | None, fields: dict[str, JsonValue]) -> dict[str, Any]:
    """Champs prêts pour `SetIntent` : les objets partiels (lumière, format, matières) sont
    complétés par l'intention actuelle, pour ne pas effacer ce qui n'a pas été dit."""
    base = (current or DesignIntent()).model_dump(mode="json")
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if name in ("light", "views", "materials") and isinstance(value, dict):
            out[name] = {**base[name], **value}
        else:
            out[name] = value
    return out


# ------------------------------------------------------------------ lecture complète
Understanding = Literal["model", "phrases", "none"]


class Reading(StrictModel):
    """Une consigne lue : la traduction, qui l'a comprise, et pourquoi le modèle n'a pas
    servi s'il était configuré (accord manquant, indisponible)."""

    translation: Translation
    understanding: Understanding
    notice: str = ""


def read_instruction(
    text: str,
    categories: list[str],
    *,
    model: LanguageModel | None,
    gateway: ModelGateway | None,
    project: Project | None,
) -> Reading:
    """Le modèle configuré d'abord (texte seul, accord du projet pour un modèle distant),
    sinon les phrases simples, hors ligne. Partagé par la fiche Ambiance et le chemin
    automatique (consigne écrite sur l'accueil)."""
    from openplan.adapters.gateway import ConsentRequiredError, ModelUnavailableError
    from openplan.adapters.remote.openai_compatible import ModelCallError

    translation: Translation | None = None
    understanding: Understanding = "phrases"
    notice = ""
    if model is not None and gateway is not None:
        try:
            translation = translate_with_model(
                text, categories, model=model, gateway=gateway, project=project
            )
            understanding = "model"
        except ConsentRequiredError:
            notice = (
                "le modèle distant attend votre accord (Paramètres avancés › Assistant IA) ; "
                "consigne lue par les phrases simples"
            )
        except (ModelUnavailableError, ModelCallError, InstructionParseError, OSError) as exc:
            notice = f"modèle indisponible ({str(exc)[:80]}) ; consigne lue par les phrases simples"
    if translation is None:
        translation = translate_phrases(text, categories)
    if not translation.fields:
        understanding = "none"
    return Reading(translation=translation, understanding=understanding, notice=notice)
