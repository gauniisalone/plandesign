/** KAN-91 (lot L6) : ce que la galerie du cadre Rendus montre, sans React (testé seul).
 *
 *  La galerie a une taille fixe (le cadre ne grandit jamais tout seul) : au plus `GALLERY_CELLS`
 *  cases de même format (4:3), la plus récente d'abord. Les demandes en attente ou en cours
 *  prennent les premières cases ; au-delà, la dernière case dit « +N plus anciens · Tout voir ». */

import type { Project } from "../api/types.gen";
import type { RealisticRequest } from "../api/client";

export const GALLERY_CELLS = 4;
export const DEFAULT_SECONDS = 120;

export type Tile =
  | { kind: "running"; request: RealisticRequest }
  | { kind: "done"; request: RealisticRequest }
  /** La dernière case quand tout ne tient pas : « +N plus anciens · Tout voir », sur l'image
   *  du premier d'entre eux. */
  | { kind: "more"; request: RealisticRequest; count: number };

export interface Gallery {
  tiles: Tile[];
  /** Tous les rendus faits, du plus récent au plus ancien (la visionneuse les parcourt). */
  done: RealisticRequest[];
  /** Le dernier échec ou la dernière expiration, s'il est plus récent que tout rendu fait. */
  problem: RealisticRequest | null;
}

const newestFirst = (a: RealisticRequest, b: RealisticRequest) =>
  b.created_at.localeCompare(a.created_at);

export function isRunning(r: RealisticRequest): boolean {
  return r.state === "pending" || r.state === "taken";
}

export function gallery(
  requests: RealisticRequest[],
  cells = GALLERY_CELLS,
): Gallery {
  const sorted = [...requests].sort(newestFirst);
  const running = sorted.filter(isRunning);
  const done = sorted.filter((r) => r.state === "done" && r.image);
  const tiles: Tile[] = [
    ...running.slice(0, cells).map((request) => ({
      kind: "running" as const,
      request,
    })),
  ];
  const room = Math.max(0, cells - tiles.length);
  if (done.length <= room) {
    tiles.push(...done.map((request) => ({ kind: "done" as const, request })));
  } else if (room > 0) {
    const shown = done.slice(0, room - 1);
    tiles.push(...shown.map((request) => ({ kind: "done" as const, request })));
    tiles.push({
      kind: "more",
      request: done[room - 1]!,
      count: done.length - shown.length,
    });
  }
  const latestDone = done[0]?.created_at ?? "";
  const problem =
    sorted.find(
      (r) =>
        (r.state === "failed" || r.state === "expired") &&
        r.created_at > latestDone,
    ) ?? null;
  return { tiles, done, problem };
}

/** « ~2 min », « ~45 s » : la durée typique (médiane du journal du poste, sinon 2 min). */
export function etaText(seconds: number | undefined): string {
  const s = seconds && seconds > 0 ? seconds : DEFAULT_SECONDS;
  if (s < 90) return `~${Math.max(5, Math.round(s / 5) * 5)} s`;
  return `~${Math.round(s / 60)} min`;
}

/** Avancement estimé d'un rendu pris (0 à 0,95 : jamais « fini » avant l'image). */
export function progress(
  request: RealisticRequest,
  typicalSeconds: number | undefined,
  now: number,
): number | null {
  if (request.state !== "taken" || !request.taken_at) return null;
  const typical =
    typicalSeconds && typicalSeconds > 0 ? typicalSeconds : DEFAULT_SECONDS;
  const elapsed = (now - Date.parse(request.taken_at)) / 1000;
  return Math.max(0, Math.min(0.95, elapsed / typical));
}

/** Le numéro d'une photo, le même que dans la bande de photos et sur le plan. */
export function photoNumber(project: Project, imageId: string): number | null {
  const index = Object.keys(project.images).indexOf(imageId);
  return index < 0 ? null : index + 1;
}
