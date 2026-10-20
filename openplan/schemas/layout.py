"""Brief d'aménagement (roadmap 3.3, rôle Intention) : ce que veut l'utilisateur, structuré.

Le modèle de langage traduit une phrase (« chambre d'amis avec un coin bureau ») en un
`LayoutBrief` : des meubles du catalogue (catégorie fermée), leur importance et des
préférences de placement dans un vocabulaire fermé. **Aucune coordonnée** : le placement
revient au moteur géométrique (3.4). Une dimension n'est acceptée que si l'utilisateur l'a
écrite lui-même ; le brief ne peut jamais redimensionner la pièce. Source `generated`,
confiance plafonnée, à relire et corriger avant de lancer une proposition.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from openplan.schemas.common import StrictModel
from openplan.schemas.intent import Zone

Place = Literal[
    "wall",
    "window",
    "door",
    "door_swing",
    "radiator",
    "low_wall",
    "ridge",
    "outlet",
    "corner",
    "skylight",
]
"""Repères de placement compris par le générateur : contre un mur, près d'une fenêtre, loin
du débattement d'une porte, hors du dégagement d'un radiateur, dans la zone basse d'un
comble (`low_wall`, sous 1,80 m), sous le faîtage (`ridge`), près d'une prise, dans un coin
de la pièce (`corner` : douche, bureau d'angle), loin du caisson d'un velux (`skylight`,
KAN-67)."""
PLACE_LABELS: dict[str, str] = {
    "wall": "un mur",
    "window": "une fenêtre",
    "door": "une porte",
    "door_swing": "le débattement d'une porte",
    "radiator": "un radiateur",
    "low_wall": "la zone basse sous pente",
    "ridge": "le faîtage",
    "outlet": "une prise",
    "corner": "un coin de la pièce",
    "skylight": "un velux",
}
Priority = Literal["required", "nice_to_have"]
MAX_BRIEF_CONFIDENCE = 0.6
"""Un brief produit par un modèle ne vaut jamais plus qu'une estimation (comme la perception)."""


class ItemDimensions(StrictModel):
    """Dimensions voulues par l'utilisateur pour un meuble (mètres) ; None = catalogue."""

    width: float | None = Field(default=None, gt=0.0, lt=6.0)
    depth: float | None = Field(default=None, gt=0.0, lt=6.0)
    height: float | None = Field(default=None, gt=0.0, lt=4.0)

    @property
    def empty(self) -> bool:
        return self.width is None and self.depth is None and self.height is None


class BriefItem(StrictModel):
    category: str
    """Catégorie du catalogue (`CatalogAsset.category`)."""
    count: int = Field(default=1, ge=1, le=10)
    priority: Priority = "required"
    dimensions_m: ItemDimensions | None = None
    near: list[Place] = Field(default_factory=list)
    away_from: list[Place] = Field(default_factory=list)
    zone: Zone | None = None
    """Zone de la pièce (V1, brief par zones) : le meuble cherche sa place dans sa baie."""


class LayoutBrief(StrictModel):
    room_use: str = ""
    """Usage de la pièce en un mot-clé libre du modèle (« bedroom_office »), informatif."""
    items: list[BriefItem] = Field(default_factory=list)
    keep: list[UUID] = Field(default_factory=list)
    """Éléments existants à ne pas déplacer (meubles « à conserver »)."""
    avoid: list[str] = Field(default_factory=list)
    """Interdits en clair (« rien devant le radiateur »), transmis au générateur en souple."""
    notes: str = ""
    """Ce que le modèle n'a pas compris ou a supposé."""
    dropped: list[str] = Field(default_factory=list)
    """Ce qui a été écarté de la réponse du modèle, et pourquoi (catégorie hors catalogue,
    position proposée, dimension non écrite par l'utilisateur…)."""

    @property
    def required(self) -> list[BriefItem]:
        return [i for i in self.items if i.priority == "required"]


# ------------------------------------------------------------------ critique (3.5)
PLACE_OF: dict[str, str] = {
    "wall": "d'un mur",
    "window": "d'une fenêtre",
    "door": "d'une porte",
    "door_swing": "du débattement d'une porte",
    "radiator": "d'un radiateur",
    "low_wall": "de la zone basse sous pente",
    "ridge": "du faîtage",
    "outlet": "d'une prise",
    "corner": "d'un coin de la pièce",
    "skylight": "d'un velux",
}
"""Compléments de « près … » / « loin … » (« Déplacer le bureau loin d'une fenêtre »)."""
Importance = Literal["high", "medium", "low"]
IMPORTANCE_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
RULE_LABELS: dict[str, str] = {
    "furniture_overlap": "collision entre meubles",
    "furniture_in_wall": "meuble dans un mur",
    "door_swing_blocked": "débattement de porte gêné",
    "window_blocked": "fenêtre masquée",
    "clearance_blocked": "dégagement insuffisant",
    "furniture_unreachable": "meuble inaccessible",
    "element_too_tall_under_slope": "trop haut sous la pente",
    "velux_casing": "sous le caisson d'un velux",
    "bed_headroom": "hauteur libre insuffisante au-dessus du lit",
    "bed_access": "trop bas pour se tenir debout devant le lit",
    "furniture_in_stairwell": "meuble dans la trémie",
    "stairs_arrival_blocked": "arrivée d'escalier encombrée",
    "stairs_headroom": "échappée d'escalier insuffisante",
    "stairs_on_wall": "trémie sur un mur",
    "sliding_zone_blocked": "passage du vantail coulissant gêné",
    "slide_no_room": "pas de place pour le vantail ouvert",
    "stairs_outside_room": "trémie hors de la pièce",
    "preference_near": "souhait « près de » non tenu",
    "preference_away": "souhait « loin de » non tenu",
    "circulation": "circulation",
    "ergonomics": "ergonomie",
    "light": "lumière",
    "acoustics": "bruit",
    "style": "style",
    "other": "autre",
}
"""Vocabulaire fermé des règles qu'une remarque peut citer : les règles géométriques de
`validation/layout.py`, les souhaits du brief, et quelques critères d'usage pour le modèle."""
USAGE_RULES: frozenset[str] = frozenset(
    {"circulation", "ergonomics", "light", "acoustics", "style", "other"}
)
"""Règles d'usage, seules que le modèle peut avancer de lui-même : les autres (collision,
mur, porte, fenêtre, dégagement, accès, hauteur sous pente, souhaits) sont **calculées** ; une
remarque du modèle qui en cite une sans que le calcul la confirme est écartée."""
ActionKind = Literal["move_near", "move_away", "remove"]


class CritiqueAction(StrictModel):
    """Action en un clic proposée par une remarque : recalcule une variante révisée que
    l'utilisateur relit ; rien n'entre dans la scène sans « Appliquer »."""

    kind: ActionKind
    furniture_id: UUID
    target: Place | None = None
    """Repère pour `move_near` / `move_away` ; None pour `remove`."""
    label: str = ""
    """Texte du bouton (« Déplacer le bureau loin d'une fenêtre »)."""


class Remark(StrictModel):
    importance: Importance = "medium"
    rule: str = "other"
    """Clé de `RULE_LABELS`."""
    rule_label: str = ""
    furniture_id: UUID | None = None
    furniture_label: str = ""
    message: str
    action: CritiqueAction | None = None
    origin: Literal["rules", "model"] = "rules"
    """`rules` : calculée par les règles géométriques ; `model` : proposée par le modèle
    (source `generated`, validée mais jamais appliquée seule)."""

    @model_validator(mode="after")
    def _fill_rule_label(self) -> Remark:
        if not self.rule_label:
            object.__setattr__(self, "rule_label", RULE_LABELS.get(self.rule, RULE_LABELS["other"]))
        return self


class Critique(StrictModel):
    """Remarques triées par importance ; chaque remarque cite un meuble et une règle."""

    verdict: str = ""
    remarks: list[Remark] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    """Ce qui a été écarté de la réponse du modèle, et pourquoi."""


def sort_remarks(remarks: list[Remark]) -> list[Remark]:
    return sorted(remarks, key=lambda r: (IMPORTANCE_RANK.get(r.importance, 9), r.furniture_label))


def action_label(kind: ActionKind, target: str | None, furniture_label: str) -> str:
    if kind == "remove":
        return f"Retirer {furniture_label}"
    where = PLACE_OF.get(target or "", "du repère")
    return f"Déplacer {furniture_label} {'près' if kind == 'move_near' else 'loin'} {where}"
