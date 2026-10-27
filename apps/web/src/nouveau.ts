import type { GuidedAnswers } from "./api/client";

/** Contrat « /nouveau v1 » (site → app, apps/site/README.md) : le formulaire de plandesign.fr
 *  préremplit le premier espace de travail. Les limites du site ne sont garanties que par le
 *  navigateur : tout est revalidé ici, un paramètre inconnu est ignoré, un champ hors bornes
 *  est traité comme absent. Le compositeur s'ouvre prérempli et le plan se génère depuis ces
 *  exigences (retour d'augus, 19 oct. ; voir NewFromSite). */

/** Valeurs de `ROOM_TYPES` (openplan/schemas/assistant.py), reprises par le site. */
const ROOM_TYPES = [
  "living_room",
  "bedroom",
  "kitchen",
  "bathroom",
  "office",
  "attic",
  "hallway",
  "other",
] as const;

/** Bornes des réponses guidées (`GuidedAnswers.area_m2`, openplan/schemas/home.py). */
const AREA_MIN = 8;
const AREA_MAX = 400;
/** Longueur de la consigne gardée (`pending_instruction`). */
export const INSTRUCTION_MAX = 300;
const DESCRIPTION_MAX = 1000;

export interface SitePrefill {
  instruction: string;
  answers: GuidedAnswers;
}

function clip(text: string, max: number): string {
  if (text.length <= max) return text;
  const cut = text.slice(0, max);
  const space = cut.lastIndexOf(" ");
  return (space > max * 0.6 ? cut.slice(0, space) : cut).trimEnd();
}

/** `?v=1&source=site&piece=…&surface=…&description=…` → ce qui préremplit le compositeur ;
 *  null si ce n'est pas le contrat v1 (rien n'est prérempli, l'espace s'ouvre vide). */
export function parseNouveau(query: string): SitePrefill | null {
  const params = new URLSearchParams(query);
  if (params.get("v") !== "1") return null;
  const answers: GuidedAnswers = {};
  const piece = (params.get("piece") ?? "").trim();
  if ((ROOM_TYPES as readonly string[]).includes(piece)) {
    if (piece === "attic") answers.space = "combles";
    else if (piece !== "other") {
      answers.space = "piece";
      answers.room_type = piece;
    }
  }
  // « 35 », « 35.5 », « 35,5 », « 35.55 » (arrondi au dixième, comme le champ du site).
  const surfaceText = (params.get("surface") ?? "").trim().replace(",", ".");
  const surface = /^\d{1,4}(\.\d{1,3})?$/.test(surfaceText)
    ? Math.round(Number(surfaceText) * 10) / 10
    : Number.NaN;
  if (Number.isFinite(surface) && surface >= AREA_MIN && surface <= AREA_MAX)
    answers.area_m2 = surface;
  const description = (params.get("description") ?? "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, DESCRIPTION_MAX);
  return { instruction: clip(description, INSTRUCTION_MAX), answers };
}
