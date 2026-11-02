/** KAN-99 : zoom et déplacement de la vue comparée, communs à tous les rendus comparés (sans
 *  React, testé seul). Chaque rendu est dans un cadre de même taille (4:3) : une seule vue
 *  (échelle, décalage en pixels du cadre) montre le même endroit dans chacun.
 *
 *  L'image, à l'échelle `scale`, couvre au moins le cadre : le décalage est borné pour ne
 *  jamais laisser de vide (à l'échelle 1, aucun déplacement). */

export interface CompareView {
  scale: number;
  x: number;
  y: number;
}

export const FIT: CompareView = { scale: 1, x: 0, y: 0 };
export const MAX_SCALE = 6;
/** Pas des boutons − / + et des touches - / + (molette : plus fin). */
export const ZOOM_STEP = 1.25;
export const WHEEL_STEP = 1.15;

/** Bornes de l'échelle (1 à MAX_SCALE) et du décalage (aucun bord vide). */
export function clampView(
  view: CompareView,
  width: number,
  height: number,
): CompareView {
  const scale = Math.min(MAX_SCALE, Math.max(1, view.scale));
  const x = Math.min(0, Math.max(width - width * scale, view.x));
  const y = Math.min(0, Math.max(height - height * scale, view.y));
  return { scale, x: x || 0, y: y || 0 };
}

/** Zoom de `factor` autour du point (cx, cy) du cadre : ce point reste sous le pointeur. */
export function zoomAt(
  view: CompareView,
  factor: number,
  cx: number,
  cy: number,
  width: number,
  height: number,
): CompareView {
  const scale = Math.min(MAX_SCALE, Math.max(1, view.scale * factor));
  const k = scale / view.scale;
  return clampView(
    { scale, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k },
    width,
    height,
  );
}

/** Déplacement de (dx, dy) pixels du cadre. */
export function panBy(
  view: CompareView,
  dx: number,
  dy: number,
  width: number,
  height: number,
): CompareView {
  return clampView(
    { scale: view.scale, x: view.x + dx, y: view.y + dy },
    width,
    height,
  );
}

/** La transformation CSS d'une image comparée. */
export function transformOf(view: CompareView): string {
  return `translate(${view.x}px, ${view.y}px) scale(${view.scale})`;
}

/** « 100 % », « 220 % ». */
export function zoomText(view: CompareView): string {
  return `${Math.round(view.scale * 100)} %`;
}
