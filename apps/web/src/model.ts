/** Aides de lecture du format de scène côté client. Aucune géométrie ici : le serveur calcule. */
import type { Op } from "./api/ops";
import type { Point2 } from "./api/types.gen";
import type {
  DerivedRoof,
  RoofWindow,
  Camera,
  Furniture,
  Level,
  Opening,
  Project,
  RoofPlane,
  Room,
  Source,
  Stairs,
  Vertex,
  Violation,
  Wall,
} from "./api/types.gen";

export type Element = Level["elements"][string];

export type Selection =
  | { kind: "none" }
  | { kind: "vertex"; id: string }
  | { kind: "element"; id: string };

export const NO_SELECTION: Selection = { kind: "none" };

export const UNCERTAIN_SOURCES: ReadonlySet<Source> = new Set<Source>([
  "estimated",
  "inferred",
  "generated",
]);

export const SOURCE_LABEL: Record<Source, string> = {
  measured: "mesuré",
  user_defined: "saisi",
  observed: "observé",
  estimated: "estimé",
  inferred: "inféré",
  generated: "généré",
};

export function isUncertain(source: Source): boolean {
  return UNCERTAIN_SOURCES.has(source);
}

/** Les photos du projet peuvent partir chez un modèle distant : accord donné dans Paramètres
 *  avancés › IA sans « texte seul » (la passerelle refuse les images en texte seul). */
export function photosMayLeave(project: Project): boolean {
  const p = project.privacy;
  return p.allow_remote_models && p.remote_consent && !p.remote_text_only;
}

/** Où vont les photos, pour les mentions (A1, T6) : jamais « sur cette machine » si c'est
 *  faux, ni sur le serveur PlanDesign (`online`) ni quand un modèle distant les reçoit. */
export function photosWhere(project: Project, online: boolean): string {
  if (photosMayLeave(project))
    return "peut partir chez le modèle distant autorisé dans Paramètres avancés › IA";
  return online
    ? "gardée sur le serveur PlanDesign"
    : "reste sur cette machine";
}

/** Statut du plan (A3) : « estimated » tant qu'un plan reconstruit (photo, image) n'a ni cote
 *  saisie ni recalibrage ; « generated » pour un plan proposé depuis un texte, tant qu'il
 *  n'a pas été ajusté ; null pour un plan relevé (DXF, PDF, SVG), saisi ou recalibré. Jamais
 *  un relevé certifié : le libellé le dit. */
export type PlanStatus = "estimated" | "generated" | null;

export function planStatus(project: Project, level: Level): PlanStatus {
  const ws = walls(level);
  if (ws.length === 0) return null;
  if (project.calibrations.some((c) => c.level_id === level.id)) return null;
  if (ws.some((w) => w.source === "user_defined" || w.source === "measured"))
    return null;
  if (ws.some((w) => w.source === "estimated" || w.source === "inferred"))
    return "estimated";
  return ws.some((w) => w.source === "generated") ? "generated" : null;
}

export const PLAN_STATUS_LABEL: Record<"estimated" | "generated", string> = {
  estimated: "Estimé — à vérifier",
  generated: "Proposé — à ajuster",
};

export function isCamera(e: Element | undefined): e is Camera {
  return e?.type === "camera";
}

/** Toutes les caméras du niveau : vues des photos et points de vue libres (écran Rendre). */
export function allCameras(level: Level): Camera[] {
  return Object.values(level.elements).filter(isCamera);
}

/** Point de vue libre : posé par l'utilisateur, sans photo. */
export function isFreeCamera(camera: Camera): boolean {
  return camera.image_id === null;
}

/** Pose d'une caméra pour le viewer 3D (position, lacet, inclinaison). */
export function poseOf(camera: Camera): {
  position: Camera["position"];
  yaw: number;
  pitch: number;
} {
  return { position: camera.position, yaw: camera.yaw, pitch: camera.pitch };
}

/** Caméras de photos (vues) du niveau, dans l'ordre d'ajout. */
export function cameras(level: Level): Camera[] {
  return Object.values(level.elements)
    .filter(isCamera)
    .filter((c) => c.image_id !== null);
}

export function viewsOfImage(level: Level, imageId: string): Camera[] {
  return cameras(level).filter((c) => c.image_id === imageId);
}

/** Photo écartée par l'utilisateur : conservée dans le projet, sans effet sur le plan. */
export function isExcludedView(camera: Camera): boolean {
  return camera.metadata["excluded"] === true;
}

/** Largeurs vues entre deux murs opposés, par axe de la scène (mètres). */
export function observedExtents(camera: Camera): Record<string, number> {
  const raw = camera.metadata["observed_extents_m"];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, number> = {};
  for (const [axis, value] of Object.entries(raw)) {
    if (typeof value === "number") out[axis] = value;
  }
  return out;
}

/** La photo a vu au moins un mur (distances observées) : sa vue peut être tournée. */
export function hasObservations(camera: Camera): boolean {
  const raw = camera.metadata["observed_distances_m"];
  return (
    !!raw &&
    typeof raw === "object" &&
    !Array.isArray(raw) &&
    Object.keys(raw).length > 0
  );
}

/** La contradiction entre photos qui concerne cette caméra, s'il y en a une. */
export function violationFor(
  violations: Violation[] | undefined,
  cameraId: string,
): Violation | undefined {
  return violations?.find(
    (v) => v.type === "inconsistent_views" && v.element_ids.includes(cameraId),
  );
}

export function excludeViewOp(camera: Camera, excluded: boolean): Op {
  return {
    op: "update_element",
    element_id: camera.id,
    fields: { metadata: { ...camera.metadata, excluded } },
  };
}

export function isWall(e: Element | undefined): e is Wall {
  return e?.type === "wall";
}

export function isFurniture(e: Element | undefined): e is Furniture {
  return e?.type === "furniture";
}

export function furnitureItems(level: Level): Furniture[] {
  return Object.values(level.elements).filter(isFurniture);
}

/** Coins de l'emprise d'un meuble (monde), centré sur sa transformée, tourné de son lacet. */
/** Types de pièce proposés dans la fiche (R2), dans l'ordre de la liste. */
export const ROOM_TYPE_LABELS: Record<string, string> = {
  bedroom: "Chambre",
  living_room: "Séjour",
  kitchen: "Cuisine",
  bathroom: "Salle de bain",
  office: "Bureau",
  attic: "Combles",
  other: "Autre",
};

/** Sortes de niveau (R2) et nom donné au niveau tant que l'utilisateur ne l'a pas renommé. */
export const LEVEL_KIND_LABELS: Record<string, string> = {
  ground: "Rez-de-chaussée",
  upper: "Étage",
  attic: "Combles / grenier",
  basement: "Sous-sol",
};
export const LEVEL_DEFAULT_NAMES: Record<string, string> = {
  ground: "Rez-de-chaussée",
  upper: "Étage",
  attic: "Grenier",
  basement: "Sous-sol",
};

/** Cage d'escalier (S1) : son symbole en plan vient du serveur (`derived.stairs`). */
export function isStairs(e: Element | undefined): e is Stairs {
  return e?.type === "stairs";
}

export const STAIR_LABELS: Record<Stairs["kind"] & string, string> = {
  straight: "Droit",
  quarter_turn: "Quart tournant",
  half_turn: "Demi-tournant",
  spiral: "Hélicoïdal",
};

/** Circulation (S1) : cages d'escalier paramétriques, pas des meubles du catalogue.
 *  Encombrements pour 2,70 m d'étage et 90 cm de large (le serveur recalcule tout). */
export interface StairCard {
  kind: "straight" | "quarter_turn" | "half_turn" | "spiral";
  label: string;
  along: number;
  across: number;
}

export const STAIR_CARDS: StairCard[] = [
  { kind: "straight", label: "Escalier droit", along: 3.78, across: 0.9 },
  {
    kind: "quarter_turn",
    label: "Escalier quart tournant",
    along: 2.79,
    across: 2.52,
  },
  {
    kind: "half_turn",
    label: "Escalier demi-tournant",
    along: 2.79,
    across: 1.8,
  },
  { kind: "spiral", label: "Escalier hélicoïdal", along: 2.0, across: 2.0 },
];

/** Velux (D1) : élément `roof_window`, rattaché à une pente. */
export function isRoofWindow(e: Element | undefined): e is RoofWindow {
  return e?.type === "roof_window";
}

export function roofWindows(level: Level): RoofWindow[] {
  return Object.values(level.elements).filter(isRoofWindow);
}

/** Emprise au sol d'un velux, d'après sa pente vue en plan (même calcul que l'export). */
export function roofWindowCorners(roof: DerivedRoof, w: RoofWindow): Point2[] {
  const [a, b] = roof.eave as [Point2, Point2];
  const len = Math.hypot(b.x - a.x, b.y - a.y);
  if (len < 1e-9) return [];
  const ux = (b.x - a.x) / len;
  const uy = (b.y - a.y) / len;
  const c = Math.cos(roof.pitch);
  return [
    [w.offset, w.distance],
    [w.offset + w.width, w.distance],
    [w.offset + w.width, w.distance + w.length],
    [w.offset, w.distance + w.length],
  ].map(([along, up]) => ({
    x: a.x + ux * along! + roof.inward.x * up! * c,
    y: a.y + uy * along! + roof.inward.y * up! * c,
  }));
}

export function furnitureCorners(f: Furniture): Point2[] {
  const { x, y } = f.transform.position;
  const yaw = f.transform.yaw;
  const w = f.bbox.width * f.transform.scale;
  const d = f.bbox.depth * f.transform.scale;
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  return [
    [-w / 2, -d / 2],
    [w / 2, -d / 2],
    [w / 2, d / 2],
    [-w / 2, d / 2],
  ].map(([dx, dy]) => ({ x: x + c * dx! - s * dy!, y: y + s * dx! + c * dy! }));
}

export function isOpening(e: Element | undefined): e is Opening {
  return e?.type === "opening";
}

export function isRoof(e: Element | undefined): e is RoofPlane {
  return e?.type === "roof_plane";
}

export function isRoom(e: Element | undefined): e is Room {
  return e?.type === "room";
}

export function firstLevel(project: Project): Level | undefined {
  return Object.values(project.levels)[0];
}

/** T7e : le plan d'un rendu (écrit dans le rendu, sinon celui de sa caméra), comme
 *  `openplan.pipeline.board._level_of` ; undefined si aucun. */
export function renderLevelId(
  project: Project,
  entry: { level_id?: string | null; camera_id: string | null },
): string | undefined {
  if (entry.level_id && project.levels[entry.level_id]) return entry.level_id;
  const cameraId = entry.camera_id;
  if (!cameraId) return undefined;
  return Object.values(project.levels).find((l) => l.elements[cameraId])?.id;
}

/** T7e : le plan d'une caméra. */
export function cameraLevelId(
  project: Project,
  cameraId: string,
): string | undefined {
  return Object.values(project.levels).find((l) => l.elements[cameraId])?.id;
}

export function walls(level: Level): Wall[] {
  return Object.values(level.elements).filter(isWall);
}

export function openings(level: Level): Opening[] {
  return Object.values(level.elements).filter(isOpening);
}

export function rooms(level: Level): Room[] {
  return Object.values(level.elements).filter(isRoom);
}

export function vertexOf(level: Level, id: string): Vertex | undefined {
  return level.vertices[id];
}

export function formatLength(
  valueM: number,
  units: Project["units_display"],
): string {
  if (units === "cm") return `${(valueM * 100).toFixed(0)} cm`;
  if (units === "mm") return `${(valueM * 1000).toFixed(0)} mm`;
  return `${valueM.toFixed(2)} m`;
}

const MONTHS = [
  "janv.",
  "févr.",
  "mars",
  "avr.",
  "mai",
  "juin",
  "juil.",
  "août",
  "sept.",
  "oct.",
  "nov.",
  "déc.",
];

/** Date française fixe, quel que soit le navigateur : « 1er oct. 2026 à 10:56 » (heure
 *  locale de la machine). */
export function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const day = d.getDate();
  return `${day === 1 ? "1er" : day} ${MONTHS[d.getMonth()]} ${d.getFullYear()} à ${formatTime(iso)}`;
}

/** Heure seule : « 10:56 ». */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Convertit une saisie dans l'unité d'affichage vers des mètres. */
export function parseLength(
  text: string,
  units: Project["units_display"],
): number | null {
  const value = Number(text.replace(",", "."));
  if (!Number.isFinite(value)) return null;
  if (units === "cm") return value / 100;
  if (units === "mm") return value / 1000;
  return value;
}

/** Clés de `Level.background_transform` : mètres par pixel, translation (m), rotation (rad), opacité. */
export interface BackgroundTransform {
  scale: number;
  tx: number;
  ty: number;
  rotation: number;
  opacity: number;
  /** Étirement de l'image selon ses deux axes (R3 : longueur et largeur recalibrées). */
  stretch_x: number;
  stretch_y: number;
}

const DEFAULT_BACKGROUND_WIDTH_M = 4;

export function readTransform(
  t: Record<string, number>,
  widthPx: number,
): BackgroundTransform {
  return {
    scale: t["scale"] ?? DEFAULT_BACKGROUND_WIDTH_M / widthPx,
    tx: t["tx"] ?? 0,
    ty: t["ty"] ?? 0,
    rotation: t["rotation"] ?? 0,
    opacity: t["opacity"] ?? 0.5,
    stretch_x: t["stretch_x"] ?? 1,
    stretch_y: t["stretch_y"] ?? 1,
  };
}

export function toDisplay(
  valueM: number,
  units: Project["units_display"],
): number {
  if (units === "cm") return Math.round(valueM * 100 * 10) / 10;
  if (units === "mm") return Math.round(valueM * 1000);
  return Math.round(valueM * 1000) / 1000;
}
