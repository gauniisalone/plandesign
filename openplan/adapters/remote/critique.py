"""Rôle « Critique » (roadmap 3.5) : le JSON d'une variante → remarques structurées.

Le modèle reçoit du texte seulement : la description de la pièce (cotes du plan), le brief,
les meubles de la variante avec leurs distances aux repères, et les remarques déjà connues
des règles géométriques. Il renvoie des remarques qui citent un meuble (par numéro) et une
règle du vocabulaire fermé (`RULE_LABELS`), avec une importance et, si possible, une action
(« déplacer près de… », « déplacer loin de… », « retirer »). Il ne déplace rien : toute
coordonnée est ignorée et dite. Une remarque sans meuble reconnu est écartée avec son motif ;
seul un JSON absent ou illisible est une erreur (`CritiqueParseError`), que l'appelant peut
réessayer une fois. Rien de ce qu'il dit n'est appliqué : chaque action recalcule une variante
révisée que l'utilisateur relit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

from openplan.adapters.protocols import LanguageModel, LlmMessage, LlmRequest, LlmResponse
from openplan.adapters.remote.fence import strip_fence
from openplan.schemas.layout import (
    PLACE_LABELS,
    PLACE_OF,
    RULE_LABELS,
    USAGE_RULES,
    Critique,
    CritiqueAction,
    Remark,
    action_label,
    sort_remarks,
)

SYSTEM_PROMPT = (
    "Tu es un critique d'aménagement intérieur, concret et bref. On te donne une pièce (cotes "
    "issues du plan), le souhait de l'utilisateur et une variante d'aménagement déjà calculée. "
    "Tu relèves ce qui gêne l'usage (circulation, lumière, ergonomie, bruit) et ce qui "
    "contredit le souhait, en JSON strict, sans texte autour. Tu ne places rien : aucune "
    "coordonnée, aucune position. Tu proposes au plus une action par remarque, parmi : "
    "déplacer près d'un repère, déplacer loin d'un repère, retirer. Tu cites chaque meuble par "
    "son numéro et chaque remarque par une règle de la liste fournie. Les collisions, murs, "
    "portes, fenêtres, dégagements, accès, hauteurs sous pente et souhaits près / loin sont "
    "déjà vérifiés par le calcul : tu ne les affirmes jamais toi-même, tu ne répètes pas les "
    "remarques déjà connues, tu parles d'usage (circulation, ergonomie, lumière, bruit, style)."
)

USER_TEMPLATE = """La pièce (description issue du plan, à ne pas modifier) :
{room}

Souhait de l'utilisateur (brief) : {brief}

Meubles de la variante (numéro. nom, dimensions, où il est) :
{furniture}

Remarques déjà connues (règles géométriques, à ne pas répéter) :
{known}

Règles possibles pour "rule" (usage seulement, la géométrie est déjà vérifiée) : {rules}.
Repères possibles pour "target" : {places}.

Réponds exactement avec cette structure :
{{
  "verdict": "<une phrase sur la variante>",
  "strengths": ["<point fort, une phrase>"],
  "remarks": [
    {{"furniture": <numéro du meuble>, "rule": "<règle de la liste>",
     "importance": "<high|medium|low>", "message": "<une phrase concrète>",
     "action": {{"kind": "<move_near|move_away|remove>", "target": "<repère ou null>"}} ou null}}
  ]
}}
Trois remarques au plus, la plus importante d'abord. Pas de position, pas de coordonnées."""

_POSITION_KEYS = frozenset({"position", "x", "y", "z", "yaw", "rotation", "placement", "pos"})
_ACTION_KINDS = frozenset({"move_near", "move_away", "remove"})
MAX_REMARKS = 6
MAX_MESSAGE = 300


class CritiqueParseError(ValueError):
    """Le modèle n'a pas renvoyé le JSON attendu."""


FurnitureRef = tuple[UUID, str]
"""(identifiant, libellé) d'un meuble de la variante, dans l'ordre des numéros du prompt."""


def _load_object(text: str) -> dict[str, Any]:
    cleaned = strip_fence(text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        msg = "réponse sans objet JSON"
        raise CritiqueParseError(msg)
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        msg = f"JSON de la critique illisible : {str(exc)[:160]}"
        raise CritiqueParseError(msg) from exc
    if not isinstance(data, dict):
        msg = "réponse JSON qui n'est pas un objet"
        raise CritiqueParseError(msg)
    return data


def _furniture_ref(raw: Any, furniture: list[FurnitureRef]) -> FurnitureRef | None:
    """Numéro (1..n) ou libellé exact ; None si le meuble n'est pas dans la variante."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        index = int(raw)
        if 1 <= index <= len(furniture):
            return furniture[index - 1]
        return None
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text.isdigit():
            return _furniture_ref(int(text), furniture)
        for ref in furniture:
            if ref[1].lower() == text:
                return ref
    return None


def _action(raw: Any, ref: FurnitureRef, label: str, dropped: list[str]) -> CritiqueAction | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        dropped.append(f"{label} : action illisible, remarque gardée sans action")
        return None
    if _POSITION_KEYS & raw.keys():
        dropped.append(
            f"{label} : position proposée par le modèle, ignorée (le placement revient au "
            "moteur géométrique) ; remarque gardée sans action"
        )
        return None
    kind = raw.get("kind")
    if kind not in _ACTION_KINDS:
        dropped.append(f"{label} : action inconnue ({kind!r}), remarque gardée sans action")
        return None
    target = raw.get("target")
    if kind == "remove":
        target = None
    elif not isinstance(target, str) or target not in PLACE_LABELS:
        dropped.append(
            f"{label} : repère inconnu ({target!r}) pour un déplacement, remarque gardée sans "
            "action"
        )
        return None
    return CritiqueAction(
        kind=kind,
        furniture_id=ref[0],
        target=target,  # type: ignore[arg-type]
        label=action_label(kind, target, ref[1]),
    )


def parse_critique(text: str, furniture: list[FurnitureRef]) -> Critique:
    """Extrait et valide le JSON du modèle. Tolérant : une remarque sans meuble reconnu est
    écartée avec son motif, une règle inconnue devient « autre », une action douteuse tombe
    (la remarque reste) ; le JSON absent ou illisible lève `CritiqueParseError`."""
    data = _load_object(text)
    dropped: list[str] = []
    remarks: list[Remark] = []
    raw_remarks = data.get("remarks")
    for index, raw in enumerate(raw_remarks if isinstance(raw_remarks, list) else [], start=1):
        if not isinstance(raw, dict):
            dropped.append(f"remarque {index} : pas un objet")
            continue
        ref = _furniture_ref(raw.get("furniture"), furniture)
        if ref is None:
            dropped.append(
                f"remarque {index} : meuble « {raw.get('furniture')} » absent de la variante, "
                "écartée"
            )
            continue
        message = raw.get("message")
        if not isinstance(message, str) or not message.strip():
            dropped.append(f"remarque {index} ({ref[1]}) : sans texte, écartée")
            continue
        rule = raw.get("rule")
        if not isinstance(rule, str) or rule not in RULE_LABELS:
            dropped.append(f"remarque {index} ({ref[1]}) : règle « {rule} » hors liste → « autre »")
            rule = "other"
        importance = raw.get("importance")
        label = f"remarque {index} ({ref[1]})"
        remarks.append(
            Remark(
                importance=importance if importance in ("high", "medium", "low") else "medium",
                rule=rule,
                furniture_id=ref[0],
                furniture_label=ref[1],
                message=message.strip()[:MAX_MESSAGE],
                action=_action(raw.get("action"), ref, label, dropped),
                origin="model",
            )
        )
        if len(remarks) >= MAX_REMARKS:
            break
    strengths_raw = data.get("strengths")
    strengths = (
        [s.strip()[:MAX_MESSAGE] for s in strengths_raw if isinstance(s, str) and s.strip()]
        if isinstance(strengths_raw, list)
        else []
    )
    verdict = data.get("verdict")
    return Critique(
        verdict=verdict.strip()[:MAX_MESSAGE] if isinstance(verdict, str) else "",
        remarks=sort_remarks(remarks),
        strengths=strengths[:5],
        dropped=dropped,
    )


class CritiqueAgent:
    """Compose la requête (texte seul) et l'envoie par la fonction liée à la passerelle."""

    def __init__(self, model: LanguageModel, send: Callable[[LlmRequest], LlmResponse]) -> None:
        self.model = model
        self.send = send
        self.model_id = f"llm:{model.model_id}"

    @staticmethod
    def user_prompt(room: str, brief: str, furniture: str, known: str) -> str:
        return USER_TEMPLATE.format(
            room=room,
            brief=brief or "non précisé",
            furniture=furniture,
            known=known or "aucune",
            rules=", ".join(
                f"{key} ({label})" for key, label in RULE_LABELS.items() if key in USAGE_RULES
            ),
            places=", ".join(f"{key} ({label})" for key, label in PLACE_OF.items()),
        )

    def ask(self, prompt: str, *, retry_hint: str | None = None) -> LlmResponse:
        messages = [
            LlmMessage(role="system", content=SYSTEM_PROMPT),
            LlmMessage(role="user", content=prompt),
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
            purpose="critique",
            messages=messages,
            json_mode=True,
            temperature=0.2,
            max_tokens=900,
        )
        return self.send(request)
