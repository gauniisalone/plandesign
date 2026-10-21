import type { NoteColor } from "../api/client";

/** KAN-65 : post-its de couleur, comme sur Miro. Palette, couleur par défaut (la dernière
 *  utilisée, gardée dans ce navigateur), taille du texte qui tient dans le post-it. Sans
 *  dépendance (testé par `npm run test:unit`). */

export const POSTIT_COLORS: readonly { id: NoteColor; label: string }[] = [
  { id: "yellow", label: "Jaune" },
  { id: "orange", label: "Orange" },
  { id: "pink", label: "Rose" },
  // KAN-77 : « lavande » (décision d'augus du 21 oct.) ; la valeur gardée reste `violet`.
  { id: "violet", label: "Lavande" },
  { id: "blue", label: "Bleu" },
  { id: "green", label: "Vert" },
  { id: "gray", label: "Gris" },
  { id: "white", label: "Blanc" },
];

/** Une note d'avant KAN-65 n'a pas de couleur : jaune, sans migration. */
export const DEFAULT_COLOR: NoteColor = "yellow";
export const POSTIT_SIDE = 200;

export function colorOf(item: { color?: NoteColor | null }): NoteColor {
  return item.color ?? DEFAULT_COLOR;
}

const KEY = "pd.postit.color";
const KNOWN = new Set<string>(POSTIT_COLORS.map((c) => c.id));

/** La dernière couleur choisie (confort de ce navigateur ; jaune s'il n'y en a pas). */
export function lastColor(): NoteColor {
  try {
    const saved = globalThis.localStorage?.getItem(KEY);
    if (saved && KNOWN.has(saved)) return saved as NoteColor;
  } catch {
    // Stockage bloqué (navigation privée) : jaune.
  }
  return DEFAULT_COLOR;
}

export function rememberColor(color: NoteColor): void {
  try {
    globalThis.localStorage?.setItem(KEY, color);
  } catch {
    // Stockage bloqué : la couleur reste jaune à la prochaine ouverture.
  }
}

/** La plus grande taille de texte (en px de l'espace de travail, entre `min` et `max`) pour
 *  laquelle `fits` dit vrai, par dichotomie ; `min` si rien ne tient (le texte défile). */
export function fitFontSize(
  fits: (size: number) => boolean,
  min = 8,
  max = 40,
): number {
  if (fits(max)) return max;
  let lo = min;
  let hi = max;
  while (hi - lo > 0.5) {
    const mid = (lo + hi) / 2;
    if (fits(mid)) lo = mid;
    else hi = mid;
  }
  return Math.floor(lo * 2) / 2;
}

/** Redimensionner un post-it : un carré par défaut (le plus grand côté), un rectangle avec
 *  Maj. */
export function resizedPostit(
  w: number,
  h: number,
  free: boolean,
): { w: number; h: number } {
  if (free) return { w, h };
  const side = Math.max(w, h);
  return { w: side, h: side };
}
