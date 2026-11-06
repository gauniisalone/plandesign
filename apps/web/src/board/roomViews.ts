/** KAN-137 (maquette 2fb2691, validée par augus le 6 nov.) : les vues calculées par pièce
 *  dans le node Rendus. Une vue de pièce est un point de vue ordinaire, marqué
 *  `metadata.room_view` (l'identifiant de sa pièce) par le serveur (pipeline/room_views.py) ;
 *  sa case (`room_view_on`) dit si « Rendre toutes les pièces » la prend. Logique pure, sans
 *  React (testée par `node --test`). */

import type { Camera, Level } from "../api/types.gen";

export const ROOM_KEY = "room_view";
export const ON_KEY = "room_view_on";

export interface RoomRow {
  camera: Camera;
  roomId: string;
  name: string;
  /** Surface de la pièce (m²), lue au calcul de la vue. */
  area: number | null;
  /** « depuis l'entrée · 1,55 m · 26 mm · 78 % du sol visible » */
  note: string;
  on: boolean;
}

type Meta = Record<string, unknown> | null | undefined;

const meta = (c: { metadata?: unknown }): Meta =>
  (c.metadata ?? null) as Meta;

export function isRoomView(c: { type?: string; metadata?: unknown }): boolean {
  return typeof meta(c)?.[ROOM_KEY] === "string";
}

function cameraOf(e: unknown): Camera | null {
  const c = e as Camera | undefined;
  return c && c.type === "camera" ? c : null;
}

/** Les vues de pièce du niveau, la plus grande pièce d'abord (puis par nom). */
export function roomRows(level: Level | undefined): RoomRow[] {
  if (!level) return [];
  const rows: RoomRow[] = [];
  for (const e of Object.values(level.elements)) {
    const c = cameraOf(e);
    if (!c || !isRoomView(c)) continue;
    const m = meta(c)!;
    const area = typeof m.area_m2 === "number" ? m.area_m2 : null;
    rows.push({
      camera: c,
      roomId: String(m[ROOM_KEY]),
      name: c.name || (typeof m.room_name === "string" ? m.room_name : "Pièce"),
      area,
      note: typeof m.note === "string" ? m.note : "",
      on: m[ON_KEY] !== false,
    });
  }
  return rows.sort(
    (a, b) => (b.area ?? 0) - (a.area ?? 0) || a.name.localeCompare(b.name, "fr"),
  );
}

/** Le plan a des pièces fermées (« Proposer une vue par pièce » a un sens). */
export function hasRooms(level: Level | undefined): boolean {
  return Boolean(
    level && Object.values(level.elements).some((e) => e.type === "room"),
  );
}

/** « 31 m² », « 4,5 m² » : la surface comme sur le plan, sans décimale inutile. */
export function areaText(area: number | null): string {
  if (area === null) return "";
  const rounded = area >= 10 ? Math.round(area) : Math.round(area * 10) / 10;
  return `${String(rounded).replace(".", ",")} m²`;
}

/** Le changement de la case d'une pièce : l'opération `update_element` à envoyer. */
export function tickOp(row: RoomRow, on: boolean) {
  return {
    op: "update_element" as const,
    element_id: row.camera.id,
    fields: { metadata: { ...(meta(row.camera) ?? {}), [ON_KEY]: on } },
  };
}

/** Filtre par pièce de la galerie : le nom de pièce de chaque rendu d'une vue de pièce,
 *  dans l'ordre des pièces ; vide s'il y a moins de deux pièces rendues. */
export function roomFilters(
  level: Level | undefined,
  requests: { camera_id: string; photo_id?: string | null }[],
): string[] {
  if (!level) return [];
  const names: string[] = [];
  for (const r of requests) {
    if (r.photo_id) continue;
    const c = cameraOf(level.elements[r.camera_id]);
    if (!c || !isRoomView(c)) continue;
    const name = c.name || "Pièce";
    if (!names.includes(name)) names.push(name);
  }
  const order = roomRows(level).map((row) => row.name);
  names.sort((a, b) => order.indexOf(a) - order.indexOf(b));
  return names.length >= 2 ? names : [];
}

/** Les rendus d'une pièce (pour le filtre) : leurs identifiants. */
export function rendersOfRoom(
  level: Level | undefined,
  requests: { id: string; camera_id: string; photo_id?: string | null }[],
  name: string,
): Set<string> {
  return new Set(
    requests
      .filter((r) => {
        if (r.photo_id || !level) return false;
        const c = cameraOf(level.elements[r.camera_id]);
        return Boolean(c && isRoomView(c) && (c.name || "Pièce") === name);
      })
      .map((r) => r.id),
  );
}
