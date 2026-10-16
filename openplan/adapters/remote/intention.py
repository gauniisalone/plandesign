"""Rôle « Intention » : texte de l'utilisateur + description de la pièce → `LayoutBrief`.

Le modèle ne reçoit que du texte (jamais la photo). Sa réponse JSON est validée avec
tolérance : une catégorie hors catalogue, une position, une dimension que l'utilisateur n'a
pas écrite sont écartées et **dites** (`LayoutBrief.dropped`) ; seul un JSON absent ou
illisible est une erreur (`IntentionParseError`), que l'appelant peut réessayer une fois.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from openplan.adapters.protocols import LanguageModel, LlmMessage, LlmRequest, LlmResponse
from openplan.schemas.layout import PLACE_LABELS, BriefItem, ItemDimensions, LayoutBrief

SYSTEM_PROMPT = (
    "Tu es un assistant d'aménagement intérieur. Tu transformes le souhait d'un utilisateur "
    "en une liste de meubles à placer, en JSON strict, sans texte autour. Tu ne places rien : "
    "aucune coordonnée, aucune position. Tu n'inventes aucune dimension : tu ne donnes une "
    "dimension que si l'utilisateur l'a écrite en toutes lettres ou en chiffres. Tu ne "
    "modifies jamais les dimensions de la pièce. Tu choisis les catégories uniquement dans la "
    "liste fournie."
)

USER_TEMPLATE = """La pièce (description issue du plan, à ne pas modifier) :
{room}

Catégories de meubles disponibles (utilise exactement ces mots) : {categories}.

Repères de placement possibles pour "near" et "away_from" : {places}.

Souhait de l'utilisateur :
« {text} »

Réponds exactement avec cette structure :
{{
  "room_use": "<mot-clé : bedroom, office, bedroom_office, living_room, kids_room, guest_room…>",
  "items": [
    {{"category": "<catégorie de la liste>", "count": <entier>,
     "priority": "<required|nice_to_have>",
     "dimensions_m": null,
       ou {{"width": <m>, "depth": <m>, "height": <m>}} si l'utilisateur l'a écrit,
     "near": ["<repère>"], "away_from": ["<repère>"]}}
  ],
  "avoid": ["<interdit exprimé par l'utilisateur, en clair>"],
  "notes": "<ce que tu n'as pas compris ou as supposé>"
}}
Pas de position, pas de coordonnées, pas de dimension de pièce."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_POSITION_KEYS = frozenset({"position", "x", "y", "z", "yaw", "rotation", "placement", "pos"})
_ROOM_KEYS = frozenset({"room_width", "room_depth", "room_size", "room_dimensions", "room"})
DIMENSION_TOLERANCE_M = 0.011


class IntentionParseError(ValueError):
    """Le modèle n'a pas renvoyé le JSON attendu."""


def numbers_in(text: str) -> set[float]:
    """Nombres écrits par l'utilisateur, en mètres et en centimètres (« 1,60 », « 160 »)."""
    out: set[float] = set()
    for raw in _NUMBER.findall(text):
        value = float(raw.replace(",", "."))
        out.add(value)
        out.add(value / 100.0)
    return out


def _written_by_user(value: float, mentioned: set[float]) -> bool:
    return any(abs(value - m) < DIMENSION_TOLERANCE_M for m in mentioned)


def _dimensions(
    raw: Any, mentioned: set[float], label: str, dropped: list[str]
) -> ItemDimensions | None:
    if not isinstance(raw, dict):
        return None
    kept: dict[str, float] = {}
    for key in ("width", "depth", "height"):
        value = raw.get(key)
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        if _written_by_user(float(value), mentioned):
            kept[key] = float(value)
        else:
            dropped.append(
                f"{label} : {key} {value} m proposé par le modèle mais non écrit par vous : "
                "dimension du catalogue conservée"
            )
    if not kept:
        return None
    try:
        return ItemDimensions(**kept)
    except ValidationError as exc:
        dropped.append(f"{label} : dimensions invalides ({str(exc)[:80]})")
        return None


def parse_brief(text: str, categories: list[str], user_text: str) -> LayoutBrief:
    """Extrait et valide le JSON du modèle. Tolérant : chaque élément douteux est écarté avec
    son motif ; le JSON absent ou illisible lève `IntentionParseError`."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        msg = "réponse sans objet JSON"
        raise IntentionParseError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = f"JSON du brief illisible : {str(exc)[:160]}"
        raise IntentionParseError(msg) from exc
    if not isinstance(data, dict):
        msg = "réponse JSON qui n'est pas un objet"
        raise IntentionParseError(msg)
    dropped: list[str] = []
    mentioned = numbers_in(user_text)
    if _ROOM_KEYS & data.keys() or any(k.startswith("room_") and k != "room_use" for k in data):
        dropped.append(
            "le modèle a proposé des dimensions ou une forme de pièce : ignorées (la pièce vient "
            "du plan, jamais du modèle)"
        )
    items: list[BriefItem] = []
    raw_items = data.get("items")
    for index, raw in enumerate(raw_items if isinstance(raw_items, list) else [], start=1):
        if not isinstance(raw, dict):
            dropped.append(f"élément {index} : pas un objet")
            continue
        category = raw.get("category")
        label = f"élément {index} ({category})" if isinstance(category, str) else f"élément {index}"
        if not isinstance(category, str) or category not in categories:
            dropped.append(f"{label} : catégorie hors catalogue, écartée")
            continue
        if _POSITION_KEYS & raw.keys():
            dropped.append(
                f"{label} : position proposée par le modèle, ignorée (le placement revient au "
                "moteur géométrique)"
            )
        places = set(PLACE_LABELS)
        near = (
            [p for p in raw.get("near", []) if isinstance(p, str) and p in places]
            if isinstance(raw.get("near"), list)
            else []
        )
        away = (
            [p for p in raw.get("away_from", []) if isinstance(p, str) and p in places]
            if isinstance(raw.get("away_from"), list)
            else []
        )
        priority = raw.get("priority")
        raw_count = raw.get("count", 1)
        count = (
            int(raw_count)
            if isinstance(raw_count, int | float) and not isinstance(raw_count, bool)
            else 1
        )
        try:
            items.append(
                BriefItem(
                    category=category,
                    count=max(1, min(10, count)),
                    priority="nice_to_have" if priority == "nice_to_have" else "required",
                    dimensions_m=_dimensions(raw.get("dimensions_m"), mentioned, label, dropped),
                    near=near,  # type: ignore[arg-type]
                    away_from=away,  # type: ignore[arg-type]
                )
            )
        except ValidationError as exc:
            dropped.append(f"{label} : invalide ({str(exc)[:80]})")
    avoid_raw = data.get("avoid")
    avoid = (
        [a for a in avoid_raw if isinstance(a, str) and a.strip()]
        if isinstance(avoid_raw, list)
        else []
    )
    room_use = data.get("room_use")
    notes = data.get("notes")
    return LayoutBrief(
        room_use=room_use if isinstance(room_use, str) else "",
        items=items,
        avoid=avoid[:10],
        notes=notes[:500] if isinstance(notes, str) else "",
        dropped=dropped,
    )


class IntentionAgent:
    """Compose la requête (texte seul) et l'envoie par la fonction liée à la passerelle."""

    def __init__(self, model: LanguageModel, send: Callable[[LlmRequest], LlmResponse]) -> None:
        self.model = model
        self.send = send
        self.model_id = f"llm:{model.model_id}"

    @staticmethod
    def user_prompt(text: str, room: str, categories: list[str]) -> str:
        return USER_TEMPLATE.format(
            room=room,
            categories=", ".join(categories),
            places=", ".join(f"{key} ({label})" for key, label in PLACE_LABELS.items()),
            text=text.strip(),
        )

    def ask(
        self, text: str, room: str, categories: list[str], *, retry_hint: str | None = None
    ) -> LlmResponse:
        messages = [
            LlmMessage(role="system", content=SYSTEM_PROMPT),
            LlmMessage(role="user", content=self.user_prompt(text, room, categories)),
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
            purpose="intention",
            messages=messages,
            json_mode=True,
            temperature=0.1,
            max_tokens=900,
        )
        return self.send(request)
