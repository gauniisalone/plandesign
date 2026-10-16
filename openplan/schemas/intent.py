"""Intention de design d'une pièce (V1, note 10) : une seule représentation de ce que la pièce
doit devenir, rangée dans la `Room`. Elle dit *quoi* (type, style, ambiance, matières, lumière,
meubles et zones voulus, vues), jamais *où* : le moteur géométrique place.

Chaque champ a sa provenance (`sources`) : `user_defined` quand l'utilisateur l'a dit ou choisi,
`inferred` (par défaut) quand un calcul l'a déduit. **Une étape automatique ne réécrit jamais un
champ choisi par l'utilisateur** (principe d'augus du 5 octobre 2026) : voir `merge_intent`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from openplan.schemas.common import Confidence, Source, StrictModel

DESIGN_INTENT_VERSION = 1

STYLES: dict[str, str] = {
    "naturel": "Naturel",
    "chalet": "Chalet",
    "scandinave": "Scandinave",
    "industriel": "Industriel",
    "contemporain": "Contemporain",
}
"""Registre des styles (extensible : une entrée de plus, pas un `Literal` figé)."""

Mood = Literal["chaleureux", "lumineux", "epure", "cosy", "brut"]
MOOD_LABELS: dict[str, str] = {
    "chaleureux": "chaleureux",
    "lumineux": "lumineux",
    "epure": "épuré",
    "cosy": "cosy",
    "brut": "brut",
}
COLORS: dict[str, tuple[int, int, int]] = {
    "blanc casse": (240, 236, 226),
    "lin": (214, 200, 176),
    "sable": (205, 184, 150),
    "terracotta": (190, 105, 75),
    "ocre": (200, 150, 70),
    "vert sauge": (150, 168, 140),
    "vert foret": (55, 85, 60),
    "bleu nuit": (35, 50, 80),
    "bleu gris": (120, 140, 160),
    "gris anthracite": (60, 62, 66),
    "noir": (25, 25, 25),
    "chene clair": (196, 160, 115),
    "noyer": (110, 75, 50),
    "rose poudre": (220, 180, 175),
}
"""Couleurs nommées de la palette, traduites en RGB pour l'habillage."""
Surface = Literal["floor", "walls", "gables", "slopes", "timber", "furniture"]
Density = Literal["sobre", "normal", "riche"]
LightTime = Literal["matin", "midi", "heure_doree", "soir"]
Warmth = Literal["froide", "neutre", "chaude"]
Zone = Literal["couchage", "lecture", "bureau", "rangement", "detente", "cuisine", "repas"]
ZONE_LABELS: dict[str, str] = {
    "couchage": "couchage",
    "lecture": "lecture",
    "bureau": "bureau",
    "rangement": "rangement",
    "detente": "détente",
    "cuisine": "cuisine",
    "repas": "repas",
}
Aspect = Literal["4:3", "4:5"]


class SurfaceMaterials(StrictModel):
    """Matière voulue par surface ; vide = le style décide."""

    floor: str | None = None
    walls: str | None = None
    gables: str | None = None
    slopes: str | None = None
    timber: str | None = None
    furniture: str | None = None


class LightIntent(StrictModel):
    time: LightTime = "midi"
    warmth: Warmth = "neutre"


class FurnitureWish(StrictModel):
    category: str = Field(min_length=1, max_length=40)
    count: int = Field(default=1, ge=1, le=12)


class ViewsIntent(StrictModel):
    """Les trois vues automatiques (ensemble, zone, détail) et leur format."""

    overall: bool = True
    zone: bool = True
    detail: bool = True
    aspect: Aspect = "4:3"


class FieldSource(StrictModel):
    source: Source
    confidence: Confidence = 1.0


INTENT_FIELDS: tuple[str, ...] = (
    "room_type",
    "usage",
    "style",
    "mood",
    "palette",
    "materials",
    "decor_density",
    "light",
    "furniture",
    "avoid",
    "zones",
    "views",
)
"""Champs de l'intention qui portent une provenance."""
META_FIELDS: tuple[str, ...] = ("instruction", "translated_by")
"""Champs sans provenance, écrits tels quels : la consigne traduite et qui l'a traduite."""


class DesignIntent(StrictModel):
    """Ce que la pièce doit devenir (V1). Une par pièce, dans `Room.intent`."""

    version: Literal[1] = 1
    """`DESIGN_INTENT_VERSION`."""
    room_type: str | None = None
    """Vocabulaire fermé `ROOM_TYPES` (chambre, séjour, combles…)."""
    usage: str | None = Field(default=None, max_length=80)
    """Texte court : « chambre d'amis », « bureau et lecture »."""
    style: str = "naturel"
    mood: list[Mood] = Field(default_factory=list, max_length=2)
    palette: list[str] = Field(default_factory=list, max_length=5)
    """Vide (le style décide) ou 3 à 5 couleurs nommées de `COLORS`."""
    materials: SurfaceMaterials = Field(default_factory=SurfaceMaterials)
    decor_density: Density = "normal"
    light: LightIntent = Field(default_factory=LightIntent)
    furniture: list[FurnitureWish] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    """Catégories de meubles à ne pas proposer."""
    zones: list[Zone] = Field(default_factory=list)
    """Vide = le moteur décide d'après le type et la surface."""
    views: ViewsIntent = Field(default_factory=ViewsIntent)
    sources: dict[str, FieldSource] = Field(default_factory=dict)
    """Provenance par champ ; un champ absent est `inferred` (déduit)."""
    instruction: str | None = Field(default=None, max_length=300)
    """Dernière consigne libre traduite (point 5), relue dans la fiche « Ambiance »."""
    translated_by: str | None = Field(default=None, max_length=80)
    """Qui l'a traduite : « phrases » ou « llm:<modèle> »."""

    @field_validator("style")
    @classmethod
    def _known_style(cls, value: str) -> str:
        if value not in STYLES:
            msg = f"style inconnu : {value} (connus : {', '.join(STYLES)})"
            raise ValueError(msg)
        return value

    @field_validator("palette")
    @classmethod
    def _known_colors(cls, value: list[str]) -> list[str]:
        unknown = [c for c in value if c not in COLORS]
        if unknown:
            msg = f"couleurs inconnues : {', '.join(unknown)}"
            raise ValueError(msg)
        if value and not 3 <= len(value) <= 5:
            msg = "une palette compte 3 à 5 couleurs (ou aucune : le style décide)"
            raise ValueError(msg)
        return value

    @field_validator("mood", "zones")
    @classmethod
    def _no_duplicates(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            msg = "valeurs en double"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _sources_name_fields(self) -> DesignIntent:
        unknown = set(self.sources) - set(INTENT_FIELDS)
        if unknown:
            msg = f"provenance pour des champs inconnus : {', '.join(sorted(unknown))}"
            raise ValueError(msg)
        return self

    def source_of(self, name: str) -> Source:
        entry = self.sources.get(name)
        return entry.source if entry else Source.INFERRED

    def chosen_by_user(self, name: str) -> bool:
        return self.source_of(name) is Source.USER_DEFINED


def merge_intent(
    current: DesignIntent, fields: dict[str, object], *, by_user: bool
) -> tuple[DesignIntent, list[str]]:
    """Applique `fields` à l'intention. Un utilisateur écrit tout et marque les champs
    `user_defined` ; une étape automatique n'écrit que les champs que l'utilisateur n'a pas
    choisis (marqués `inferred`). Renvoie l'intention et les champs laissés tels quels."""
    unknown = set(fields) - set(INTENT_FIELDS) - set(META_FIELDS)
    if unknown:
        msg = f"champs d'intention inconnus : {', '.join(sorted(unknown))}"
        raise ValueError(msg)
    data = current.model_dump(mode="json")
    sources = dict(data.get("sources", {}))
    kept: list[str] = []
    for name, value in fields.items():
        if name in META_FIELDS:
            data[name] = value  # la consigne et son traducteur : pas de provenance
            continue
        if not by_user and current.chosen_by_user(name):
            kept.append(name)
            continue
        data[name] = value
        sources[name] = {
            "source": (Source.USER_DEFINED if by_user else Source.INFERRED).value,
            "confidence": 1.0 if by_user else 0.6,
        }
    data["sources"] = sources
    return DesignIntent.model_validate(data), kept
