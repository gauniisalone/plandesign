"""Éléments architecturaux, mobilier, éclairages et caméras d'un niveau.

Les murs référencent des sommets partagés (`Vertex`) : déplacer un sommet met à jour
tous les murs connectés, donc les pièces, sols, plafonds et ouvertures qui en dépendent.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from openplan.schemas.common import (
    BBox3,
    Confidence,
    ElementBase,
    Meters,
    Point2,
    Point3,
    Radians,
    Source,
    StrictModel,
    Transform3,
    Uncertainty,
)
from openplan.schemas.intent import DesignIntent


class Vertex(StrictModel):
    """Sommet 2D partagé entre murs, dans le repère du niveau."""

    id: UUID = Field(default_factory=uuid4)
    position: Point2
    source: Source
    confidence: Confidence
    locked: bool = False
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)


class Wall(ElementBase):
    type: Literal["wall"] = "wall"
    start_vertex: UUID
    end_vertex: UUID
    thickness: Meters = 0.15
    height: Meters | None = None
    """None = hauteur du niveau."""
    offset: Literal["center", "left", "right"] = "center"
    """Côté de l'épaisseur par rapport à l'axe start→end ("left" = normale gauche)."""
    exterior: bool | None = None
    """None = inconnu."""

    @model_validator(mode="after")
    def _distinct_vertices(self) -> Wall:
        if self.start_vertex == self.end_vertex:
            msg = "un mur doit relier deux sommets distincts"
            raise ValueError(msg)
        return self


class OpeningKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    PASSAGE = "passage"
    """Ouverture sans menuiserie."""
    DOUBLE_DOOR = "double_door"
    """Deux vantaux battants, égaux ou inégaux (`split`), schéma 0.4.0."""
    SLIDING_DOOR = "sliding_door"
    """Coulissante en applique : rail visible, le vantail ouvert court le long du mur."""
    POCKET_DOOR = "pocket_door"
    """À galandage : le vantail s'efface dans l'épaisseur de la cloison."""
    FOLDING_DOOR = "folding_door"
    """Pliante (accordéon) : les panneaux se replient côté gonds."""
    FRENCH_WINDOW = "french_window"
    """Porte-fenêtre : un ou deux vantaux vitrés battants (`leaves`)."""
    SLIDING_WINDOW = "sliding_window"
    """Baie vitrée coulissante : deux vantaux, l'un glisse devant l'autre."""


DOOR_KINDS = frozenset(
    {
        OpeningKind.DOOR,
        OpeningKind.DOUBLE_DOOR,
        OpeningKind.SLIDING_DOOR,
        OpeningKind.POCKET_DOOR,
        OpeningKind.FOLDING_DOOR,
        OpeningKind.FRENCH_WINDOW,
    }
)
"""Ouvertures par où l'on passe d'une pièce à l'autre."""
GLAZED_KINDS = frozenset(
    {OpeningKind.WINDOW, OpeningKind.FRENCH_WINDOW, OpeningKind.SLIDING_WINDOW}
)
"""Ouvertures vitrées : le jour entre par là."""
OPENING_LABELS: dict[OpeningKind, str] = {
    OpeningKind.DOOR: "porte",
    OpeningKind.WINDOW: "fenêtre",
    OpeningKind.PASSAGE: "passage",
    OpeningKind.DOUBLE_DOOR: "double porte",
    OpeningKind.SLIDING_DOOR: "porte coulissante",
    OpeningKind.POCKET_DOOR: "porte à galandage",
    OpeningKind.FOLDING_DOOR: "porte pliante",
    OpeningKind.FRENCH_WINDOW: "porte-fenêtre",
    OpeningKind.SLIDING_WINDOW: "baie vitrée coulissante",
}


class Swing(StrictModel):
    """Sens d'ouverture, vu depuis le start_vertex du mur hôte. Battants : côté des gonds (du
    vantail principal pour une paire) et côté d'ouverture. Coulissants : `hinge` est le côté
    où le vantail s'efface, `direction` la face du mur où court le rail (applique)."""

    hinge: Literal["left", "right"]
    direction: Literal["inward", "outward"]
    """Relatif au côté "left" du mur (normale gauche)."""
    angle: Radians = 1.5708


class Opening(ElementBase):
    type: Literal["opening"] = "opening"
    kind: OpeningKind
    host_wall: UUID
    offset: Meters
    """Distance le long du mur, depuis start_vertex jusqu'au bord de l'ouverture."""
    width: Meters = Field(gt=0.0)
    height: Meters = Field(gt=0.0)
    sill_height: Meters = 0.0
    """0 pour une porte."""
    swing: Swing | None = None
    """Toute menuiserie qui s'ouvre (pas une fenêtre simple ni un passage)."""
    leaves: int = Field(default=1, ge=1, le=2)
    """Vantaux d'une porte-fenêtre (1 ou 2) ; une double porte et une baie coulissante en ont
    toujours deux."""
    split: float = Field(default=0.5, ge=0.25, le=0.75)
    """Part du vantail principal (côté `swing.hinge`) d'une paire : 0,5 égaux, 1/3 ou 2/3."""

    @model_validator(mode="after")
    def _swing_only_on_doors(self) -> Opening:
        if self.swing is not None and self.kind in (OpeningKind.WINDOW, OpeningKind.PASSAGE):
            msg = "swing n'a de sens que pour une porte (ou une menuiserie qui s'ouvre)"
            raise ValueError(msg)
        return self


class Room(ElementBase):
    type: Literal["room"] = "room"
    boundary_walls: list[UUID] = Field(min_length=3)
    """Murs ordonnés, sens antihoraire, formant une boucle fermée."""
    room_type: str | None = None
    """Ex. "bedroom", "attic", "living_room"."""
    intent: DesignIntent | None = None
    """Intention de design (V1, schéma 0.5.0) ; absente = déduite à la demande."""


class Floor(ElementBase):
    type: Literal["floor"] = "floor"
    room: UUID
    derived: bool = True
    polygon: list[Point2] | None = None
    """Renseigné uniquement si derived est False."""
    material: UUID | None = None


class FloorZone(ElementBase):
    """KAN-92 (schéma 0.19.0) : zone de sol surélevée (estrade, marche, plancher technique),
    quadrilatère posé dans une pièce et élevé de 0 à 1,20 m. Ses sommets sont les siens (le
    graphe des murs, la détection et la surface des pièces n'en dépendent pas) ; un meuble dont
    le centre est dessus monte avec elle, par calcul (`openplan.geom.floor_zones.lift_at`)."""

    type: Literal["floor_zone"] = "floor_zone"
    polygon: list[Point2] = Field(min_length=4, max_length=4)
    """Les 4 coins, dans l'ordre du tracé (la forme est vérifiée par la commande)."""
    elevation: float = Field(default=0.0, ge=0.0, le=1.20)
    """Hauteur du dessus de la zone au-dessus du sol du niveau, en mètres."""
    room: UUID | None = None
    """La pièce qui la contient (renseignée par la commande)."""


class Ceiling(ElementBase):
    type: Literal["ceiling"] = "ceiling"
    room: UUID
    derived: bool = True
    height: Meters | None = None
    polygon: list[Point2] | None = None
    material: UUID | None = None


class RoofPlane(ElementBase):
    """Pente : plan défini par sa ligne d'égout, sa hauteur à l'égout et son inclinaison.

    Réservé à la version 0.4 ; présent pour ne pas migrer le format plus tard.
    """

    type: Literal["roof_plane"] = "roof_plane"
    eave_start: Point2
    eave_end: Point2
    eave_height: Meters
    pitch: Radians
    rooms: list[UUID] = Field(default_factory=list)


class RoofWindow(ElementBase):
    """Fenêtre de toit (velux) dans une pente (D1, schéma 0.2.0) : châssis de `width` le long de
    l'égout et `length` le long de la pente, son bord bas à `distance` de l'égout (mesurée le long
    de la pente) et son bord gauche à `offset` du début de l'égout (`eave_start`)."""

    type: Literal["roof_window"] = "roof_window"
    roof: UUID
    offset: Meters
    distance: Meters
    width: float = Field(default=0.78, gt=0.0, le=3.0)
    length: float = Field(default=0.98, gt=0.0, le=3.0)


class Beam(ElementBase):
    type: Literal["beam"] = "beam"
    start: Point3
    end: Point3
    section_width: Meters
    section_height: Meters


class Radiator(ElementBase):
    type: Literal["radiator"] = "radiator"
    host_wall: UUID
    offset: Meters
    width: Meters
    height: Meters
    depth: Meters
    bottom_height: Meters
    clearance: Meters = 0.30
    """Zone à garder libre devant (contrainte par défaut)."""


StairKind = Literal["straight", "quarter_turn", "half_turn", "spiral"]
StairTurn = Literal["left", "right"]


class Stairs(ElementBase):
    """Cage d'escalier paramétrique (S1, schéma 0.3.0) : trémie dans le plancher de ce niveau,
    escalier qui en descend de `rise` mètres, garde-corps sur les côtés ouverts. Droit, quart
    tournant (palier carré), demi-tournant (deux volées parallèles) ou hélicoïdal ; marches
    calculées par la règle de Blondel (`openplan.geom.stairs`). `position` est le centre de
    l'encombrement en plan (le centre du cercle pour un hélicoïdal), `yaw` la direction de
    montée de la première volée, `turn` le côté vers lequel il tourne."""

    type: Literal["stairs"] = "stairs"
    kind: StairKind = "straight"
    position: Point2
    yaw: Radians = 0.0
    width: float = Field(default=0.90, ge=0.70, le=1.20)
    """Largeur d'emmarchement."""
    rise: float = Field(default=2.70, gt=0.5, le=6.0)
    """Hauteur à franchir : du plancher du niveau d'en dessous à celui-ci."""
    turn: StairTurn = "left"
    railing: bool = True
    to_level: UUID | None = None


class Fixture(ElementBase):
    """Élément fixe générique : évier, cheminée, placard bâti, prise..."""

    type: Literal["fixture"] = "fixture"
    category: str
    transform: Transform3
    bbox: BBox3
    host_wall: UUID | None = None
    keep_clear: Meters = 0.0


class Furniture(ElementBase):
    type: Literal["furniture"] = "furniture"
    asset_id: str
    """Référence au catalogue (version 0.3)."""
    category: str
    transform: Transform3
    bbox: BBox3
    """Copie des dimensions de l'asset, éditable."""
    material_overrides: dict[str, UUID] = Field(default_factory=dict)
    preserve: bool = False
    """« À conserver » dans un brief d'aménagement."""


class Light(ElementBase):
    type: Literal["light"] = "light"
    kind: Literal["point", "sun", "area", "spot"]
    position: Point3
    power_w: float = Field(default=100.0, ge=0.0)
    color_temp_k: float = Field(default=4000.0, gt=0.0)


class Camera(ElementBase):
    """Caméra de rendu, ou caméra estimée d'une photo si image_id est renseigné."""

    type: Literal["camera"] = "camera"
    position: Point3
    yaw: Radians
    pitch: Radians
    roll: Radians = 0.0
    focal_px: float | None = Field(default=None, gt=0.0)
    image_id: UUID | None = None


Element = Annotated[
    Wall
    | Opening
    | Room
    | Floor
    | FloorZone
    | Ceiling
    | RoofPlane
    | RoofWindow
    | Beam
    | Radiator
    | Stairs
    | Fixture
    | Furniture
    | Light
    | Camera,
    Field(discriminator="type"),
]
"""Union discriminée par `type` de tous les éléments d'un niveau."""

ELEMENT_TYPES: tuple[type[ElementBase], ...] = (
    Wall,
    Opening,
    Room,
    Floor,
    FloorZone,
    Ceiling,
    RoofPlane,
    RoofWindow,
    Beam,
    Radiator,
    Stairs,
    Fixture,
    Furniture,
    Light,
    Camera,
)
