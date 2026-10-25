/** Commandes d'édition, miroir de openplan/scene/ops.py. */
import type { Opening, Point2, Source, Swing } from "./types.gen";

export type Anchor = "start" | "end" | "center";

export interface MoveVertex {
  op: "move_vertex";
  vertex_id: string;
  to: Point2;
}

export interface SetWallLength {
  op: "set_wall_length";
  wall_id: string;
  length: number;
  anchor?: Anchor;
}

export interface AddWall {
  op: "add_wall";
  start: string | Point2;
  end: string | Point2;
  thickness?: number;
  source?: Source;
  confidence?: number;
}

/** Velux dans une pente (D1) : bord gauche à `offset` du début de l'égout, bord bas à
 *  `distance` de l'égout le long de la pente. */
export interface AddRoofWindow {
  op: "add_roof_window";
  roof: string;
  offset: number;
  distance: number;
  width?: number;
  length?: number;
  name?: string | null;
}

/** Cage d'escalier (S1) : trémie dans ce plancher, escalier qui descend au niveau d'en
 *  dessous ; `yaw` = sens de montée de la première volée. */
export interface AddStairs {
  op: "add_stairs";
  kind?: "straight" | "quarter_turn" | "half_turn" | "spiral";
  position: Point2;
  yaw?: number;
  width?: number;
  turn?: "left" | "right";
  railing?: boolean;
  rise?: number | null;
  name?: string | null;
}

export interface AddOpening {
  op: "add_opening";
  host_wall: string;
  kind: Opening["kind"];
  offset: number;
  width: number;
  height: number;
  sill_height?: number;
  swing?: Swing | null;
  /** Vantaux d'une porte-fenêtre ; part du vantail principal d'une paire (E5). */
  leaves?: number;
  split?: number;
  source?: Source;
  confidence?: number;
}

/** Pose un meuble du catalogue au sol, centré en `position` ; dimensions du catalogue par
 *  défaut, réglables dans la plage de l'asset. */
export interface AddFurniture {
  op: "add_furniture";
  asset_id: string;
  position: Point2;
  yaw?: number;
  width?: number | null;
  depth?: number | null;
  height?: number | null;
  name?: string | null;
  source?: Source;
  confidence?: number;
}

/** Remplace un meuble par un autre modèle du catalogue (P4) : même place, même lacet, même
 *  identifiant ; dimensions et nom du nouveau modèle. */
export interface ReplaceFurniture {
  op: "replace_furniture";
  element_id: string;
  asset_id: string;
}

/** Point de vue libre pour l'écran Rendre : posé dans le plan, visée `yaw`, hauteur d'œil. */
export interface AddCamera {
  op: "add_camera";
  position: Point2;
  yaw?: number;
  height?: number;
  name?: string | null;
  /** Tangage (radians) et focale pour 1280 px : la vue d'architecte les donne (D3). */
  pitch?: number;
  focal_px?: number | null;
}

/** KAN-92 : une zone de sol (4 coins dans l'ordre), dans une pièce ; Pousser/Tirer l'élève
 *  ensuite (`update_element`, champ `elevation`, 0 à 1,20 m). */
export interface AddFloorZone {
  op: "add_floor_zone";
  polygon: [Point2, Point2, Point2, Point2];
  elevation?: number;
  name?: string | null;
}

export interface UpdateElement {
  op: "update_element";
  element_id: string;
  fields: Record<string, unknown>;
}

/** Intention de design d'une pièce (V1) : les champs touchés par l'utilisateur deviennent
 *  prioritaires (`user_defined`), jamais réécrits par une étape automatique. */
export interface SetIntent {
  op: "set_intent";
  room_id: string;
  fields: Record<string, unknown>;
}

export interface UpdateVertex {
  op: "update_vertex";
  vertex_id: string;
  fields: Record<string, unknown>;
}

export interface RemoveElement {
  op: "remove_element";
  element_id: string;
}

export interface RemoveVertex {
  op: "remove_vertex";
  vertex_id: string;
}

export interface DetectRooms {
  op: "detect_rooms";
}

export interface UpdateLevel {
  op: "update_level";
  fields: Record<string, unknown>;
}

export type Op =
  | MoveVertex
  | SetWallLength
  | AddWall
  | AddOpening
  | AddRoofWindow
  | AddStairs
  | AddFurniture
  | ReplaceFurniture
  | AddCamera
  | AddFloorZone
  | UpdateElement
  | SetIntent
  | UpdateVertex
  | RemoveElement
  | RemoveVertex
  | DetectRooms
  | UpdateLevel;
