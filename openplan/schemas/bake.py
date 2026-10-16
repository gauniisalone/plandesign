"""« Bake » 3D : la scène paramétrique réduite à des prismes droits et des polyèdres.

C'est le contrat entre le noyau Python et les moteurs de rendu (trimesh, Blender) :
chaque prisme est un polygone 2D extrudé entre z0 et z1 ; un polyèdre (sommets + faces
triangulaires) sert aux formes qu'une extrusion ne décrit pas : panneaux de pente inclinés,
murs découpés sous une pente. Chaque solide est nommé par l'élément qui le produit. Le
script Blender ne lit que ce format, jamais le projet complet.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from openplan.schemas.common import Point2, Point3, Source, StrictModel

PrismKind = Literal["wall", "floor", "ceiling", "furniture", "fixture", "stairs", "joinery"]
"""`joinery` : menuiserie d'une ouverture (chambranle, vantail, vitrage, rail ; E5b)."""

Finish = Literal[
    "plaster",
    "wood_floor",
    "wood_dark",
    "wood_boards",
    "stone",
    "stone_rubble",
    "concrete",
    "glass",
    "timber",
    "metal",
    "limewash",
    "concrete_floor",
    "oak_dark",
    "painted_wood",
    "linen",
]
"""Matière d'aspect, procédurale côté rendu (aucune texture externe, donc aucune licence à
vérifier). Choisie par `metadata["finish"]` de l'élément, sinon par défaut selon le type."""

BakeStyle = Literal["provenance", "realistic"]
"""Ce que signifie `color` : couleur de provenance (source de la donnée) ou teinte de matière."""


class Prism(StrictModel):
    element_id: UUID
    part: int = 0
    """Index du morceau quand un élément se découpe (mur autour d'une ouverture)."""
    kind: PrismKind
    source: Source
    exterior: list[Point2] = Field(min_length=3)
    """Contour antihoraire, sans point de fermeture."""
    holes: list[list[Point2]] = Field(default_factory=list)
    z0: float
    z1: float
    color: tuple[int, int, int]
    """RGB 0–255 selon `Bake.style` (aide visuelle, pas un matériau complet)."""
    finish: Finish = "plaster"
    model: str | None = None
    """Meuble : modèle 3D du catalogue (relatif à `assets/furniture/`), mis à l'échelle de
    la boîte (`exterior`, `z0`, `z1`) et tourné de son lacet par le moteur de rendu ; la
    boîte reste la référence des règles et le repli si le modèle manque."""
    yaw: float = 0.0
    """Lacet du meuble (face avant = +y local), utile au placement du modèle."""
    tints: dict[str, tuple[float, float, float]] | None = None
    """Teintes (linéaires 0–1) du tissu (`carpet`) et du métal (`metal`) du modèle selon
    l'ambiance (V1) ; None = celles par défaut."""
    woods: dict[str, Finish] | None = None
    """Bois des meubles selon l'ambiance (V1) : finition des matières `wood` et `woodDark`
    du modèle (chêne clair, noyer, bois peint blanc, chêne foncé)."""

    @property
    def name(self) -> str:
        return str(self.element_id) if self.part == 0 else f"{self.element_id}#{self.part}"


PolyhedronKind = Literal["wall", "roof", "slab", "roof_window", "timber", "reveal"]
"""`reveal` : ébrasement d'un velux (H2), caisson biais sous la toiture."""


class Polyhedron(StrictModel):
    """Solide fermé décrit par ses sommets et ses faces triangulaires (indices, sens direct
    vu de l'extérieur)."""

    element_id: UUID
    part: int = 0
    kind: PolyhedronKind
    source: Source
    vertices: list[Point3] = Field(min_length=4)
    faces: list[tuple[int, int, int]] = Field(min_length=4)
    color: tuple[int, int, int]
    finish: Finish = "plaster"

    @property
    def name(self) -> str:
        return str(self.element_id) if self.part == 0 else f"{self.element_id}#{self.part}"


class BakedOpening(StrictModel):
    """Une porte ou une fenêtre repérée pour l'habillage (rideaux, plantes) ; le trou est dans
    les prismes du mur et la menuiserie dans les prismes `joinery` (E5b). Repère : `center`
    au milieu de la baie sur l'axe du mur, à la hauteur d'appui ; `direction` le long du mur ;
    `normal` à gauche."""

    element_id: UUID
    kind: Literal["door", "window"]
    source: Source
    center: Point3
    direction: tuple[float, float]
    normal: tuple[float, float]
    width: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    wall_thickness: float = Field(gt=0.0)


class Bake(StrictModel):
    level_id: UUID
    level_name: str
    project_name: str
    style: BakeStyle = "provenance"
    prisms: list[Prism] = Field(default_factory=list)
    polyhedra: list[Polyhedron] = Field(default_factory=list)
    """Murs sous pente (découpés) et panneaux de toit ; vide sans `RoofPlane`."""
    openings: list[BakedOpening] = Field(default_factory=list)
    """Habillage de rendu des baies (Blender, après l'export GLB) ; sans effet sur les volumes."""
    decor: list[Decor] = Field(default_factory=list)
    """Habillage (style « realistic » seulement)."""
    palette: DecorPalette | None = None
    """Ambiance de l'habillage (tapis notamment) ; style « realistic » seulement."""
    bounds_min: Point3 | None = None
    bounds_max: Point3 | None = None


RGB = tuple[float, float, float]
"""Couleur linéaire 0–1 (Blender)."""
DecorStyle = Literal["naturel", "chalet", "scandinave", "industriel", "contemporain"]
"""Ambiance d'une pièce (`Room.metadata["ambiance"]`) : couleurs et objets de l'habillage."""
PendantFinish = Literal["rattan", "metal_black", "white", "brass"]


class DecorPalette(StrictModel):
    """Couleurs et dosage de l'habillage pour une ambiance (C3)."""

    style: DecorStyle
    textile: RGB
    accent: RGB
    rug: RGB
    rug_stripe: RGB
    """Tapis : fond et rayures, contrastés pour survivre à la finition IA."""
    prints: list[RGB] = Field(min_length=2)
    curtain: RGB
    pendant: PendantFinish
    floor_plants: int = Field(ge=0, le=3)
    books: bool


class Decor(StrictModel):
    """Habillage du rendu (D4, C3), hors de la scène et des règles : lampe allumée sur un
    chevet, plantes, cadre au mur, coussins et plaid, livres, suspension, rideaux. Posé en
    (x, y, z) = centre bas ; une suspension pend de `z + height` (plafond) à `z`."""

    kind: Literal[
        "table_lamp",
        "plant",
        "frame",
        "pillow",
        "throw",
        "books",
        "floor_plant",
        "pendant",
        "curtain",
        "duvet",
        "jute_rug",
    ]
    model: str | None = None
    x: float
    y: float
    z: float
    yaw: float = 0.0
    width: float = Field(gt=0.0)
    depth: float = Field(gt=0.0)
    height: float = Field(gt=0.0)
    light_w: float = Field(default=0.0, ge=0.0)
    color: RGB | None = None
    accent: RGB | None = None
    pattern: Literal["blocks", "arch", "bands"] | None = None
    """Tirage d'un cadre : aplats abstraits construits par Blender (aucune image tierce)."""
    material: PendantFinish | None = None
    drape: float | None = Field(default=None, ge=0.0)
    """Textile (H3) : demi-largeur du matelas sous un plaid ou une couette ; au-delà, le tissu
    retombe sur les côtés du lit au lieu de rester une plaque rigide."""
    host: UUID | None = None
    """Meuble porteur (P4) : linge et coussins → leur lit, lampe → son chevet, livres → leur
    étagère. Un clic sur le décor dans un rendu désigne ce meuble ; None : décor sans porteur
    (plante, cadre, tapis, rideau, suspension)."""


class RenderCamera(StrictModel):
    """Caméra imposée au rendu Blender (fixtures synthétiques à pose connue).

    Mêmes conventions que `Camera` : position en mètres, lacet depuis l'axe X (antihoraire
    vu du dessus), tangage positif vers le haut, focale en pixels pour la largeur donnée.
    """

    position: Point3
    yaw: float
    pitch: float
    focal_px: float = Field(gt=0.0)
    width_px: int = Field(gt=0)
    height_px: int = Field(gt=0)
    checker_floor: bool = True
    """Sol carrelé à deux tons : les lignes d'un intérieur réel, absentes d'un sol uni."""
    roll: float = 0.0
