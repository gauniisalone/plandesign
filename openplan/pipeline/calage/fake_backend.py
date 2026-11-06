"""KAN-124 : le faux modèle de l'assistant de calage (OPENPLAN_ASSISTANT_URL=fake).

Pour les e2e et les essais sur la boucle locale seulement (refusé sur une API exposée, comme le
mailer `fake`) : des règles fixes, sans aucun modèle. Un nombre dans le message est lu comme une
dimension du champ demandé (« 2m45 », « 2,60 », « 245 cm » → mètres) ; « passe » passe la
question ; « lent » simule un modèle trop lent (repli sur le formulaire) ; sinon, une demande de
précision. La sortie suit le même schéma que celle d'un vrai modèle.
"""

from __future__ import annotations

import json
import re
from typing import Any

from openplan.adapters.protocols import AssistantTimeoutError

FAKE_URL = "fake"
NUMBER = re.compile(r"(\d+)(?:\s*(m|,|\.)\s*(\d+))?\s*(cm|m)?", re.IGNORECASE)
DIMENSIONS = ("hauteur_plafond", "cote_connue")


def _metres(text: str) -> float | None:
    found = NUMBER.search(text)
    if found is None:
        return None
    whole, sep, frac, unit = found.groups()
    value = float(f"{whole}.{frac}") if sep and frac else float(whole)
    if (unit or "").lower() == "cm" or (not sep and value > 20):
        value /= 100
    return round(value, 3)


class FakeAssistant:
    model_id = "assistant:fake"

    def extract(
        self, system: str, user: str, schema: dict[str, Any] | None, timeout_s: float
    ) -> str:
        del system, schema
        data = json.loads(user)
        field = data.get("question_app")
        message = str(data.get("message_utilisateur", ""))
        lowered = message.lower()
        if "lent" in lowered:
            msg = f"faux modèle : lenteur simulée ({timeout_s:.0f} s)"
            raise AssistantTimeoutError(msg)
        out: dict[str, Any] = {
            "outil": "none",
            "champ": field if field in DIMENSIONS else None,
            "valeur": None,
            "unite": None,
            "element": None,
            "certitude": "inconnue",
            "besoin_clarification": False,
        }
        if "passe" in lowered:
            out.update(outil="skip")
            return json.dumps(out)
        value = _metres(message) if field in DIMENSIONS else None
        if value is None:
            out.update(besoin_clarification=field in DIMENSIONS)
            return json.dumps(out)
        bounds = (1.8, 4.0) if field == "hauteur_plafond" else (0.3, 20.0)
        if not bounds[0] <= value <= bounds[1]:
            out.update(valeur=value, besoin_clarification=True)
            return json.dumps(out)
        out.update(outil="set_dimension", valeur=value, unite="m", certitude="sure")
        return json.dumps(out)
