"""Rôle « Assistant » : phrase de l'utilisateur + description du plan → intentions typées.

Texte seul (jamais la photo). La réponse JSON est validée avec tolérance, comme le brief :
une intention inconnue, une catégorie hors catalogue, une coordonnée ou une dimension que
l'utilisateur n'a pas écrite sont écartées et dites (`AssistantReply.dropped`) ; seul un JSON
absent ou illisible est une erreur (`AssistantParseError`), que l'appelant réessaie une fois.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any, get_args

from pydantic import ValidationError

from openplan.adapters.protocols import LanguageModel, LlmMessage, LlmRequest, LlmResponse
from openplan.adapters.remote.intention import _dimensions, numbers_in
from openplan.schemas.assistant import (
    INTENT_LABELS,
    ROOM_TYPES,
    AssistantIntent,
    AssistantReply,
    Dimension,
    OpeningWord,
)
from openplan.schemas.layout import PLACE_LABELS
from openplan.schemas.numbers import metres

SYSTEM_PROMPT = (
    "Tu es l'assistant d'un logiciel de plan d'intérieur. L'utilisateur te parle de sa pièce ; "
    "tu traduis sa phrase en une liste d'intentions, en JSON strict, sans texte autour. Tu ne "
    "places rien toi-même : aucune coordonnée, aucune position, aucun mur ; c'est le logiciel "
    "qui calcule les positions. Tu ne donnes une dimension que si l'utilisateur l'a écrite. "
    "Si une demande est ambiguë (deux meubles possibles, cible inconnue), tu poses une question "
    "(intention ask) au lieu de deviner. Si l'utilisateur pose une question sur la pièce, tu "
    "réponds (intention answer) à partir de la description, sans rien changer. Ce que tu ne "
    "sais pas faire, tu le dis (answer)."
)

USER_TEMPLATE = """Le plan (description exacte, à ne pas modifier) :
{room}

Catégories de meubles du catalogue (utilise exactement ces mots) : {categories}.
Repères de placement pour "near" et "away_from" : {places}.
Intentions possibles : add_furniture (category, count, near, away_from, dimensions_m si écrites),
move_furniture (target = le meuble tel que l'utilisateur le nomme, near, away_from),
remove_furniture (target), propose_layouts (items optionnels comme add_furniture),
set_dimension (dimension = wall | length | width | height, target = le mur nommé par l'utilisateur
pour wall, value_m = la valeur écrite par l'utilisateur en mètres),
add_opening (opening = door | window | double_door | sliding_door | pocket_door | folding_door
| french_window | sliding_window, leaves = 2 pour une porte-fenêtre double, target = le mur
nommé, value_m = largeur si écrite),
move_opening (target = la porte ou la fenêtre telle que l'utilisateur la désigne, avec sa pièce
et son orientation si dites, delta_m = l'écart écrit en mètres, négatif vers la gauche, positif
vers la droite), remove_opening (target),
resize_opening (target, dimension = width | height, value_m = la nouvelle valeur écrite, ou
delta_m = l'écart écrit, positif pour agrandir, négatif pour réduire),
add_stairs (stairs = straight | quarter_turn | half_turn | spiral,
target = le mur nommé ou "centre"),
name_room (name = le nom voulu, room_type parmi {room_types}),
ask (text = ta question), answer (text = ta réponse).
{memory}

Exemples : « la pièce fait 5 m sur 3,5 » → deux set_dimension (length 5.0, width 3.5) ;
« le mur du fond fait 4,5 m » → set_dimension (dimension wall, target "le mur du fond",
value_m 4.5) ; « hauteur sous plafond 2,60 » → set_dimension (height, 2.6) ;
« ajoute une porte sur le mur de gauche » → add_opening (opening door, target "le mur de
gauche") ; « appelle cette pièce Chambre d'amis » → name_room (name "Chambre d'amis") ;
« mets un évier sous la fenêtre » → add_furniture (category kitchen_sink, near ["window"]) ;
« ajoute une douche dans le coin » → add_furniture (category shower, near ["corner"]) ;
« ajoute un escalier droit contre le mur de gauche » → add_stairs (stairs straight, target "mur de
gauche") ; « mets une trémie avec garde-corps au centre » → add_stairs (stairs straight, target
"centre") ;
« déplace la fenêtre de la chambre de 50 cm vers la gauche » → move_opening (target "la fenêtre
de la chambre", delta_m -0.5) ; « supprime la porte du placard » → remove_opening (target "la
porte du placard") ; « élargis la fenêtre du salon à 1,20 m » → resize_opening (target "la
fenêtre du salon", dimension width, value_m 1.2). Une porte ou une fenêtre déjà dans le plan se
modifie par ces intentions, jamais par add_opening ni remove_furniture.
Une valeur écrite par l'utilisateur n'est pas une question : tu l'appliques.

Phrase de l'utilisateur :
« {text} »

Réponds exactement avec cette structure :
{{
  "intents": [
    {{"kind": "<intention>", "category": "<catégorie ou null>", "count": <entier>,
     "near": ["<repère>"], "away_from": ["<repère>"], "target": "<cible ou null>",
     "dimensions_m": null, "text": "<question ou réponse, sinon vide>",
     "dimension": null, "value_m": null, "delta_m": null, "opening": null, "name": null,
     "room_type": null}}
  ],
  "message": "<une phrase courte pour l'utilisateur>"
}}
Pas de position, pas de coordonnées, pas de dimension de pièce."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_POSITION_KEYS = frozenset({"position", "x", "y", "z", "yaw", "rotation", "placement", "pos"})
MAX_INTENTS = 8
WIDTH: Dimension = "width"


class AssistantParseError(ValueError):
    """Le modèle n'a pas renvoyé le JSON attendu."""


def _places(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, str) and p in PLACE_LABELS]


def _text(raw: Any, limit: int = 400) -> str:
    return raw.strip()[:limit] if isinstance(raw, str) else ""


def _value_m(raw: Any, mentioned: set[float], label: str, dropped: list[str]) -> float | None:
    """Une dimension n'est gardée que si l'utilisateur l'a écrite (mètres ou centimètres)."""
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw <= 0:
        return None
    value = float(raw)
    if any(abs(value - m) < 0.011 for m in mentioned):
        return value
    # Les décimales telles que le modèle les a écrites (« 1,6 m », « 2,0 m »).
    shown = metres(value, len(str(value).partition(".")[2]))
    dropped.append(f"{label} : {shown} proposé par le modèle mais non écrit par vous, ignoré")
    return None


def _delta_m(raw: Any, mentioned: set[float], label: str, dropped: list[str]) -> float | None:
    """Un écart signé (KAN-32) n'est gardé que si sa valeur absolue a été écrite."""
    if not isinstance(raw, int | float) or isinstance(raw, bool) or raw == 0:
        return None
    value = float(raw)
    if abs(value) > 10.0:
        dropped.append(f"{label} : écart de {value} m hors bornes, ignoré")
        return None
    if any(abs(abs(value) - m) < 0.011 for m in mentioned):
        return value
    dropped.append(f"{label} : écart de {value} m proposé par le modèle mais non écrit par vous")
    return None


def parse_reply(  # noqa: PLR0912, PLR0915
    text: str,
    categories: list[str],
    user_text: str,
    aliases: dict[str, str] | None = None,
) -> AssistantReply:
    """Extrait et valide le JSON du modèle ; le douteux est écarté avec son motif."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        msg = "réponse sans objet JSON"
        raise AssistantParseError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = f"JSON de l'assistant illisible : {str(exc)[:160]}"
        raise AssistantParseError(msg) from exc
    if not isinstance(data, dict):
        msg = "réponse JSON qui n'est pas un objet"
        raise AssistantParseError(msg)
    dropped: list[str] = []
    mentioned = numbers_in(user_text)
    intents: list[AssistantIntent] = []
    raw_intents = data.get("intents")
    if not isinstance(raw_intents, list):
        raw_intents = [data] if "kind" in data else []
    for index, raw in enumerate(raw_intents[:MAX_INTENTS], start=1):
        if not isinstance(raw, dict):
            dropped.append(f"intention {index} : pas un objet")
            continue
        kind = raw.get("kind")
        if not isinstance(kind, str) or kind not in INTENT_LABELS:
            dropped.append(f"intention {index} : « {kind} » inconnue, écartée")
            continue
        label = f"intention {index} ({INTENT_LABELS[kind]})"
        if _POSITION_KEYS & raw.keys():
            dropped.append(f"{label} : position proposée par le modèle, ignorée")
        category = raw.get("category")
        if isinstance(category, str) and category not in categories and aliases:
            category = aliases.get(category.strip().lower(), category)
        if isinstance(category, str) and category not in categories:
            if kind == "add_furniture":
                dropped.append(f"{label} : catégorie « {category} » hors catalogue, écartée")
                continue
            category = None
        if kind == "add_furniture" and not isinstance(category, str):
            dropped.append(f"{label} : sans catégorie, écartée")
            continue
        raw_count = raw.get("count", 1)
        count = (
            int(raw_count)
            if isinstance(raw_count, int | float) and not isinstance(raw_count, bool)
            else 1
        )
        value_m = _value_m(raw.get("value_m"), mentioned, label, dropped)
        dimension = raw.get("dimension")
        if dimension not in ("wall", "length", "width", "height"):
            dimension = None
        if kind == "set_dimension" and value_m is None:
            dropped.append(f"{label} : sans valeur écrite par vous, écartée")
            continue
        delta_m = _delta_m(raw.get("delta_m"), mentioned, label, dropped)
        if kind == "resize_opening" and dimension != "height":
            dimension = WIDTH  # une ouverture n'a qu'une largeur et une hauteur
        opening = raw.get("opening")
        if opening not in get_args(OpeningWord):
            opening = None
        if kind == "add_opening" and opening is None:
            dropped.append(f"{label} : porte ou fenêtre ?, écartée")
            continue
        room_type = raw.get("room_type")
        if room_type not in ROOM_TYPES:
            room_type = None
        near, away = _places(raw.get("near")), _places(raw.get("away_from"))
        # Le même repère des deux côtés (« loin de la fenêtre » rendu near + away) : le
        # « loin de » l'emporte, c'est la contrainte la plus forte.
        near = [p for p in near if p not in away]
        try:
            intents.append(
                AssistantIntent(
                    kind=kind,  # type: ignore[arg-type]
                    category=category if isinstance(category, str) else None,
                    count=max(1, min(10, count)),
                    dimensions_m=_dimensions(raw.get("dimensions_m"), mentioned, label, dropped),
                    near=near,  # type: ignore[arg-type]
                    away_from=away,  # type: ignore[arg-type]
                    target=_text(raw.get("target"), 120) or None,
                    text=_text(raw.get("text")),
                    value_m=value_m,
                    delta_m=delta_m,
                    dimension=dimension,
                    opening=opening,
                    name=_text(raw.get("name"), 60) or None,
                    room_type=room_type,
                )
            )
        except ValidationError as exc:
            dropped.append(f"{label} : invalide ({str(exc)[:80]})")
    return AssistantReply(intents=intents, message=_text(data.get("message"), 300), dropped=dropped)


class AssistantAgent:
    """Compose la requête (texte seul) et l'envoie par la fonction liée à la passerelle."""

    def __init__(
        self,
        model: LanguageModel,
        send: Callable[[LlmRequest], LlmResponse],
        *,
        labels: dict[str, str] | None = None,
    ) -> None:
        self.model = model
        self.send = send
        self.model_id = f"llm:{model.model_id}"
        self.labels = labels or {}
        """Libellé français par catégorie, pour que le modèle relie « table de chevet » à
        `nightstand`."""

    def user_prompt(
        self, text: str, room: str, categories: list[str], *, last_label: str | None = None
    ) -> str:
        memory = (
            f"Dernier meuble dont on a parlé : {last_label} (« le », « celui-ci » le désignent)."
            if last_label
            else ""
        )
        return USER_TEMPLATE.format(
            room=room,
            room_types=", ".join(ROOM_TYPES),
            memory=memory,
            categories=", ".join(
                f"{c} ({self.labels[c]})" if c in self.labels else c for c in categories
            ),
            places=", ".join(f"{key} ({label})" for key, label in PLACE_LABELS.items()),
            text=text.strip(),
        )

    def ask(
        self,
        text: str,
        room: str,
        categories: list[str],
        *,
        retry_hint: str | None = None,
        last_label: str | None = None,
    ) -> LlmResponse:
        messages = [
            LlmMessage(role="system", content=SYSTEM_PROMPT),
            LlmMessage(
                role="user",
                content=self.user_prompt(text, room, categories, last_label=last_label),
            ),
        ]
        if retry_hint:
            messages.append(LlmMessage(role="assistant", content=retry_hint[:400]))
            messages.append(
                LlmMessage(
                    role="user",
                    content="Ta réponse n'était pas l'objet JSON demandé. Renvoie uniquement "
                    "l'objet JSON, sans texte autour.",
                )
            )
        request = LlmRequest(
            purpose="assistant",
            messages=messages,
            json_mode=True,
            temperature=0.1,
            max_tokens=700,
        )
        return self.send(request)
