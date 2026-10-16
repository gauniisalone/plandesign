/** Transformation plan (mètres, Y vers le haut) ↔ écran (pixels, Y vers le bas). */
import type { Bounds, Point2 } from "../api/types.gen";

export interface View {
  /** pixels par mètre */
  scale: number;
  /** position écran de l'origine du plan */
  tx: number;
  ty: number;
}

export const DEFAULT_VIEW: View = { scale: 80, tx: 80, ty: 520 };

export function toScreen(v: View, p: Point2): { x: number; y: number } {
  return { x: v.tx + p.x * v.scale, y: v.ty - p.y * v.scale };
}

export function toWorld(v: View, sx: number, sy: number): Point2 {
  return { x: (sx - v.tx) / v.scale, y: (v.ty - sy) / v.scale };
}

/** Marges d'écran (px) occupées par les barres flottantes : le recadrage place le plan dans
 *  la zone libre, jamais sous une barre. */
export interface Insets {
  top: number;
  right: number;
  bottom: number;
  left: number;
}

export const NO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };

export function fitView(
  bounds: Bounds | null,
  width: number,
  height: number,
  insets: Insets = NO_INSETS,
): View {
  if (!bounds || width <= 0 || height <= 0) return DEFAULT_VIEW;
  const freeW = Math.max(120, width - insets.left - insets.right);
  const freeH = Math.max(120, height - insets.top - insets.bottom);
  const pad = 1.0; // mètres
  const w = bounds.max.x - bounds.min.x + 2 * pad;
  const h = bounds.max.y - bounds.min.y + 2 * pad;
  const scale = Math.max(10, Math.min(freeW / w, freeH / h));
  const cx = (bounds.min.x + bounds.max.x) / 2;
  const cy = (bounds.min.y + bounds.max.y) / 2;
  const centerX = insets.left + freeW / 2;
  const centerY = insets.top + freeH / 2;
  return { scale, tx: centerX - cx * scale, ty: centerY + cy * scale };
}

export function zoomAt(v: View, factor: number, sx: number, sy: number): View {
  const scale = Math.min(2000, Math.max(5, v.scale * factor));
  const k = scale / v.scale;
  return { scale, tx: sx - (sx - v.tx) * k, ty: sy - (sy - v.ty) * k };
}

export function snapToGrid(p: Point2, step: number): Point2 {
  return { x: Math.round(p.x / step) * step, y: Math.round(p.y / step) * step };
}

export function distance(a: Point2, b: Point2): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

/** Projection d'un point sur un segment : abscisse le long de l'axe (m) et distance (m). */
export function projectOnSegment(
  p: Point2,
  a: Point2,
  b: Point2,
): { along: number; dist: number; length: number } {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const length = Math.hypot(dx, dy);
  if (length === 0) return { along: 0, dist: distance(p, a), length: 0 };
  const t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (length * length);
  const clamped = Math.max(0, Math.min(1, t));
  const foot = { x: a.x + clamped * dx, y: a.y + clamped * dy };
  return { along: clamped * length, dist: distance(p, foot), length };
}
