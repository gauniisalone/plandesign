/** KAN-92 : Pousser/Tirer en 3D, sans three.js ni React (testé seul).
 *
 *  La face survolée dit ce qu'on tire : le côté d'un mur, son épaisseur ; le dessus d'un mur,
 *  sa hauteur (muret, allège) ; le dessus d'une zone de sol, son élévation. La valeur suit la
 *  souris (déplacement de la face le long de sa normale) ou se tape au clavier (« 12 cm »,
 *  « 0,40 m ») ; les limites sont celles du serveur, dites avant l'envoi.
 *
 *  Épaisseur : un mur s'épaissit autour de son axe (côté « center », le cas courant) : la face
 *  tirée avance de d, l'épaisseur grandit de 2·d, la face opposée recule d'autant. Le geste de
 *  SketchUp (la face opposée fixe) demandera de déplacer l'axe : écart dit au PO, à changer ici
 *  (`THICKNESS_PER_FACE`) et dans la commande envoyée (`pushPullOp`) le jour venu. */

import type { UpdateElement } from "../api/ops";

export type PushKind = "thickness" | "height" | "elevation";

export const LIMITS: Record<PushKind, { min: number; max: number | null }> = {
  thickness: { min: 0.05, max: 0.6 },
  height: { min: 0.1, max: null }, // au plus la hauteur du niveau
  elevation: { min: 0, max: 1.2 },
};

export const PUSH_LABELS: Record<PushKind, string> = {
  thickness: "épaisseur du mur",
  height: "hauteur du mur",
  elevation: "hauteur de la zone",
};

/** Épaisseur gagnée par mètre d'avancée de la face tirée (mur « center » : les deux faces). */
export function thicknessPerFace(offset: string | undefined): number {
  return offset === "left" || offset === "right" ? 1 : 2;
}

/** La sorte de face sous le pointeur : `up` = composante verticale de sa normale (−1 à 1). */
export function faceKind(
  elementType: string | undefined,
  up: number,
): PushKind | null {
  if (elementType === "wall") {
    if (up > 0.7) return "height";
    if (Math.abs(up) < 0.3) return "thickness";
    return null;
  }
  if (elementType === "floor_zone" && up > 0.7) return "elevation";
  return null;
}

/** « 12 cm », « 0,40 m », « 40 » (entier de 10 ou plus : cm ; sinon m), « 0 » ; null si ce
 *  n'est pas une longueur. */
export function parseTyped(raw: string): number | null {
  const text = raw.trim().toLowerCase().replace(",", ".");
  const m = /^(\d+(?:\.\d+)?|\.\d+)\s*(mm|cm|m)?$/.exec(text);
  if (!m) return null;
  const value = Number(m[1]);
  const unit = m[2];
  let meters: number;
  if (unit === "mm") meters = value / 1000;
  else if (unit === "cm") meters = value / 100;
  else if (unit === "m") meters = value;
  else meters = !m[1]!.includes(".") && value >= 10 ? value / 100 : value;
  return Math.round(meters * 1000) / 1000;
}

export interface Bounded {
  value: number;
  /** La limite touchée, dite telle quelle (« 60 cm au plus »), ou null. */
  limit: string | null;
}

const cm = (m: number) => `${Math.round(m * 100)} cm`;
const metres = (m: number) => `${m.toFixed(2).replace(".", ",")} m`;

/** La valeur ramenée dans ses limites (celles du serveur) ; `ceiling` : hauteur du niveau. */
export function bound(kind: PushKind, value: number, ceiling: number): Bounded {
  const { min } = LIMITS[kind];
  const max = LIMITS[kind].max ?? ceiling;
  const unit = kind === "thickness" ? cm : metres;
  if (value < min - 1e-9)
    return {
      value: min,
      limit: `${kind === "elevation" ? "au sol" : `${unit(min)} au moins`}`,
    };
  if (value > max + 1e-9)
    return {
      value: max,
      limit:
        kind === "height"
          ? `jusqu'au plafond (${metres(max)})`
          : `${unit(max)} au plus`,
    };
  return { value: Math.round(value * 100) / 100, limit: null };
}

/** La valeur pendant le glissé : la face avance de `advance` mètres le long de sa normale. */
export function dragged(
  kind: PushKind,
  start: number,
  advance: number,
  ceiling: number,
  offset?: string,
): Bounded {
  const gain =
    kind === "thickness" ? advance * thicknessPerFace(offset) : advance;
  return bound(kind, start + gain, ceiling);
}

/** Ce que la cote dit près du curseur : « épaisseur du mur : 22 cm ». */
export function pushText(kind: PushKind, value: number): string {
  if (kind === "thickness") return `${PUSH_LABELS[kind]} : ${cm(value)}`;
  if (kind === "elevation")
    return `${PUSH_LABELS[kind]} : ${value > 0 ? `+${cm(value)}` : "au sol"}`;
  return `${PUSH_LABELS[kind]} : ${metres(value)}`;
}

/** La commande envoyée au serveur : un seul pas d'historique. Hauteur de mur égale à celle du
 *  niveau : `null` (le mur suit le niveau, comme avant). */
export function pushPullOp(
  id: string,
  kind: PushKind,
  value: number,
  ceiling: number,
): UpdateElement {
  const v = Math.round(value * 100) / 100;
  const fields =
    kind === "thickness"
      ? { thickness: v }
      : kind === "elevation"
        ? { elevation: v }
        : { height: Math.abs(v - ceiling) < 0.005 ? null : v };
  return { op: "update_element", element_id: id, fields };
}
