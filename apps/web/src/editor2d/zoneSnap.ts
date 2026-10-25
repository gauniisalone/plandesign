/** KAN-92 : accroche d'un coin de zone de sol, en 2D comme en 3D (sans React, testé seul).
 *
 *  Une zone se pose sur le sol de la pièce : elle s'accroche aux angles et aux faces
 *  INTÉRIEURES de la pièce (le polygone intérieur dérivé par le serveur), jamais à l'axe des
 *  murs, qui est dans leur épaisseur (le serveur refuserait la zone, hors de la pièce). */

import type { Point2 } from "../api/types.gen";

export interface ZoneSnap {
  point: Point2;
  /** Ce qui a accroché, dit près du curseur : « angle », « mur », ou rien. */
  label: "angle" | "mur" | null;
}

const cm = (v: number) => Math.round(v * 100) / 100;

function project(
  p: Point2,
  a: Point2,
  b: Point2,
): { point: Point2; d: number } {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy || 1;
  const t = Math.max(
    0,
    Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2),
  );
  const point = { x: a.x + t * dx, y: a.y + t * dy };
  return { point, d: Math.hypot(p.x - point.x, p.y - point.y) };
}

/** Le coin accroché : l'angle intérieur le plus proche à moins de `reach` mètres, sinon la
 *  face intérieure la plus proche, sinon le point arrondi au centimètre ; `free` (Alt) :
 *  le point seul, arrondi. */
export function snapZoneCorner(
  p: Point2,
  rooms: Point2[][],
  reach: number,
  free = false,
): ZoneSnap {
  const rounded = { x: cm(p.x), y: cm(p.y) };
  if (free) return { point: rounded, label: null };
  let corner: { point: Point2; d: number } | null = null;
  for (const ring of rooms)
    for (const c of ring) {
      const d = Math.hypot(p.x - c.x, p.y - c.y);
      if (d <= reach && (!corner || d < corner.d)) corner = { point: c, d };
    }
  if (corner) return { point: corner.point, label: "angle" };
  let face: { point: Point2; d: number } | null = null;
  for (const ring of rooms)
    ring.forEach((a, i) => {
      const hit = project(p, a, ring[(i + 1) % ring.length]!);
      if (hit.d <= reach && (!face || hit.d < face.d)) face = hit;
    });
  if (face) {
    const f = face as { point: Point2; d: number };
    return { point: f.point, label: "mur" };
  }
  return { point: rounded, label: null };
}

/** Le centre d'une zone (moyenne de ses coins), où s'écrit sa hauteur. */
export function zoneCenter(polygon: readonly Point2[]): Point2 {
  return {
    x: polygon.reduce((s, p) => s + p.x, 0) / polygon.length,
    y: polygon.reduce((s, p) => s + p.y, 0) / polygon.length,
  };
}

/** La zone avec un coin déplacé (les autres ne bougent pas). */
export function movedCorner(
  polygon: readonly Point2[],
  index: number,
  to: Point2,
): [Point2, Point2, Point2, Point2] {
  const out = polygon.map((p, i) => (i === index ? to : p));
  return [out[0]!, out[1]!, out[2]!, out[3]!];
}
