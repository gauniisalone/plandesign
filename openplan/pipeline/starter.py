"""Plan de départ simplifié (accueil L1b, décision d'augus du 6 octobre) : un texte sans
fichier mais avec une surface (« 60 m² ») donne une pièce rectangulaire 4:3 de cette surface
intérieure, hauteur 2,50 m, murs `generated`, marquée « plan de départ simplifié » ; puis le
même chemin automatique. Ce n'est pas un relevé : le bandeau de l'atelier le dit."""

from __future__ import annotations

import math
import re
from uuid import UUID

from openplan.scene.ops import AddWall, DetectRooms, OpError, apply_ops
from openplan.schemas.common import Point2, Source
from openplan.schemas.elements import Room, Wall
from openplan.schemas.scene import Level, Project

STARTER_KEY = "starter"
LABEL = "Plan de départ simplifié"
ASPECT = 4 / 3
THICKNESS = 0.15
CONFIDENCE = 0.3
MIN_AREA_M2 = 8.0
MAX_AREA_M2 = 400.0

# « 60 m² », « 60m2 », « 45,5 m² », « 30 mètres carrés »
_AREA = re.compile(
    r"(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:m²|m2|mètres?\s+carrés?|metres?\s+carres?)",
    re.IGNORECASE,
)


def area_in_text(text: str) -> float | None:
    """La première surface lue dans un texte, si elle est dans les bornes."""
    for match in _AREA.finditer(text):
        value = float(match.group(1).replace(",", "."))
        if MIN_AREA_M2 <= value <= MAX_AREA_M2:
            return value
    return None


def starter_plan(project: Project, level_id: UUID, area_m2: float, *, actor: str) -> Project:
    """Une pièce rectangulaire 4:3 de `area_m2` mètres carrés intérieurs, en un pas
    d'historique ; refusé si le niveau a déjà des murs ou si la surface est hors bornes."""
    if not MIN_AREA_M2 <= area_m2 <= MAX_AREA_M2:
        msg = f"surface hors bornes : {area_m2:g} m² (de {MIN_AREA_M2:g} à {MAX_AREA_M2:g} m²)"
        raise OpError(msg)
    level = project.levels.get(level_id)
    if level is None:
        msg = f"niveau inconnu : {level_id}"
        raise OpError(msg)
    if any(isinstance(e, Wall) for e in level.elements.values()):
        msg = "ce niveau a déjà des murs : le plan de départ ne s'ajoute qu'à un plan vide"
        raise OpError(msg)
    # Murs posés sur leur axe : l'intérieur garde la surface demandée.
    inner_w = math.sqrt(area_m2 * ASPECT)
    inner_d = area_m2 / inner_w
    w, d = inner_w + THICKNESS, inner_d + THICKNESS
    corners = [Point2(x=0, y=0), Point2(x=w, y=0), Point2(x=w, y=d), Point2(x=0, y=d)]
    walls = [
        AddWall(
            start=corners[i],
            end=corners[(i + 1) % 4],
            thickness=THICKNESS,
            source=Source.GENERATED,
            confidence=CONFIDENCE,
        )
        for i in range(4)
    ]

    def mark(built: Level) -> Level:
        elements = dict(built.elements)
        for eid, element in built.elements.items():
            if isinstance(element, Room):
                metadata = {**element.metadata, STARTER_KEY: round(area_m2, 1)}
                elements[eid] = element.model_copy(update={"metadata": metadata})
        return built.model_copy(update={"elements": elements})

    return apply_ops(
        project, level_id, [*walls, DetectRooms()], actor=actor, label=LABEL, finish=mark
    )
