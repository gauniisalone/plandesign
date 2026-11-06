/** KAN-134 (maquette 45d0d6d, validée par augus le 6 nov.) : l'infobulle de l'app, la même
 *  partout, pour les icônes sans texte (`data-tip`). Au survol après 0,4 s, dès le focus
 *  clavier, et au toucher par un appui long (un simple toucher n'affiche rien et agit comme
 *  avant). Logique pure, sans React ni DOM (testée par `node --test`). */

export const TIP_DELAY_MS = 400;
export const LONG_PRESS_MS = 500;
/** Un doigt qui glisse de plus de 10 px ne fait pas un appui long (il fait défiler). */
export const PRESS_SLOP_PX = 10;
/** Après un appui long, l'infobulle reste lisible un moment, puis s'en va. */
export const TOUCH_LINGER_MS = 1500;
const GAP_PX = 6;
const GUTTER_PX = 8;

export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** La place de l'infobulle : sous l'icône, centrée ; au-dessus s'il n'y a pas la place
 *  dessous ; jamais hors de l'écran (marge de 8 px). */
export function placeTip(
  target: Box,
  tip: { width: number; height: number },
  view: { width: number; height: number },
): { left: number; top: number; side: "below" | "above" } {
  const below = target.top + target.height + GAP_PX;
  const fitsBelow = below + tip.height <= view.height - GUTTER_PX;
  const above = target.top - GAP_PX - tip.height;
  const side = fitsBelow || above < GUTTER_PX ? "below" : "above";
  const centre = target.left + target.width / 2 - tip.width / 2;
  const maxLeft = Math.max(GUTTER_PX, view.width - GUTTER_PX - tip.width);
  return {
    left: Math.round(Math.min(maxLeft, Math.max(GUTTER_PX, centre))),
    top: Math.round(side === "below" ? below : above),
    side,
  };
}

/** Le texte lu : celui de `data-tip`, sans espaces de trop ; rien s'il est vide. */
export function tipText(raw: string | null | undefined): string | null {
  const text = (raw ?? "").replace(/\s+/g, " ").trim();
  return text || null;
}

/** L'infobulle double-t-elle le nom déjà lu (aria-label) ? Alors pas d'aria-describedby :
 *  un lecteur d'écran ne lit pas deux fois la même chose. */
export function sameAsName(text: string, name: string | null | undefined): boolean {
  const norm = (s: string) => s.replace(/\s+/g, " ").trim().toLowerCase();
  return Boolean(name) && norm(text) === norm(name!);
}

/** Le doigt a-t-il assez peu bougé pour rester un appui long ? */
export function stillPressing(
  start: { x: number; y: number },
  now: { x: number; y: number },
): boolean {
  return Math.hypot(now.x - start.x, now.y - start.y) <= PRESS_SLOP_PX;
}
