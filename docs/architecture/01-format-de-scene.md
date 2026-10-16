# 01 — Format de scène interne

Statut : **proposition, en attente de validation**. Le code ci-dessous est un brouillon de
`openplan/schemas/*` ; il n'est pas encore écrit dans le paquet.

## 1. Principes

- **Indépendant de Blender et du moteur de rendu.** Le format décrit une intention géométrique
  paramétrique, jamais un maillage. Les maillages sont des artefacts dérivés.
- **Unités.** Toutes les longueurs en **mètres**, angles en **radians**, stockés en `float`.
  La conversion m/cm/mm est une affaire d'affichage.
- **Repères.** Plan 2D : repère local du niveau, X vers la droite, Y vers le haut du plan,
  origine libre. 3D : Z vers le haut (convention Blender), `z = level.elevation` au sol du niveau.
- **Topologie partagée.** Les murs référencent des **sommets partagés** (`vertex_id`). Déplacer
  un sommet met à jour tous les murs connectés, donc les pièces, sols, plafonds et ouvertures
  qui en dépendent. C'est ce qui rend le plan paramétrique et synchronise 2D et 3D.
- **Éléments dérivés.** Sols et plafonds sont dérivés des pièces par défaut (`derived=True`).
  L'utilisateur peut les détacher pour les éditer ; ils cessent alors de suivre la pièce.
- **Provenance obligatoire.** Chaque élément porte `source`, `confidence`, `provenance`
  (quelle image, quelle étape, quel modèle, quand) et `uncertainty`.
- **Priorité des sources.** `measured` > `user_defined` > `observed` > `estimated` >
  `inferred` > `generated`. Une étape automatique ne peut jamais écraser une valeur de
  priorité supérieure : elle propose une valeur dans `metadata["proposals"]` et l'historique
  garde la trace. Seul un `HistoryEntry` dont `actor == "user"` peut abaisser une source.
- **Versionné.** `schema_version` sémantique ; `openplan.scene.migrate` porte les projets
  anciens vers la version courante.
- **Historique.** Chaque modification est un patch JSON (RFC 6902) avec son inverse, ce qui
  donne annuler/rétablir sans stocker des instantanés complets.

## 2. Types communs (brouillon Pydantic v2)

```python
from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, ConfigDict

Meters = Annotated[float, Field(ge=0)]
Radians = float
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]

class Source(StrEnum):
    MEASURED = "measured"          # saisi ou confirmé par l'utilisateur
    OBSERVED = "observed"          # détecté directement dans une image
    ESTIMATED = "estimated"        # estimation algorithmique
    INFERRED = "inferred"          # déduit du contexte (ex. mur caché fermé par symétrie)
    GENERATED = "generated"        # créé par l'IA (aménagement)
    USER_DEFINED = "user_defined"  # ajouté manuellement sans mesure

SOURCE_PRIORITY = {s: i for i, s in enumerate([
    Source.GENERATED, Source.INFERRED, Source.ESTIMATED,
    Source.OBSERVED, Source.USER_DEFINED, Source.MEASURED])}

class Provenance(BaseModel):
    image_ids: list[UUID] = []
    step: str | None = None          # nom de l'étape de pipeline, ex. "detection.heuristic_box"
    model: str | None = None         # identifiant du registre de modèles, ex. "depth_anything_v2_small@1.0"
    run_id: UUID | None = None
    created_at: datetime

class Uncertainty(BaseModel):
    """Incertitude 1-sigma en mètres (ou radians pour les angles). None = inconnue, pas zéro."""
    sigma: float | None = None
    interval: tuple[float, float] | None = None
    notes: str | None = None

class Point2(BaseModel):
    x: float
    y: float

class Point3(BaseModel):
    x: float
    y: float
    z: float

class Transform3(BaseModel):
    """Position + rotation autour de Z + échelle uniforme. Suffisant pour du mobilier posé."""
    position: Point3
    yaw: Radians = 0.0
    scale: float = 1.0

class BBox3(BaseModel):
    """Boîte englobante alignée sur l'objet, en mètres : largeur (x), profondeur (y), hauteur (z)."""
    width: Meters
    depth: Meters
    height: Meters

class ElementBase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID = Field(default_factory=uuid4)
    type: str                       # discriminant, fixé par chaque sous-classe (Literal)
    source: Source
    confidence: Confidence
    locked: bool = False
    visible: bool = True
    provenance: Provenance
    uncertainty: Uncertainty = Uncertainty()
    name: str | None = None
    metadata: dict[str, object] = {}
```

`geometry` et `dimensions` du brief ne sont pas deux dictionnaires opaques : chaque sous-classe
les typifie (un mur a `start_vertex`, `end_vertex`, `thickness`, `height`). Une propriété
`dimensions` calculée peut exposer la vue uniforme demandée par l'UI.

## 3. Éléments architecturaux

```python
class Vertex(BaseModel):
    """Sommet 2D partagé entre murs, dans le repère du niveau."""
    id: UUID = Field(default_factory=uuid4)
    position: Point2
    source: Source
    confidence: Confidence
    locked: bool = False
    uncertainty: Uncertainty = Uncertainty()

class Wall(ElementBase):
    type: Literal["wall"] = "wall"
    start_vertex: UUID
    end_vertex: UUID
    thickness: Meters = 0.15
    height: Meters | None = None    # None = hauteur du niveau
    offset: Literal["center", "left", "right"] = "center"  # côté de l'épaisseur par rapport à l'axe
    exterior: bool | None = None    # None = inconnu
    # longueur = distance entre sommets : dérivée, jamais stockée

class OpeningKind(StrEnum):
    DOOR = "door"
    WINDOW = "window"
    PASSAGE = "passage"            # ouverture sans menuiserie

class Swing(BaseModel):
    """Sens d'ouverture d'une porte. side = charnières vue du start_vertex du mur hôte."""
    hinge: Literal["left", "right"]
    direction: Literal["inward", "outward"]  # relatif au côté "left" du mur (normale gauche)
    angle: Radians = 1.5708

class Opening(ElementBase):
    type: Literal["opening"] = "opening"
    kind: OpeningKind
    host_wall: UUID
    offset: Meters                  # distance le long du mur depuis start_vertex jusqu'au bord gauche
    width: Meters
    height: Meters
    sill_height: Meters = 0.0       # 0 pour une porte
    swing: Swing | None = None      # portes seulement

class Room(ElementBase):
    type: Literal["room"] = "room"
    boundary_walls: list[UUID]      # ordonnés, sens antihoraire
    room_type: str | None = None    # "bedroom", "attic", ...
    # area, perimeter : dérivés via openplan.geom

class Floor(ElementBase):
    type: Literal["floor"] = "floor"
    room: UUID
    derived: bool = True
    polygon: list[Point2] | None = None   # rempli si derived=False
    material: UUID | None = None

class Ceiling(ElementBase):
    type: Literal["ceiling"] = "ceiling"
    room: UUID
    derived: bool = True
    height: Meters | None = None
    polygon: list[Point2] | None = None
    material: UUID | None = None

class RoofPlane(ElementBase):
    """Pente : un plan défini par sa ligne d'égout (2D), sa hauteur à l'égout et sa pente.
    Réservé pour 0.4 ; le schéma le prévoit pour ne pas migrer le format plus tard."""
    type: Literal["roof_plane"] = "roof_plane"
    eave_start: Point2
    eave_end: Point2
    eave_height: Meters
    pitch: Radians                  # angle par rapport à l'horizontale
    rooms: list[UUID] = []          # pièces dont le plafond est coupé par ce plan

class RoofWindow(ElementBase):
    """Velux dans une pente (schéma 0.2.0, D1 du 4 octobre 2026). La pente est percée à
    sa place (cadre bois + vitrage) dans le bake ; le plan montre son emprise au sol."""
    type: Literal["roof_window"] = "roof_window"
    roof: UUID                      # la pente qui le porte (supprimée → velux supprimés)
    offset: Meters                  # bord gauche, depuis eave_start, le long de l'égout
    distance: Meters                # bord bas, depuis l'égout, le long de la pente
    width: float = 0.78             # le long de l'égout
    length: float = 0.98            # le long de la pente

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
    clearance: Meters = 0.30        # zone à garder libre devant (contrainte par défaut)

class Stairs(ElementBase):
    """Réservé pour 0.4 : emprise 2D + niveau d'arrivée."""
    type: Literal["stairs"] = "stairs"
    footprint: list[Point2]
    to_level: UUID | None = None

class Fixture(ElementBase):
    """Élément fixe générique (évier, cheminée, placard bâti, prise, etc.)."""
    type: Literal["fixture"] = "fixture"
    category: str
    transform: Transform3
    bbox: BBox3
    host_wall: UUID | None = None
    keep_clear: Meters = 0.0

class Furniture(ElementBase):
    type: Literal["furniture"] = "furniture"
    asset_id: str                   # référence au catalogue (doc catalogue, 0.3)
    category: str
    transform: Transform3
    bbox: BBox3                     # copie des dimensions de l'asset, éditable
    material_overrides: dict[str, UUID] = {}
    preserve: bool = False          # "à conserver" dans un brief d'aménagement
```

## 4. Matériaux, éclairages, caméras

```python
class Material(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    base_color: tuple[float, float, float] = (0.8, 0.8, 0.8)
    roughness: float = 0.6
    metallic: float = 0.0
    texture: str | None = None       # chemin relatif dans blender/materials ou catalogue
    license: Literal["open_source", "cc0", "cc_by", "commercial_restricted", "unknown"] = "cc0"

class Light(ElementBase):
    type: Literal["light"] = "light"
    kind: Literal["point", "sun", "area", "spot"]
    position: Point3
    power_w: float = 100.0
    color_temp_k: float = 4000.0

class Camera(ElementBase):
    """Caméra de rendu OU caméra estimée d'une photo (image_id renseigné)."""
    type: Literal["camera"] = "camera"
    position: Point3
    yaw: Radians
    pitch: Radians
    roll: Radians = 0.0
    focal_px: float | None = None    # focale en pixels si connue (EXIF ou estimée)
    image_id: UUID | None = None
```

## 5. Images, mesures, calibrations, contraintes, historique

```python
class ImageRef(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    path: str                        # relatif au dossier projet, jamais absolu
    sha256: str
    width_px: int
    height_px: int
    exif_stripped: bool
    focal_35mm: float | None = None  # lu dans l'EXIF avant suppression, si l'utilisateur l'autorise
    quality: dict[str, float] = {}   # sortie du contrôle qualité : blur, exposure, ...
    imported_at: datetime

class MeasurementKind(StrEnum):
    WALL_LENGTH = "wall_length"
    DOOR_WIDTH = "door_width"
    CEILING_HEIGHT = "ceiling_height"
    POINT_TO_POINT = "point_to_point"
    OBJECT_DIMENSION = "object_dimension"
    PLAN_DIMENSION = "plan_dimension"   # cote lue sur un plan photographié

class Measurement(BaseModel):
    """Une mesure connue, toujours source=measured. Elle référence soit un élément (mur, ouverture),
    soit deux points image, soit deux sommets du plan."""
    id: UUID = Field(default_factory=uuid4)
    kind: MeasurementKind
    value: Meters
    tolerance: Meters = 0.01
    element_id: UUID | None = None
    vertex_ids: tuple[UUID, UUID] | None = None
    image_points: tuple[UUID, Point2, Point2] | None = None  # (image_id, p1, p2) en pixels
    confirmed_by_user: bool = True
    created_at: datetime
    note: str | None = None

class Calibration(BaseModel):
    """Résultat d'une résolution d'échelle : quelles mesures ont été utilisées, résidus, décision."""
    id: UUID = Field(default_factory=uuid4)
    level_id: UUID
    measurement_ids: list[UUID]
    scale_factor: float              # appliqué au plan précédent (1.0 si déjà métrique)
    residuals_m: dict[UUID, float]   # par mesure : écart après optimisation
    rms_m: float
    inconsistent: list[UUID] = []    # mesures dont le résidu dépasse la tolérance
    created_at: datetime

class ConstraintKind(StrEnum):
    PARALLEL = "parallel"
    ORTHOGONAL = "orthogonal"
    FIXED_LENGTH = "fixed_length"
    FIXED_ANGLE = "fixed_angle"
    COLLINEAR = "collinear"
    KEEP_CLEAR = "keep_clear"        # zone libre devant un élément (porte, radiateur, fenêtre)
    MIN_PASSAGE = "min_passage"      # largeur de circulation minimale
    NO_OVERLAP = "no_overlap"
    MAX_HEIGHT_UNDER_SLOPE = "max_height_under_slope"

class Constraint(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: ConstraintKind
    element_ids: list[UUID]
    params: dict[str, float] = {}
    hard: bool = True                # dur = violation bloquante ; souple = pénalité de score
    source: Source = Source.USER_DEFINED
    enabled: bool = True

class HistoryEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    timestamp: datetime
    actor: str                       # "user" | "pipeline:<step>" | "ai:<model>"
    label: str                       # texte affichable : "Déplacer le sommet", "Calibrer"
    patch: list[dict]                # JSON Patch RFC 6902 appliqué au Project
    inverse_patch: list[dict]
    run_id: UUID | None = None
```

## 6. Niveaux et projet

```python
Element = Annotated[
    Wall | Opening | Room | Floor | Ceiling | RoofPlane | RoofWindow | Beam | Radiator | Stairs
    | Fixture | Furniture | Light | Camera,
    Field(discriminator="type"),
]

class Level(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str = "Rez-de-chaussée"
    elevation: float = 0.0           # Z du sol, mètres
    height: Meters = 2.50            # hauteur sous plafond par défaut
    height_source: Source = Source.ESTIMATED
    vertices: dict[UUID, Vertex] = {}
    elements: dict[UUID, Element] = {}
    background_image: UUID | None = None   # fond de photo pour l'éditeur 2D
    background_transform: dict[str, float] = {}  # échelle / translation / rotation du fond

class PrivacySettings(BaseModel):
    strip_exif: bool = True
    allow_remote_models: bool = False
    telemetry: bool = False

class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "0.2.0"   # 0.1.0 → 0.2.0 : velux, sorte de niveau (sans perte)
    id: UUID = Field(default_factory=uuid4)
    name: str
    created_at: datetime
    updated_at: datetime
    units_display: Literal["m", "cm", "mm"] = "m"
    privacy: PrivacySettings = PrivacySettings()
    images: dict[UUID, ImageRef] = {}
    levels: dict[UUID, Level] = {}
    measurements: dict[UUID, Measurement] = {}
    calibrations: list[Calibration] = []
    constraints: dict[UUID, Constraint] = {}
    materials: dict[UUID, Material] = {}
    history: list[HistoryEntry] = []
    history_cursor: int = 0          # position pour annuler/rétablir
```

## 7. Invariants vérifiés par `openplan.validation`

- Tout `start_vertex`/`end_vertex` référence un sommet existant du même niveau.
- `host_wall` existe ; `offset + width <= longueur du mur`.
- Les `boundary_walls` d'une pièce forment une boucle fermée (chaque sommet partagé par deux
  murs consécutifs) ; le polygone est simple (pas d'auto-intersection).
- `Floor.derived` implique `polygon is None`, et réciproquement.
- `confidence == 1.0` uniquement si `source in {measured, user_defined}`.
- Un élément `locked` n'est modifié par aucun patch dont l'acteur n'est pas `user`.

## 8. Exemple minimal (pièce rectangulaire 4 × 3 m, une porte, une fenêtre)

```json
{
  "schema_version": "0.1.0",
  "id": "2f1c…",
  "name": "Chambre",
  "created_at": "2026-09-23T10:00:00Z",
  "updated_at": "2026-09-23T10:05:00Z",
  "units_display": "cm",
  "privacy": {"strip_exif": true, "allow_remote_models": false, "telemetry": false},
  "images": {},
  "levels": {
    "L1": {
      "id": "L1", "name": "RDC", "elevation": 0.0, "height": 2.5, "height_source": "estimated",
      "vertices": {
        "A": {"id": "A", "position": {"x": 0, "y": 0}, "source": "user_defined", "confidence": 1.0},
        "B": {"id": "B", "position": {"x": 4, "y": 0}, "source": "user_defined", "confidence": 1.0},
        "C": {"id": "C", "position": {"x": 4, "y": 3}, "source": "estimated", "confidence": 0.6},
        "D": {"id": "D", "position": {"x": 0, "y": 3}, "source": "estimated", "confidence": 0.6}
      },
      "elements": {
        "W1": {"id": "W1", "type": "wall", "start_vertex": "A", "end_vertex": "B", "thickness": 0.15,
               "source": "measured", "confidence": 1.0, "provenance": {"created_at": "…"}},
        "W2": {"id": "W2", "type": "wall", "start_vertex": "B", "end_vertex": "C", "thickness": 0.15,
               "source": "estimated", "confidence": 0.6, "uncertainty": {"sigma": 0.15},
               "provenance": {"step": "detection.heuristic_box", "image_ids": ["img1"], "created_at": "…"}},
        "W3": {"id": "W3", "type": "wall", "start_vertex": "C", "end_vertex": "D", "thickness": 0.15,
               "source": "estimated", "confidence": 0.6, "provenance": {"created_at": "…"}},
        "W4": {"id": "W4", "type": "wall", "start_vertex": "D", "end_vertex": "A", "thickness": 0.15,
               "source": "estimated", "confidence": 0.6, "provenance": {"created_at": "…"}},
        "R1": {"id": "R1", "type": "room", "boundary_walls": ["W1", "W2", "W3", "W4"],
               "room_type": "bedroom", "source": "inferred", "confidence": 0.6, "provenance": {"created_at": "…"}},
        "O1": {"id": "O1", "type": "opening", "kind": "door", "host_wall": "W1", "offset": 0.5,
               "width": 0.83, "height": 2.04, "swing": {"hinge": "left", "direction": "inward"},
               "source": "user_defined", "confidence": 1.0, "provenance": {"created_at": "…"}},
        "O2": {"id": "O2", "type": "opening", "kind": "window", "host_wall": "W3", "offset": 1.2,
               "width": 1.2, "height": 1.25, "sill_height": 0.9,
               "source": "observed", "confidence": 0.8, "provenance": {"image_ids": ["img1"], "created_at": "…"}}
      }
    }
  },
  "measurements": {
    "M1": {"id": "M1", "kind": "wall_length", "value": 4.0, "element_id": "W1",
           "confirmed_by_user": true, "created_at": "…"}
  },
  "calibrations": [], "constraints": {}, "materials": {}, "history": [], "history_cursor": 0
}
```

(Identifiants abrégés pour la lisibilité ; en réalité des UUID v4.)

## 9. Ce que le format ne couvre pas encore

- Murs courbes et murs d'épaisseur variable (hors périmètre avant 0.4).
- Pièces à plusieurs niveaux de sol (mezzanine) : `Level` unique par altitude suffit pour 0.4.
- Textures procédurales : `Material.texture` est un chemin, pas un graphe de nœuds.
- Groupes / hiérarchies d'objets : pas nécessaires tant que le mobilier est posé au sol.
