/** Intention de design d'une pièce (V1, note 10) : libellés, valeur par défaut et résumé
 *  lisible. Jumeau de `openplan/schemas/intent.py` (mêmes vocabulaires). */

import type { DesignIntent, Room } from "./api/types.gen";

export const STYLE_LABELS: Record<string, string> = {
  naturel: "Naturel",
  chalet: "Chalet",
  scandinave: "Scandinave",
  industriel: "Industriel",
  contemporain: "Contemporain",
};
export const MOOD_LABELS: Record<string, string> = {
  chaleureux: "chaleureux",
  lumineux: "lumineux",
  epure: "épuré",
  cosy: "cosy",
  brut: "brut",
};
export const DENSITY_LABELS: Record<string, string> = {
  sobre: "sobre",
  normal: "normal",
  riche: "riche",
};
export const LIGHT_TIME_LABELS: Record<string, string> = {
  matin: "matin",
  midi: "midi",
  heure_doree: "heure dorée",
  soir: "soir",
};
export const WARMTH_LABELS: Record<string, string> = {
  froide: "froide",
  neutre: "neutre",
  chaude: "chaude",
};
export const ZONE_LABELS: Record<string, string> = {
  couchage: "couchage",
  lecture: "lecture",
  bureau: "bureau",
  rangement: "rangement",
  detente: "détente",
};
export const SURFACE_LABELS: Record<string, string> = {
  floor: "sol",
  walls: "murs",
  gables: "pignons",
  slopes: "pentes",
  timber: "charpente",
  furniture: "meubles",
};

/** L'intention de la pièce, ou celle qu'un calcul déduirait (tout `inferred`). */
export function intentOf(room: Room): DesignIntent {
  return (
    room.intent ?? {
      version: 1,
      room_type: room.room_type ?? null,
      usage: null,
      instruction: null,
      translated_by: null,
      style: "naturel",
      mood: [],
      palette: [],
      materials: {
        floor: null,
        walls: null,
        gables: null,
        slopes: null,
        timber: null,
        furniture: null,
      },
      decor_density: "normal",
      light: { time: "midi", warmth: "neutre" },
      furniture: [],
      avoid: [],
      zones: [],
      views: { overall: true, zone: true, detail: true, aspect: "4:3" },
      sources: {},
    }
  );
}

/** Le champ a-t-il été choisi par l'utilisateur (prioritaire sur l'automatique) ? */
export function chosen(intent: DesignIntent, field: string): boolean {
  return intent.sources[field]?.source === "user_defined";
}

/** Une phrase : « Naturel · chaleureux · décor normal · lumière de midi, neutre ». */
export function intentSummary(intent: DesignIntent): string {
  const parts = [STYLE_LABELS[intent.style] ?? intent.style];
  if (intent.mood.length)
    parts.push(intent.mood.map((m) => MOOD_LABELS[m] ?? m).join(" et "));
  parts.push(`décor ${DENSITY_LABELS[intent.decor_density]}`);
  const when: Record<string, string> = {
    matin: "du matin",
    midi: "de midi",
    heure_doree: "de l'heure dorée",
    soir: "du soir",
  };
  parts.push(
    `lumière ${when[intent.light.time]}, ${WARMTH_LABELS[intent.light.warmth]}`,
  );
  if (intent.zones.length)
    parts.push(`zones : ${intent.zones.map((z) => ZONE_LABELS[z]).join(", ")}`);
  return parts.join(" · ");
}
