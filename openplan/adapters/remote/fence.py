"""Le JSON d'une réponse de modèle sans sa clôture Markdown (```json … ```), en temps linéaire.

KAN-108 : la regex d'avant (`^```(?:json)?\\s*|\\s*```$`, multiligne) revenait en arrière sur
une longue suite d'espaces (règle Sonar S8786) ; la réponse vient d'un serveur de modèles,
donc de l'extérieur. Même effet ligne par ligne : la clôture d'ouverture (en début de ligne,
`json` facultatif) et de fermeture (en fin de ligne) sont retirées ; les espaces qui restent ne
gênent pas la lecture du JSON.
"""

from __future__ import annotations

FENCE = "```"


def _unfenced(line: str) -> str:
    if line.startswith(FENCE):
        line = line.removeprefix(FENCE).removeprefix("json")
    return line.removesuffix(FENCE)


def strip_fence(text: str) -> str:
    return "\n".join(_unfenced(line) for line in text.strip().split("\n")).strip()
