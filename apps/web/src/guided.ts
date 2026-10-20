/** Questions guidées de l'accueil (L2) : choix proposés, libellés et résumé lisible (ligne
 *  « Compris »). Jumeau de `openplan/schemas/home.py` et `openplan/pipeline/guided.py`. */

import type { GuidedAnswers } from "./api/client";
import { typed } from "./format";

export const SPACES: {
  value: string;
  label: string;
  space: GuidedAnswers["space"];
  room?: string;
}[] = [
  { value: "", label: "Laisser choisir", space: null },
  { value: "appartement", label: "Appartement", space: "appartement" },
  { value: "maison", label: "Maison", space: "maison" },
  { value: "studio", label: "Studio", space: "studio" },
  { value: "combles", label: "Combles / grenier", space: "combles" },
  {
    value: "bedroom",
    label: "Une pièce : chambre",
    space: "piece",
    room: "bedroom",
  },
  {
    value: "living_room",
    label: "Une pièce : séjour",
    space: "piece",
    room: "living_room",
  },
  {
    value: "kitchen",
    label: "Une pièce : cuisine",
    space: "piece",
    room: "kitchen",
  },
  {
    value: "bathroom",
    label: "Une pièce : salle de bain",
    space: "piece",
    room: "bathroom",
  },
  {
    value: "office",
    label: "Une pièce : bureau",
    space: "piece",
    room: "office",
  },
];

export const STYLES: { value: string; label: string }[] = [
  { value: "", label: "Laisser choisir" },
  { value: "naturel", label: "Naturel" },
  { value: "chalet", label: "Chalet" },
  { value: "scandinave", label: "Scandinave" },
  { value: "industriel", label: "Industriel" },
  { value: "contemporain", label: "Contemporain" },
];

export const MOODS: { value: string; label: string }[] = [
  { value: "chaleureux", label: "chaleureux" },
  { value: "lumineux", label: "lumineux" },
  { value: "epure", label: "épuré" },
  { value: "cosy", label: "cosy" },
  { value: "brut", label: "brut" },
];

export const LIGHTS: { value: string; label: string }[] = [
  { value: "", label: "Laisser choisir" },
  { value: "matin", label: "matin" },
  { value: "midi", label: "midi" },
  { value: "heure_doree", label: "heure dorée" },
  { value: "soir", label: "soir" },
];

/** Meubles proposés en un clic (catégories du catalogue). */
export const FURNITURE: { value: string; label: string }[] = [
  { value: "bed_double", label: "lit double" },
  { value: "bed_single", label: "lit simple" },
  { value: "sofa", label: "canapé" },
  { value: "armchair", label: "fauteuil" },
  { value: "desk", label: "bureau" },
  { value: "bookshelf", label: "bibliothèque" },
  { value: "wardrobe", label: "armoire" },
  { value: "rug", label: "tapis" },
];

const label = (
  list: { value: string; label: string }[],
  value?: string | null,
) => list.find((x) => x.value === value)?.label ?? value ?? "";

/** Une réponse au moins est donnée ? */
/** Exigences du compositeur (T7b) : le texte libre et les réponses guidées. */
export interface Requirements {
  text: string;
  answers: GuidedAnswers;
}

export function hasAnswers(a: GuidedAnswers): boolean {
  return Boolean(
    a.space ||
    a.area_m2 ||
    (a.bedrooms !== undefined && a.bedrooms !== null) ||
    a.style ||
    a.aspect ||
    a.light_time ||
    a.mood?.length ||
    a.furniture?.length ||
    a.avoid?.length,
  );
}

/** « combles · 35 m² · 2 chambres · scandinave · portrait 4:5 · sans tapis ». */
export function answersSummary(a: GuidedAnswers): string[] {
  const out: string[] = [];
  const space = SPACES.find(
    (s) =>
      s.space === a.space &&
      (s.room ?? null) === (a.space === "piece" ? a.room_type : null),
  );
  if (a.space && space)
    out.push(space.label.replace("Une pièce : ", "").toLowerCase());
  if (a.area_m2) out.push(`${typed(a.area_m2)} m²`);
  if (a.bedrooms !== undefined && a.bedrooms !== null)
    out.push(
      a.bedrooms === 0
        ? "sans chambre"
        : `${a.bedrooms} chambre${a.bedrooms > 1 ? "s" : ""}`,
    );
  if (a.style) out.push(label(STYLES, a.style).toLowerCase());
  for (const m of a.mood ?? []) out.push(label(MOODS, m));
  if (a.light_time) out.push(label(LIGHTS, a.light_time));
  if (a.aspect) out.push(a.aspect === "4:5" ? "portrait 4:5" : "paysage 4:3");
  for (const f of a.furniture ?? []) out.push(label(FURNITURE, f));
  for (const f of a.avoid ?? []) out.push(`sans ${label(FURNITURE, f)}`);
  return out;
}
