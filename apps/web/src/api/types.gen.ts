/* Généré par scripts/gen-types.mjs depuis les JSON Schema Pydantic. Ne pas éditer. */
/* eslint-disable */

/**
 * Origine d'une donnée. L'ordre de priorité est donné par SOURCE_PRIORITY.
 */

export type Source =
  | "measured"
  | "observed"
  | "estimated"
  | "inferred"
  | "generated"
  | "user_defined";

export interface Bake {
  bounds_max: Point3 | null;
  bounds_min: Point3 | null;
  decor: Decor[];
  level_id: string;
  level_name: string;
  openings: BakedOpening[];
  palette: DecorPalette | null;
  polyhedra: Polyhedron[];
  prisms: Prism[];
  project_name: string;
  style: "provenance" | "realistic";
}

export interface Point3 {
  x: number;
  y: number;
  z: number;
}
/**
 * Habillage du rendu (D4, C3), hors de la scène et des règles : lampe allumée sur un
 * chevet, plantes, cadre au mur, coussins et plaid, livres, suspension, rideaux. Posé en
 * (x, y, z) = centre bas ; une suspension pend de `z + height` (plafond) à `z`.
 */

export interface Decor {
  accent: [unknown, unknown, unknown] | null;
  color: [unknown, unknown, unknown] | null;
  depth: number;
  drape: number | null;
  height: number;
  host: string | null;
  kind:
    | "table_lamp"
    | "plant"
    | "frame"
    | "pillow"
    | "throw"
    | "books"
    | "floor_plant"
    | "pendant"
    | "curtain"
    | "duvet"
    | "jute_rug";
  light_w: number;
  material: ("rattan" | "metal_black" | "white" | "brass") | null;
  model: string | null;
  pattern: ("blocks" | "arch" | "bands") | null;
  width: number;
  x: number;
  y: number;
  yaw: number;
  z: number;
}
/**
 * Une porte ou une fenêtre repérée pour l'habillage (rideaux, plantes) ; le trou est dans
 * les prismes du mur et la menuiserie dans les prismes `joinery` (E5b). Repère : `center`
 * au milieu de la baie sur l'axe du mur, à la hauteur d'appui ; `direction` le long du mur ;
 * `normal` à gauche.
 */

export interface BakedOpening {
  center: Point3;
  /**
   * @minItems 2
   * @maxItems 2
   */
  direction: [unknown, unknown];
  element_id: string;
  height: number;
  kind: "door" | "window";
  /**
   * @minItems 2
   * @maxItems 2
   */
  normal: [unknown, unknown];
  source: Source;
  wall_thickness: number;
  width: number;
}
/**
 * Couleurs et dosage de l'habillage pour une ambiance (C3).
 */

export interface DecorPalette {
  /**
   * @minItems 3
   * @maxItems 3
   */
  accent: [unknown, unknown, unknown];
  books: boolean;
  /**
   * @minItems 3
   * @maxItems 3
   */
  curtain: [unknown, unknown, unknown];
  floor_plants: number;
  pendant: "rattan" | "metal_black" | "white" | "brass";
  /**
   * @minItems 2
   */
  prints: [
    [unknown, unknown, unknown],
    [unknown, unknown, unknown],
    ...[unknown, unknown, unknown][],
  ];
  /**
   * @minItems 3
   * @maxItems 3
   */
  rug: [unknown, unknown, unknown];
  /**
   * @minItems 3
   * @maxItems 3
   */
  rug_stripe: [unknown, unknown, unknown];
  style: "naturel" | "chalet" | "scandinave" | "industriel" | "contemporain";
  /**
   * @minItems 3
   * @maxItems 3
   */
  textile: [unknown, unknown, unknown];
}
/**
 * Solide fermé décrit par ses sommets et ses faces triangulaires (indices, sens direct
 * vu de l'extérieur).
 */

export interface Polyhedron {
  /**
   * @minItems 3
   * @maxItems 3
   */
  color: [unknown, unknown, unknown];
  element_id: string;
  /**
   * @minItems 4
   */
  faces: [
    [unknown, unknown, unknown],
    [unknown, unknown, unknown],
    [unknown, unknown, unknown],
    [unknown, unknown, unknown],
    ...[unknown, unknown, unknown][],
  ];
  finish:
    | "plaster"
    | "wood_floor"
    | "wood_dark"
    | "wood_boards"
    | "stone"
    | "stone_rubble"
    | "concrete"
    | "glass"
    | "timber"
    | "metal"
    | "limewash"
    | "concrete_floor"
    | "oak_dark"
    | "painted_wood"
    | "linen";
  kind: "wall" | "roof" | "slab" | "roof_window" | "timber" | "reveal";
  part: number;
  source: Source;
  /**
   * @minItems 4
   */
  vertices: [Point3, Point3, Point3, Point3, ...Point3[]];
}

export interface Prism {
  /**
   * @minItems 3
   * @maxItems 3
   */
  color: [unknown, unknown, unknown];
  element_id: string;
  /**
   * @minItems 3
   */
  exterior: [Point2, Point2, Point2, ...Point2[]];
  finish:
    | "plaster"
    | "wood_floor"
    | "wood_dark"
    | "wood_boards"
    | "stone"
    | "stone_rubble"
    | "concrete"
    | "glass"
    | "timber"
    | "metal"
    | "limewash"
    | "concrete_floor"
    | "oak_dark"
    | "painted_wood"
    | "linen";
  holes: Point2[][];
  kind:
    | "wall"
    | "floor"
    | "ceiling"
    | "furniture"
    | "fixture"
    | "stairs"
    | "joinery";
  model: string | null;
  part: number;
  source: Source;
  tints: {
    /**
     * @minItems 3
     * @maxItems 3
     */
    [k: string]: [unknown, unknown, unknown];
  } | null;
  woods: {
    [k: string]:
      | "plaster"
      | "wood_floor"
      | "wood_dark"
      | "wood_boards"
      | "stone"
      | "stone_rubble"
      | "concrete"
      | "glass"
      | "timber"
      | "metal"
      | "limewash"
      | "concrete_floor"
      | "oak_dark"
      | "painted_wood"
      | "linen";
  } | null;
  yaw: number;
  z0: number;
  z1: number;
}

export interface Point2 {
  x: number;
  y: number;
}

export interface DerivedLevel {
  bounds: Bounds | null;
  level_id: string;
  loops: string[][];
  roofs: {
    [k: string]: DerivedRoof;
  };
  rooms: {
    [k: string]: DerivedRoom;
  };
  stairs: {
    [k: string]: DerivedStairs;
  };
  walls: {
    [k: string]: DerivedWall;
  };
}

export interface Bounds {
  max: Point2;
  min: Point2;
}

export interface DerivedRoof {
  /**
   * @minItems 2
   * @maxItems 2
   */
  eave: [Point2, Point2];
  eave_height: number;
  inward: Point2;
  pitch: number;
  pitch_deg: number;
  run_m: number;
  usable_offset_m: number | null;
}

export interface DerivedRoom {
  area_m2: number;
  centroid: Point2;
  perimeter_m: number;
  polygon: Point2[];
  usable_area_m2: number | null;
}
/**
 * Escalier vu en plan (S1) : symbole normalisé (contour de trémie, marches, ligne de
 * foulée et flèche de montée, ligne de coupe, garde-corps) et grandeurs de Blondel.
 */

export interface DerivedStairs {
  arrival: Point2[];
  arrow: Point2[];
  blondel_m: number;
  cut: Point2[];
  going_m: number;
  headroom_m: number | null;
  outline: Point2[];
  railings: Point2[][];
  riser_m: number;
  risers: number;
  treads: Point2[][];
  walk: Point2[];
}

export interface DerivedWall {
  length_m: number;
  midpoint: Point2;
  normal: Point2;
  polygon: Point2[];
}

export type ConstraintKind =
  | "parallel"
  | "orthogonal"
  | "fixed_length"
  | "fixed_angle"
  | "collinear"
  | "keep_clear"
  | "min_passage"
  | "no_overlap"
  | "max_height_under_slope";
/**
 * Origine d'une donnée. L'ordre de priorité est donné par SOURCE_PRIORITY.
 */

export type JsonValue = unknown;
/**
 * Origine d'une donnée. L'ordre de priorité est donné par SOURCE_PRIORITY.
 */

export type Source1 =
  | "measured"
  | "observed"
  | "estimated"
  | "inferred"
  | "generated"
  | "user_defined";

export type OpeningKind =
  | "door"
  | "window"
  | "passage"
  | "double_door"
  | "sliding_door"
  | "pocket_door"
  | "folding_door"
  | "french_window"
  | "sliding_window";
/**
 * Origine d'une donnée. L'ordre de priorité est donné par SOURCE_PRIORITY.
 */

export type Source2 =
  | "measured"
  | "observed"
  | "estimated"
  | "inferred"
  | "generated"
  | "user_defined";

export type MeasurementKind =
  | "wall_length"
  | "door_width"
  | "ceiling_height"
  | "point_to_point"
  | "object_dimension"
  | "plan_dimension";

export interface Project {
  calibrations: Calibration[];
  constraints: {
    [k: string]: Constraint;
  };
  created_at: string;
  history: HistoryEntry[];
  history_cursor: number;
  id: string;
  images: {
    [k: string]: ImageRef;
  };
  levels: {
    [k: string]: Level;
  };
  materials: {
    [k: string]: Material;
  };
  measurements: {
    [k: string]: Measurement;
  };
  name: string;
  name_auto: boolean | null;
  pending_answers: GuidedAnswers | null;
  pending_instruction: string | null;
  privacy: PrivacySettings;
  render: RenderSettings;
  schema_version: string;
  units_display: "m" | "cm" | "mm";
  updated_at: string;
}
/**
 * Résultat d'une résolution d'échelle : mesures utilisées, résidus, incohérences.
 */

export interface Calibration {
  created_at: string;
  id: string;
  inconsistent: string[];
  level_id: string;
  /**
   * @minItems 1
   */
  measurement_ids: [string, ...string[]];
  residuals_m: {
    [k: string]: number;
  };
  rms_m: number;
  scale_factor: number;
}

export interface Constraint {
  /**
   * @minItems 1
   */
  element_ids: [string, ...string[]];
  enabled: boolean;
  hard: boolean;
  id: string;
  kind: ConstraintKind;
  params: {
    [k: string]: number;
  };
  source: Source;
}

export interface HistoryEntry {
  actor: string;
  id: string;
  inverse_patch: {
    [k: string]: JsonValue;
  }[];
  label: string;
  patch: {
    [k: string]: JsonValue;
  }[];
  run_id: string | null;
  timestamp: string;
}

export interface ImageRef {
  exif_stripped: boolean;
  focal_35mm: number | null;
  height_px: number;
  id: string;
  imported_at: string;
  path: string;
  quality: {
    [k: string]: number;
  };
  /**
   * @maxItems 1
   */
  reference_segments: [] | [ReferenceSegment];
  sha256: string;
  width_px: number;
}
/**
 * KAN-66 : mesure de référence posée sur une photo, deux points et une longueur connue
 * (« une porte de 2,04 m ») ; elle fixe l'échelle du plan estimé depuis cette photo.
 */

export interface ReferenceSegment {
  author: string;
  created_at: string;
  id: string;
  label: string | null;
  length_m: number;
  /**
   * @minItems 2
   * @maxItems 2
   */
  p1: [unknown, unknown];
  /**
   * @minItems 2
   * @maxItems 2
   */
  p2: [unknown, unknown];
}

export interface Level {
  background_image: string | null;
  background_transform: {
    [k: string]: number;
  };
  elements: {
    [k: string]:
      | Wall
      | Opening
      | Room
      | Floor
      | Ceiling
      | RoofPlane
      | RoofWindow
      | Beam
      | Radiator
      | Stairs
      | Fixture
      | Furniture
      | Light
      | Camera;
  };
  elevation: number;
  height: number;
  height_source: Source2;
  id: string;
  kind: ("ground" | "upper" | "attic" | "basement") | null;
  name: string;
  north_deg: number;
  scale_basis: ScaleBasis | null;
  variant_of: string | null;
  vertices: {
    [k: string]: Vertex;
  };
}

export interface Wall {
  confidence: number;
  end_vertex: string;
  exterior: boolean | null;
  height: number | null;
  id: string;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  offset: "center" | "left" | "right";
  provenance: Provenance;
  source: Source1;
  start_vertex: string;
  thickness: number;
  type: "wall";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * D'où vient une donnée : images, étape de pipeline, modèle, exécution.
 */

export interface Provenance {
  created_at: string;
  image_ids: string[];
  model: string | null;
  run_id: string | null;
  step: string | null;
}
/**
 * Incertitude 1-sigma en mètres (ou radians pour un angle). None = inconnue, pas zéro.
 */

export interface Uncertainty {
  interval: [unknown, unknown] | null;
  notes: string | null;
  sigma: number | null;
}

export interface Opening {
  confidence: number;
  height: number;
  host_wall: string;
  id: string;
  kind: OpeningKind;
  leaves: number;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  offset: number;
  provenance: Provenance;
  sill_height: number;
  source: Source1;
  split: number;
  swing: Swing | null;
  type: "opening";
  uncertainty: Uncertainty;
  visible: boolean;
  width: number;
}
/**
 * Sens d'ouverture, vu depuis le start_vertex du mur hôte. Battants : côté des gonds (du
 * vantail principal pour une paire) et côté d'ouverture. Coulissants : `hinge` est le côté
 * où le vantail s'efface, `direction` la face du mur où court le rail (applique).
 */

export interface Swing {
  angle: number;
  direction: "inward" | "outward";
  hinge: "left" | "right";
}

export interface Room {
  /**
   * @minItems 3
   */
  boundary_walls: [string, string, string, ...string[]];
  confidence: number;
  id: string;
  intent: DesignIntent | null;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  provenance: Provenance;
  room_type: string | null;
  source: Source1;
  type: "room";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * Ce que la pièce doit devenir (V1). Une par pièce, dans `Room.intent`.
 */

export interface DesignIntent {
  avoid: string[];
  decor_density: "sobre" | "normal" | "riche";
  furniture: FurnitureWish[];
  instruction: string | null;
  light: LightIntent;
  materials: SurfaceMaterials;
  /**
   * @maxItems 2
   */
  mood:
    | []
    | ["chaleureux" | "lumineux" | "epure" | "cosy" | "brut"]
    | [
        "chaleureux" | "lumineux" | "epure" | "cosy" | "brut",
        "chaleureux" | "lumineux" | "epure" | "cosy" | "brut",
      ];
  /**
   * @maxItems 5
   */
  palette:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string];
  room_type: string | null;
  sources: {
    [k: string]: FieldSource;
  };
  style: string;
  translated_by: string | null;
  usage: string | null;
  version: 1;
  views: ViewsIntent;
  zones: (
    | "couchage"
    | "lecture"
    | "bureau"
    | "rangement"
    | "detente"
    | "cuisine"
    | "repas"
  )[];
}

export interface FurnitureWish {
  category: string;
  count: number;
}

export interface LightIntent {
  time: "matin" | "midi" | "heure_doree" | "soir";
  warmth: "froide" | "neutre" | "chaude";
}
/**
 * Matière voulue par surface ; vide = le style décide.
 */

export interface SurfaceMaterials {
  floor: string | null;
  furniture: string | null;
  gables: string | null;
  slopes: string | null;
  timber: string | null;
  walls: string | null;
}

export interface FieldSource {
  confidence: number;
  source: Source1;
}
/**
 * Les trois vues automatiques (ensemble, zone, détail) et leur format.
 */

export interface ViewsIntent {
  aspect: "4:3" | "4:5";
  detail: boolean;
  overall: boolean;
  zone: boolean;
}

export interface Floor {
  confidence: number;
  derived: boolean;
  id: string;
  locked: boolean;
  material: string | null;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  polygon: Point2[] | null;
  provenance: Provenance;
  room: string;
  source: Source1;
  type: "floor";
  uncertainty: Uncertainty;
  visible: boolean;
}

export interface Ceiling {
  confidence: number;
  derived: boolean;
  height: number | null;
  id: string;
  locked: boolean;
  material: string | null;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  polygon: Point2[] | null;
  provenance: Provenance;
  room: string;
  source: Source1;
  type: "ceiling";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * Pente : plan défini par sa ligne d'égout, sa hauteur à l'égout et son inclinaison.
 *
 * Réservé à la version 0.4 ; présent pour ne pas migrer le format plus tard.
 */

export interface RoofPlane {
  confidence: number;
  eave_end: Point2;
  eave_height: number;
  eave_start: Point2;
  id: string;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  pitch: number;
  provenance: Provenance;
  rooms: string[];
  source: Source1;
  type: "roof_plane";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * Fenêtre de toit (velux) dans une pente (D1, schéma 0.2.0) : châssis de `width` le long de
 * l'égout et `length` le long de la pente, son bord bas à `distance` de l'égout (mesurée le long
 * de la pente) et son bord gauche à `offset` du début de l'égout (`eave_start`).
 */

export interface RoofWindow {
  confidence: number;
  distance: number;
  id: string;
  length: number;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  offset: number;
  provenance: Provenance;
  roof: string;
  source: Source1;
  type: "roof_window";
  uncertainty: Uncertainty;
  visible: boolean;
  width: number;
}

export interface Beam {
  confidence: number;
  end: Point3;
  id: string;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  provenance: Provenance;
  section_height: number;
  section_width: number;
  source: Source1;
  start: Point3;
  type: "beam";
  uncertainty: Uncertainty;
  visible: boolean;
}

export interface Radiator {
  bottom_height: number;
  clearance: number;
  confidence: number;
  depth: number;
  height: number;
  host_wall: string;
  id: string;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  offset: number;
  provenance: Provenance;
  source: Source1;
  type: "radiator";
  uncertainty: Uncertainty;
  visible: boolean;
  width: number;
}
/**
 * Cage d'escalier paramétrique (S1, schéma 0.3.0) : trémie dans le plancher de ce niveau,
 * escalier qui en descend de `rise` mètres, garde-corps sur les côtés ouverts. Droit, quart
 * tournant (palier carré), demi-tournant (deux volées parallèles) ou hélicoïdal ; marches
 * calculées par la règle de Blondel (`openplan.geom.stairs`). `position` est le centre de
 * l'encombrement en plan (le centre du cercle pour un hélicoïdal), `yaw` la direction de
 * montée de la première volée, `turn` le côté vers lequel il tourne.
 */

export interface Stairs {
  confidence: number;
  id: string;
  kind: "straight" | "quarter_turn" | "half_turn" | "spiral";
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  position: Point2;
  provenance: Provenance;
  railing: boolean;
  rise: number;
  source: Source1;
  to_level: string | null;
  turn: "left" | "right";
  type: "stairs";
  uncertainty: Uncertainty;
  visible: boolean;
  width: number;
  yaw: number;
}
/**
 * Élément fixe générique : évier, cheminée, placard bâti, prise...
 */

export interface Fixture {
  bbox: BBox3;
  category: string;
  confidence: number;
  host_wall: string | null;
  id: string;
  keep_clear: number;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  provenance: Provenance;
  source: Source1;
  transform: Transform3;
  type: "fixture";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * Boîte englobante alignée sur l'objet : largeur (x), profondeur (y), hauteur (z).
 */

export interface BBox3 {
  depth: number;
  height: number;
  width: number;
}
/**
 * Position, rotation autour de Z, échelle uniforme. Suffisant pour un objet posé.
 */

export interface Transform3 {
  position: Point3;
  scale: number;
  yaw: number;
}

export interface Furniture {
  asset_id: string;
  bbox: BBox3;
  category: string;
  confidence: number;
  id: string;
  locked: boolean;
  material_overrides: {
    [k: string]: string;
  };
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  preserve: boolean;
  provenance: Provenance;
  source: Source1;
  transform: Transform3;
  type: "furniture";
  uncertainty: Uncertainty;
  visible: boolean;
}

export interface Light {
  color_temp_k: number;
  confidence: number;
  id: string;
  kind: "point" | "sun" | "area" | "spot";
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  position: Point3;
  power_w: number;
  provenance: Provenance;
  source: Source1;
  type: "light";
  uncertainty: Uncertainty;
  visible: boolean;
}
/**
 * Caméra de rendu, ou caméra estimée d'une photo si image_id est renseigné.
 */

export interface Camera {
  confidence: number;
  focal_px: number | null;
  id: string;
  image_id: string | null;
  locked: boolean;
  metadata: {
    [k: string]: JsonValue;
  };
  name: string | null;
  pitch: number;
  position: Point3;
  provenance: Provenance;
  roll: number;
  source: Source1;
  type: "camera";
  uncertainty: Uncertainty;
  visible: boolean;
  yaw: number;
}
/**
 * KAN-66 : sur quoi repose l'échelle d'un plan estimé depuis une photo, dit tel quel à
 * l'utilisateur (« Échelle calée sur votre mesure de 2,04 m (porte) »).
 */

export interface ScaleBasis {
  before: RoomSize | null;
  camera_height_m: number | null;
  confidence: ScaleConfidence | null;
  confidence_inputs: ConfidenceInputs;
  factor: number | null;
  image_id: string;
  previous: ScaleBasis | null;
  segment_id: string | null;
  source: "reference" | "camera_height" | "default";
  text: string;
}

export interface RoomSize {
  height_m: number;
  length_m: number;
  width_m: number;
}
/**
 * KAN-31 + KAN-66 : niveau de confiance de l'estimation et sa raison, tenus à jour avec
 * l'échelle (une mesure de référence le change) ; une seule source de vérité pour le front.
 */

export interface ScaleConfidence {
  expected_error: number;
  label: string;
  level: "fiable" | "a_verifier" | "approximatif";
  reason: string;
}
/**
 * Ce que l'estimation savait d'elle-même, gardé pour recalculer la confiance quand
 * l'échelle change sans réestimer la photo.
 */

export interface ConfidenceInputs {
  eave_unseen: boolean;
  floor_out_of_view: boolean;
  focal_from_vps: boolean;
}
/**
 * Sommet 2D partagé entre murs, dans le repère du niveau.
 */

export interface Vertex {
  confidence: number;
  id: string;
  locked: boolean;
  position: Point2;
  source: Source1;
  uncertainty: Uncertainty;
}

export interface Material {
  /**
   * @minItems 3
   * @maxItems 3
   */
  base_color: [unknown, unknown, unknown];
  id: string;
  license:
    "open_source" | "cc0" | "cc_by" | "commercial_restricted" | "unknown";
  metallic: number;
  name: string;
  roughness: number;
  texture: string | null;
}
/**
 * Une mesure connue, toujours d'autorité utilisateur.
 *
 * Elle cible exactement une chose : un élément, deux sommets du plan, ou deux points image.
 */

export interface Measurement {
  confirmed_by_user: boolean;
  created_at: string;
  element_id: string | null;
  id: string;
  image_points: [unknown, unknown, unknown] | null;
  kind: MeasurementKind;
  note: string | null;
  tolerance: number;
  value: number;
  vertex_ids: [unknown, unknown] | null;
}
/**
 * Réponses aux questions guidées ; tout est facultatif.
 */

export interface GuidedAnswers {
  area_m2: number | null;
  aspect: ("4:3" | "4:5") | null;
  /**
   * @maxItems 8
   */
  avoid:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  bedrooms: number | null;
  /**
   * @maxItems 8
   */
  furniture:
    | []
    | [string]
    | [string, string]
    | [string, string, string]
    | [string, string, string, string]
    | [string, string, string, string, string]
    | [string, string, string, string, string, string]
    | [string, string, string, string, string, string, string]
    | [string, string, string, string, string, string, string, string];
  light_time: ("matin" | "midi" | "heure_doree" | "soir") | null;
  /**
   * @maxItems 2
   */
  mood:
    | []
    | ["chaleureux" | "lumineux" | "epure" | "cosy" | "brut"]
    | [
        "chaleureux" | "lumineux" | "epure" | "cosy" | "brut",
        "chaleureux" | "lumineux" | "epure" | "cosy" | "brut",
      ];
  room_type: string | null;
  space: ("appartement" | "maison" | "studio" | "piece" | "combles") | null;
  style: string | null;
}

export interface PrivacySettings {
  allow_remote_models: boolean;
  design_journal: boolean;
  keep_exif_focal: boolean;
  remote_consent: boolean;
  remote_text_only: boolean;
  strip_exif: boolean;
  telemetry: boolean;
}
/**
 * Chemin « zéro geste » (V1, point 6) : ce qui s'enchaîne après le dépôt d'un fichier.
 */

export interface RenderSettings {
  auto_finish: boolean;
  auto_inspiration: boolean;
  auto_on_drop: boolean;
  auto_path: boolean;
}

export interface StepResult {
  artifacts: Artifact[];
  assumptions: Assumption[];
  confidence: number;
  duration_s: number;
  model_ids: string[];
  patch: {
    [k: string]: JsonValue;
  }[];
  step: string;
  warnings: string[];
}
/**
 * Fichier inspectable produit par une étape : masque, profondeur, SVG, GLB...
 */

export interface Artifact {
  description: string;
  media_type: string;
  name: string;
  path: string;
}

export interface Assumption {
  impact: "low" | "medium" | "high";
  text: string;
}

export interface ValidationReport {
  score: number;
  suggestions: string[];
  valid: boolean;
  violations: Violation[];
  warnings: string[];
}

export interface Violation {
  element_ids: string[];
  message: string;
  severity: "low" | "medium" | "high";
  type: string;
}
