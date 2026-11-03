/** KAN-120 : ce que le node Photos de la pièce dit de ses photos, et son bouton. Le plan ne se
 *  génère plus à l'import d'une photo : « Générer le plan à partir de N photos », puis
 *  « Régénérer le plan ». Lu sur le niveau relié (caméras des photos, recoupement KAN-95). */

import type { Level } from "../api/types.gen";
import { walls } from "../model";
import { fusionOf } from "../state/photoReference";

export type PhotoUse = "used" | "excluded" | "new" | "failed";

export interface PhotoState {
  use: PhotoUse;
  /** Pourquoi elle est écartée (recoupement) ou pas analysée (navigateur). */
  reason: string;
}

export interface PhotosState {
  total: number;
  used: number;
  /** Photos ajoutées après le plan : elles attendent « Régénérer ». */
  fresh: number;
  hasPlan: boolean;
  /** « N photos concordent à ±X % » quand le plan vient d'un recoupement. */
  summary: string | null;
  photos: Record<string, PhotoState>;
}

const plural = (n: number, word: string) => `${n} ${word}${n > 1 ? "s" : ""}`;

export const FAILED_REASON = "analyse impossible : « Régénérer » la réessaie";

export function photosState(
  level: Level | undefined,
  members: string[],
  failed: ReadonlySet<string> = new Set(),
): PhotosState {
  const hasPlan = Boolean(level && walls(level).length > 0);
  const fusion = level ? fusionOf(level) : null;
  const posed = new Set(
    level
      ? Object.values(level.elements)
          .filter((e) => e.type === "camera" && "image_id" in e && e.image_id)
          .map((e) => ("image_id" in e ? (e.image_id as string) : ""))
      : [],
  );
  const read = new Map(fusion?.photos.map((p) => [p.imageId, p]) ?? []);
  const photos: Record<string, PhotoState> = {};
  for (const id of members) {
    const fused = read.get(id);
    if (failed.has(id)) photos[id] = { use: "failed", reason: FAILED_REASON };
    else if (fused)
      photos[id] = fused.kept
        ? { use: "used", reason: fused.reason }
        : { use: "excluded", reason: fused.reason };
    else if (hasPlan && posed.has(id) && !fusion)
      photos[id] = { use: "used", reason: "" };
    else photos[id] = { use: "new", reason: "" };
  }
  const states = Object.values(photos);
  return {
    total: members.length,
    used: states.filter((p) => p.use === "used").length,
    fresh: hasPlan ? states.filter((p) => p.use === "new").length : 0,
    hasPlan,
    summary: fusion?.summary || null,
    photos,
  };
}

/** Le bouton principal du node : il dit combien de photos il lira. */
export function generateLabel(s: PhotosState): string {
  if (!s.hasPlan) return `Générer le plan à partir de ${plural(s.total, "photo")}`;
  return s.fresh > 0
    ? `Régénérer le plan à partir de ${plural(s.total, "photo")}`
    : "Régénérer le plan";
}

/** La phrase sous les photos : inviter à en ajouter, ou dire ce qui a servi. */
export function photosHint(s: PhotosState): string {
  if (!s.hasPlan)
    return s.total < 3
      ? "Ajoutez 2 ou 3 photos prises depuis d'autres coins de la pièce : le plan sera plus sûr."
      : "Plusieurs angles : les photos seront recoupées entre elles.";
  if (s.fresh > 0)
    return `${plural(s.fresh, "photo")} pas encore ${s.fresh > 1 ? "utilisées" : "utilisée"} : le plan ne change pas tant que vous ne le régénérez pas.`;
  return s.used < s.total
    ? `${plural(s.used, "photo")} ${s.used > 1 ? "utilisées" : "utilisée"} sur ${s.total}.`
    : `${plural(s.used, "photo")} ${s.used > 1 ? "utilisées" : "utilisée"}.`;
}

export const PHOTO_TAGS: Record<PhotoUse, string> = {
  used: "utilisée",
  excluded: "écartée",
  new: "pas encore utilisée",
  failed: "non analysée",
};

export const REGENERATE_TITLE = "Remplacer le plan actuel ?";

/** L'avertissement de « Régénérer le plan » (décision d'augus : les meubles qui tiennent
 *  dans les nouveaux murs restent ; les autres, comptés à la fin). */
export function regenerateWarning(total: number, furniture: number): string {
  const kept =
    furniture > 0
      ? ` Les meubles qui tiennent dans les nouveaux murs restent à leur place ; ceux qui en sortent sont retirés.`
      : "";
  return `Le plan sera recalculé à partir de ${plural(total, "photo")} et remplacera le plan actuel, avec les modifications que vous y avez faites (murs, portes, fenêtres).${kept} Portes, fenêtres et éléments accrochés aux anciens murs sont refaits avec les nouveaux. Ctrl+Z ou « Annuler » remet l'ancien plan en entier.`;
}
