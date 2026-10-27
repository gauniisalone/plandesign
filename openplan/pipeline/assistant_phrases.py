"""Phrases-types comprises sans modèle : « ajoute un bureau près de la fenêtre », « éloigne le
lit de la porte », « enlève l'armoire », « propose-moi trois aménagements ».

C'est le repli quand aucun modèle n'est configuré (et la base des tests de la conversation) :
un petit vocabulaire français, pas de compréhension. Ce qui n'est pas reconnu rend `None` ;
l'appelant répond alors en texte, jamais en devinant.
"""

from __future__ import annotations

import re
import unicodedata

from openplan.schemas.assistant import AssistantIntent, AssistantReply, OpeningWord
from openplan.schemas.elements import StairKind
from openplan.schemas.layout import ItemDimensions, Place

SYNONYMS: dict[str, tuple[str, ...]] = {
    # Chambre
    "bed_builtin": (
        "lit encastre",
        "lits encastres",
        "lit sous la pente",
        "lit sous pente",
        "lit sous le rampant",
        "lit coffre",
    ),
    "bed_double": ("lit double", "grand lit", "lit 160", "lit 140", "lit"),
    "bed_single": ("lit simple", "lit 90", "lit une place", "lit d'enfant", "lit enfant"),
    "bed_bunk": ("lits superposes", "lit superpose", "lit mezzanine"),
    "nightstand": ("table de chevet", "tables de chevet", "chevet", "chevets", "table de nuit"),
    "wardrobe": ("armoire", "armoires", "penderie", "dressing", "placard"),
    "chest": ("commode", "commodes", "meuble a tiroirs"),
    "low_storage": (
        "rangement bas",
        "rangements bas",
        "meuble bas",
        "coffre sous pente",
        "rangement sous pente",
    ),
    # Bureau
    "desk": ("bureau", "bureaux", "table de travail", "plan de travail"),
    "desk_corner": ("bureau d'angle", "bureau en angle", "bureau en l"),
    "chair": ("chaise de bureau", "fauteuil de bureau", "siege de bureau", "siege"),
    # Séjour
    "bookshelf": ("bibliotheque", "etagere", "etageres", "rayonnage"),
    "sofa": ("canape", "sofa", "banquette", "canape-lit", "clic-clac"),
    "sofa_lounge": ("canape avec meridienne", "meridienne", "chaise longue"),
    "sofa_corner": ("canape d'angle", "canape en angle", "canape en l"),
    "armchair": ("fauteuil", "fauteuils"),
    "coffee_table": ("table basse", "tables basses", "table de salon"),
    "tv_unit": ("meuble tv", "meuble tele", "meuble television", "banc tv"),
    "table": (
        "table a manger",
        "table de salle a manger",
        "table et chaises",
        "table",
    ),
    "round_table": ("table ronde", "tables rondes", "gueridon"),
    "dining_chair": ("chaise", "chaises", "chaise de salle a manger"),
    "floor_lamp": ("lampadaire", "lampadaires", "lampe sur pied", "lampe"),
    # Cuisine
    "kitchen_base": (
        "meuble de cuisine",
        "meuble bas de cuisine",
        "meubles de cuisine",
        "element de cuisine",
        "caisson",
    ),
    "kitchen_drawers": ("tiroirs de cuisine", "meuble a tiroirs de cuisine", "casserolier"),
    "kitchen_sink": ("evier", "eviers"),
    "stove": (
        "cuisiniere",
        "gaziniere",
        "plaque de cuisson",
        "plaques de cuisson",
        "plaques",
        "feux",
    ),
    "fridge": ("refrigerateur", "frigo", "frigidaire", "frigo americain"),
    "fridge_small": (
        "petit frigo",
        "frigo top",
        "refrigerateur sous plan",
        "petit refrigerateur",
    ),
    # Décor
    "rug": ("tapis", "carpette", "descente de lit"),
    "rug_round": ("tapis rond",),
    # Salle de bain
    "toilet": ("wc", "toilettes", "toilette", "cuvette"),
    "shower": ("douche", "douches", "bac a douche", "cabine de douche"),
    "bathtub": ("baignoire", "baignoires"),
    "washbasin": ("lavabo", "lavabos", "vasque"),
    "bathroom_cabinet": (
        "meuble vasque",
        "meuble sous vasque",
        "meuble de salle de bain",
        "meuble lavabo",
    ),
    "washer": ("lave-linge", "lave linge", "machine a laver"),
    "dryer": ("seche-linge", "seche linge"),
}
"""Mots par catégorie du catalogue, du plus précis au plus vague (le premier qui colle gagne)."""

PLACES: dict[Place, tuple[str, ...]] = {
    "window": ("fenetre", "fenetres", "lumiere", "jour"),
    "door": ("porte", "entree"),
    "door_swing": ("debattement", "ouverture de la porte", "passage de la porte"),
    "radiator": ("radiateur", "chauffage"),
    "low_wall": (
        "sous la pente",
        "sous pente",
        "zone basse",
        "mur bas",
        "partie basse",
        "sous le toit",
    ),
    "ridge": ("faitage", "au centre", "au milieu", "sous le faitage"),
    "outlet": ("prise", "prises"),
    "corner": ("dans le coin", "dans un coin", "dans l'angle", "coin"),
    "wall": ("mur", "murs", "cloison", "contre le mur", "au mur"),
}

_ADD_WORDS = (
    "ajoute",
    "ajouter",
    "mets",
    "met",
    "mettre",
    "pose",
    "poser",
    "place",
    "placer",
    "installe",
    "installer",
    "rajoute",
    "il me faut",
    "je veux",
    "je voudrais",
    "j'aimerais",
)
_ADD = re.compile(r"^(" + "|".join(_ADD_WORDS) + r")\b")
_MOVE = re.compile(
    r"^(deplace|deplacer|bouge|bouger|decale|decaler|pousse|rapproche|rapprocher|eloigne|eloigner|colle|coller|remets|remettre)\b"
)
_REMOVE = re.compile(r"^(enleve|enlever|retire|retirer|supprime|supprimer|vire|virer|ote|oter)\b")
_PROPOSE = re.compile(
    r"\b(propose|proposer|proposition|amenagement|amenagements|disposition|dispositions|variante|variantes)\b"
)
_OPENING = re.compile(r"\b(portes?|fenetres?|baies?)\b")
_ADD_OPENING = re.compile(r"^(" + "|".join(_ADD_WORDS) + r"|perce|percer)\b")
_QUESTION = re.compile(r"\?\s*$|^(quelle|quel|combien|est-ce|y a-t-il|peux-tu me dire)\b")
_COUNT = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5}
_NUMBER = re.compile(r"\b(\d+)\b")
# Sonar S8786 : texte borné à MAX_TEXT_CHARS dans fold() avant toute lecture ; test de durée dans
# test_kan108_regex.
_DIMENSION = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:x|×|par|sur)\s*(\d+(?:[.,]\d+)?)")  # NOSONAR


_NAME_ROOM = r"^(appelle|nomme|renomme|baptise)\s+(la piece|cette piece|la)\s+(.+)$"  # NOSONAR
MAX_TEXT_CHARS = 4000
"""KAN-108 : au-delà, la phrase n'est pas lue (les routes la bornent déjà à 500 ou 2 000
caractères). Plusieurs lectures de la phrase repliée reviennent en arrière sur une longue suite
de chiffres ou de mots (Sonar S8786) : bornées ainsi, elles restent rapides."""


def fold(text: str) -> str:
    text = "".join(
        c
        for c in unicodedata.normalize("NFD", text[:MAX_TEXT_CHARS].lower())
        if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", text.replace("’", "'").replace("-moi", " moi")).strip()


def _category(text: str, categories: list[str]) -> tuple[str | None, str]:
    """(catégorie, mot reconnu) : le synonyme le plus long qui apparaît dans la phrase."""
    best: tuple[int, str, str] | None = None
    for category, words in SYNONYMS.items():
        if category not in categories:
            continue
        for word in words:
            if re.search(rf"\b{re.escape(word)}s?\b", text) and (
                best is None or len(word) > best[0]
            ):
                best = (len(word), category, word)
    return (best[1], best[2]) if best else (None, "")


def _all_categories(text: str, categories: list[str]) -> list[tuple[str, str]]:
    """Toutes les catégories nommées, dans l'ordre de la phrase, sans chevauchement (le mot le
    plus long d'abord : « table de chevet » n'est pas aussi une « table »)."""
    found: list[tuple[int, int, str, str]] = []
    words = sorted(
        ((w, c) for c, ws in SYNONYMS.items() if c in categories for w in ws),
        key=lambda wc: -len(wc[0]),
    )
    for word, category in words:
        for m in re.finditer(rf"\b{re.escape(word)}s?\b", text):
            if any(m.start() < end and start < m.end() for start, end, _, _ in found):
                continue
            found.append((m.start(), m.end(), category, word))
    seen: list[tuple[str, str]] = []
    for _, _, category, word in sorted(found):
        if category not in [c for c, _ in seen]:
            seen.append((category, word))
    return seen


def _places(text: str) -> tuple[list[Place], list[Place]]:
    """« près de / contre / sous » → near ; « loin de / pas devant / à l'écart de » → away."""
    near: list[Place] = []
    away: list[Place] = []
    for place, words in PLACES.items():
        for word in words:
            m = re.search(rf"\b{re.escape(word)}\b", text)
            if not m:
                continue
            before = text[max(0, m.start() - 28) : m.start()]
            if re.search(
                r"(loin|pas devant|a l'ecart|eloign|degage|pas contre|pas pres|pas sous|jamais)",
                before,
            ):
                if place not in away:
                    away.append(place)
            elif place not in near:
                near.append(place)
            break
    if "door_swing" in away and "door" in away:
        away.remove("door")
    return near, away


def _count(text: str, word: str) -> int:
    head = text.split(word, 1)[0]
    for token, value in _COUNT.items():
        if re.search(rf"\b{token}\b", head[-12:]):
            return value
    m = _NUMBER.search(head[-6:])
    return max(1, min(10, int(m.group(1)))) if m else 1


def _dimensions(text: str, category: str | None) -> ItemDimensions | None:
    m = _DIMENSION.search(text)
    if not m:
        return None
    a, b = (float(v.replace(",", ".")) for v in m.groups())
    if a > 10:
        a, b = a / 100.0, b / 100.0
    if not (0.2 < a < 6 and 0.2 < b < 6):
        return None
    if category in ("bed_double", "bed_single"):
        return ItemDimensions(width=a, depth=b)
    return ItemDimensions(width=a, depth=b)


def _metres(text: str) -> list[float]:
    """Nombres écrits, en mètres (« 4,20 », « 420 » → 4,2 ; « 2m60 » → 2,6)."""
    out: list[float] = []
    for m in re.finditer(r"(\d+)\s*m\s*(\d{1,2})\b", text):
        out.append(float(f"{m.group(1)}.{m.group(2)}"))
    if out:
        return out
    for raw in re.findall(r"\d+(?:[.,]\d+)?", text):
        v = float(raw.replace(",", "."))
        out.append(v / 100.0 if v > 30 else v)
    return out


_PAIR = re.compile(r"\b(double|doubles|deux vantaux|2 vantaux)\b")
_OPENING_KINDS: tuple[tuple[re.Pattern[str], OpeningWord], ...] = (
    (re.compile(r"\bportes?[- ]fenetres?\b"), "french_window"),
    (re.compile(r"\bbaies?\b"), "sliding_window"),
    (re.compile(r"galandage"), "pocket_door"),
    (re.compile(r"(?=.*\bfenetres?\b)(?=.*\bcoulissante?s?\b)"), "sliding_window"),
    (re.compile(r"\bcoulissante?s?\b"), "sliding_door"),
    (re.compile(r"\b(pliante?s?|accordeon)\b"), "folding_door"),
    (re.compile(r"fenetre"), "window"),
    (_PAIR, "double_door"),
)
"""Sortes d'ouvertures (E5), de la plus précise à la plus vague : la première qui colle."""


def _opening_kind(folded: str) -> tuple[OpeningWord, int | None]:
    """La sorte d'ouverture nommée ; pour une porte-fenêtre, 2 vantaux si « double »."""
    for pattern, kind in _OPENING_KINDS:
        if pattern.search(folded):
            pair = kind == "french_window" and _PAIR.search(folded) is not None
            return kind, 2 if pair else None
    return "door", None


def _is_opening_request(folded: str, categories: list[str]) -> bool:
    """« ajoute une porte sur le mur du fond » oui ; « ajoute un bureau près de la fenêtre »
    non : la porte ou la fenêtre doit être l'objet du verbe, pas un repère."""
    o = _OPENING.search(folded)
    if o is None or not _ADD_OPENING.match(folded):
        return False
    _, word = _category(folded, categories)
    if word and folded.find(word) < o.start():
        return False
    marker = re.search(r"\b(pres|loin|a cote|contre|face|devant|sous)\b", folded)
    return not (marker and marker.start() < o.start())


_STAIRS = re.compile(r"\b(escaliers?|tremie|colimacon)\b")
_ADD_STAIRS = re.compile(
    r"^(ajoute|ajouter|mets|mettre|met|pose|poser|place|installe|cree|fais|dessine|il faut)\b"
)


def _stairs_request(folded: str) -> AssistantReply | None:
    """« ajoute un escalier droit contre le mur de gauche », « mets une trémie avec garde-corps
    au centre » (S1). Le type se lit dans la phrase, droit par défaut ; la cible est le mur
    nommé, ou le centre de la pièce."""
    if not _STAIRS.search(folded) or not _ADD_STAIRS.match(folded):
        return None
    kind: StairKind = "straight"
    if "quart" in folded:
        kind = "quarter_turn"
    elif "demi" in folded:
        kind = "half_turn"
    elif "helico" in folded or "colimacon" in folded or "spirale" in folded:
        kind = "spiral"
    target = None
    m = re.search(
        r"\b(contre|le long d[eu]|sur|au fond d[eu])\s+(le |la |l )?(mur|cloison|pignon)(.*)$",
        folded,
    )
    if m:
        target = (m.group(3) + m.group(4)).strip(" .!")
    elif re.search(r"\b(centre|milieu)\b", folded):
        target = "centre"
    return AssistantReply(intents=[AssistantIntent(kind="add_stairs", stairs=kind, target=target)])


_EDIT_REMOVE = re.compile(
    r"^(supprime|supprimer|enleve|enlever|retire|retirer|vire|virer|ote|oter|bouche|boucher"
    r"|condamne|condamner|mure|murer)\b"
)
_EDIT_MOVE = re.compile(
    r"^(deplace|deplacer|decale|decaler|bouge|bouger|pousse|pousser|glisse|glisser)\b"
)
_EDIT_GROW = re.compile(r"^(elargis|elargir|agrandis|agrandir|rehausse|rehausser)\b")
_EDIT_SHRINK = re.compile(r"^(retrecis|retrecir|reduis|reduire|abaisse|abaisser)\b")
_EDIT_SET = re.compile(
    r"^(redimensionne|redimensionner|change|changer|modifie|modifier|regle|regler|passe"
    r"|passer|mets|mettre|met|fixe|fixer)\b"
)
_OPENING_OBJECT = re.compile(
    r"^(?:(?:la |l'|sa )?(?P<dim>largeur|hauteur) (?:de la |de l'|du |de sa |de son )?)?"
    r"(?P<det>la |le |l'|les |cette |ce |cet |ma |mon |sa |son |une |un )?"
    r"(?P<noun>portes?[- ]fenetres?|portes?|fenetres?|baies?|ouvertures?|passages?)\b"
)
"""L'objet du verbe est une ouverture (« supprime la porte… », « change la largeur de la
fenêtre… ») ; « éloigne le lit de la porte » n'en est pas une (l'objet est le lit)."""
_LENGTH = re.compile(
    r"(?P<a>\d+)\s*m\s*(?P<b>\d{1,2})\b"
    r"|(?P<v>\d+(?:[.,]\d+)?)\s*(?P<u>cm|centimetres?|mm|millimetres?|m|metres?)?(?![\w])"
)
_TARGET_END = re.compile(
    r"\s+(?:de |d'|a |sur |en |pour )?(?=\d)"
    r"|\s+vers\b|\s+(?:a|sur la|tout a)\s+(?:gauche|droite)\b"
    r"|\s+(?:en|de)\s+(?:largeur|hauteur|large|haut)\b|\s+(?:fait|mesure)\b|\s+pour\b"
    r"|\s*,"
)


def _length_m(text: str) -> float | None:
    """La première longueur écrite, en mètres : « 50 cm », « 1,20 m », « 1m20 », « 120 »
    (sans unité, au-delà de 10 : des centimètres)."""
    m = _LENGTH.search(text)
    if m is None:
        return None
    if m.group("a") is not None:
        return float(f"{m.group('a')}.{m.group('b')}")
    value = float(m.group("v").replace(",", "."))
    unit = m.group("u") or ""
    if unit.startswith("c"):
        return value / 100.0
    if unit.startswith("mm") or unit.startswith("mil"):
        return value / 1000.0
    if unit.startswith("m"):
        return value
    return value / 100.0 if value > 10 else value


def _opening_edit(folded: str) -> AssistantReply | None:  # noqa: PLR0911, PLR0912
    """KAN-32 : déplacer, supprimer, élargir ou rehausser une porte ou une fenêtre déjà posée.
    « déplace la fenêtre de la chambre de 50 cm vers la gauche », « supprime la porte du
    placard », « élargis la fenêtre du salon à 1,20 m », « la porte fait 90 cm de large »."""
    verb = None
    for kind, pattern in (
        ("remove", _EDIT_REMOVE),
        ("move", _EDIT_MOVE),
        ("grow", _EDIT_GROW),
        ("shrink", _EDIT_SHRINK),
        ("set", _EDIT_SET),
    ):
        m = pattern.match(folded)
        if m:
            verb, rest = kind, folded[m.end() :].strip()
            break
    if verb is None:
        # « la fenêtre du salon fait 1,20 m de large »
        m = re.match(
            r"^(?P<target>(la |le |l')(portes?|fenetres?|baies?|passages?)\b.*?)\s+"
            r"(fait|mesure)\s+(?P<value>.+)$",
            folded,
        )
        if m is None:
            return None
        value = _length_m(m.group("value"))
        if value is None:
            return None
        tall = re.search(r"\b(haut|hauteur)\b", m.group("value")) is not None
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="resize_opening",
                    target=m.group("target").strip(),
                    dimension="height" if tall else "width",
                    value_m=value,
                )
            ]
        )
    obj = _OPENING_OBJECT.match(rest)
    if obj is None:
        return None
    if verb == "set" and (obj.group("det") or "").strip() in ("une", "un"):
        return None  # « mets une fenêtre… » : c'est un ajout.
    body = rest[obj.start("det") if obj.group("det") else obj.start("noun") :]
    if verb == "remove":
        # Rien à mesurer : toute la suite désigne l'ouverture (« … de la chambre 2 »).
        target = body.strip(" .!")
        return AssistantReply(intents=[AssistantIntent(kind="remove_opening", target=target)])
    target = _TARGET_END.split(body, maxsplit=1)[0].strip(" .!")
    after = body[len(target) :]
    amount = _length_m(after)
    if amount is not None and not 0.0 < amount < 10.0:
        amount = None
    if verb == "move":
        left = re.search(r"\bgauche\b", after) is not None
        right = re.search(r"\bdroite\b", after) is not None
        delta = None
        if amount is not None and left != right:
            delta = -amount if left else amount
        return AssistantReply(
            intents=[AssistantIntent(kind="move_opening", target=target, delta_m=delta)]
        )
    tall = (
        obj.group("dim") == "hauteur"
        or re.search(r"\b(haut|hauteur|haute|rehausse|rehausser|abaisse|abaisser)\b", folded)
        is not None
    )
    if verb == "set" and amount is None:
        return None
    absolute = verb == "set" or re.search(r"(^|\s)(a|en|sur)\s+\d", after) is not None
    if amount is None:
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="resize_opening", target=target, dimension="height" if tall else "width"
                )
            ]
        )
    return AssistantReply(
        intents=[
            AssistantIntent(
                kind="resize_opening",
                target=target,
                dimension="height" if tall else "width",
                value_m=amount if absolute else None,
                delta_m=None if absolute else (amount if verb == "grow" else -amount),
            )
        ]
    )


def _plan_phrases(folded: str, categories: list[str]) -> AssistantReply | None:  # noqa: PLR0911, PLR0912
    """Murs, dimensions, ouvertures, nom de la pièce (B4)."""
    edit = _opening_edit(folded)
    if edit is not None:
        return edit
    if re.search(
        r"\bhauteur (sous plafond|de plafond|du plafond)\b|\bplafond\b.*\b(fait|a|est a)\b", folded
    ):
        values = _metres(folded)
        if values:
            return AssistantReply(
                intents=[
                    AssistantIntent(kind="set_dimension", dimension="height", value_m=values[0])
                ]
            )
    stairs = _stairs_request(folded)
    if stairs is not None:
        return stairs
    if _is_opening_request(folded, categories):
        kind, leaves = _opening_kind(folded)
        target = None
        # Sonar S8786 : texte borné à MAX_TEXT_CHARS dans fold() avant toute lecture ; test de durée
        # dans test_kan108_regex.
        m = re.search(r"\b(sur|dans|au|a)\s+(le mur|la cloison|le)?\s*(.+)$", folded)  # NOSONAR
        if m:
            target = m.group(3).strip(" .!")
        widths = [v for v in _metres(folded) if 0.4 <= v <= 4.0]
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="add_opening",
                    opening=kind,
                    leaves=leaves,
                    target=target or None,
                    value_m=widths[0] if widths else None,
                )
            ]
        )
    # Sonar S8786 : texte borné à MAX_TEXT_CHARS dans fold() avant toute lecture ; test de durée
    # dans test_kan108_regex.
    m = re.search(r"\b(le mur|la cloison)\s+(.*?)\s+(fait|mesure)\s+(.+)$", folded)  # NOSONAR
    if m:
        values = _metres(m.group(4))
        if values:
            return AssistantReply(
                intents=[
                    AssistantIntent(
                        kind="set_dimension",
                        dimension="wall",
                        target=m.group(2).strip() or None,
                        value_m=values[0],
                    )
                ]
            )
    if re.search(r"\b(piece|salle|chambre|salon|bureau|cuisine)\b.*\b(fait|mesure)\b", folded):
        values = _metres(
            folded.split("fait", 1)[-1] if "fait" in folded else folded.split("mesure", 1)[-1]
        )
        intents: list[AssistantIntent] = []
        if "large" in folded or "largeur" in folded:
            if values:
                intents.append(
                    AssistantIntent(kind="set_dimension", dimension="width", value_m=values[0])
                )
        elif "long" in folded or "longueur" in folded:
            if values:
                intents.append(
                    AssistantIntent(kind="set_dimension", dimension="length", value_m=values[0])
                )
        elif len(values) >= 2:
            a, b = sorted(values[:2], reverse=True)
            intents.append(AssistantIntent(kind="set_dimension", dimension="length", value_m=a))
            intents.append(AssistantIntent(kind="set_dimension", dimension="width", value_m=b))
        if intents:
            return AssistantReply(intents=intents)
    # Sonar S8786 : texte borné à MAX_TEXT_CHARS dans fold() avant toute lecture ; test de durée
    # dans test_kan108_regex.
    m = re.match(_NAME_ROOM, folded)  # NOSONAR
    if m:
        return AssistantReply(
            intents=[
                AssistantIntent(kind="name_room", name=m.group(3).strip(' .!«»"').capitalize())
            ]
        )
    # Sonar S8786 : texte borné à MAX_TEXT_CHARS dans fold() avant toute lecture ; test de durée
    # dans test_kan108_regex.
    m = re.match(r"^(la piece|cette piece)\s+s['\s]?appelle\s+(.+)$", folded)  # NOSONAR
    if m:
        return AssistantReply(
            intents=[
                AssistantIntent(kind="name_room", name=m.group(2).strip(' .!«»"').capitalize())
            ]
        )
    m = re.match(
        r"^c['\s]?est\s+(une |un |la |le )?"
        r"(chambre|salon|cuisine|bureau|salle de bain|couloir|grenier|combles)(.*)$",
        folded,
    )
    if m:
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="name_room", name=(m.group(2) + m.group(3)).strip().capitalize()
                )
            ]
        )
    return None


def explicit_add(text: str) -> bool:
    """La phrase commence par un verbe d'ajout (« ajoute », « mets », « il me faut »…) : un
    meuble nommé sans verbe (« deux lits », « sans tapis ») est une envie, pas un geste."""
    return bool(_ADD.match(fold(text)))


def understand(text: str, categories: list[str]) -> AssistantReply | None:  # noqa: PLR0911
    """La phrase en intentions, ou None si elle ne suit aucune phrase-type."""
    folded = fold(text)
    if not folded:
        return None
    plan = _plan_phrases(folded, categories)
    if plan is not None:
        return plan
    if _QUESTION.search(folded) and not _ADD.match(folded):
        return AssistantReply(intents=[AssistantIntent(kind="ask", text="")], message="")
    if _PROPOSE.search(folded) and not _MOVE.match(folded) and not _REMOVE.match(folded):
        named = _all_categories(folded, categories)
        near, away = _places(folded)
        if len(named) > 1:
            # Plusieurs meubles : un repère ne se rattache à aucun en particulier, on le laisse.
            near, away = [], []
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="propose_layouts",
                    category=category,
                    count=_count(folded, word),
                    near=near,
                    away_from=away,
                )
                for category, word in named
            ]
            or [AssistantIntent(kind="propose_layouts", near=near, away_from=away)],
            message="",
        )
    category, word = _category(folded, categories)
    near, away = _places(folded)
    if _REMOVE.match(folded):
        rest = _REMOVE.sub("", folded, count=1).strip(" ,.!")
        return AssistantReply(
            intents=[AssistantIntent(kind="remove_furniture", target=rest or word or None)],
            message="",
        )
    if _MOVE.match(folded):
        verb = _MOVE.match(folded)
        assert verb is not None
        if verb.group(1).startswith("eloign") and near and not away:
            # « éloigne le lit de la porte » : le repère est celui dont on s'éloigne.
            away, near = near, []
        target = re.split(
            r"\b(pres|loin|contre|vers|a cote|sous|au|aux|de la|du|des|d')\b",
            folded[verb.end() :],
            maxsplit=1,
        )[0]
        target = target.strip(" ,.!") or word
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="move_furniture", target=target or None, near=near, away_from=away
                )
            ],
            message="",
        )
    if _ADD.match(folded) or category:
        if category is None:
            return None
        return AssistantReply(
            intents=[
                AssistantIntent(
                    kind="add_furniture",
                    category=category,
                    count=_count(folded, word),
                    dimensions_m=_dimensions(folded, category),
                    near=near,
                    away_from=away,
                )
            ],
            message="",
        )
    return None
