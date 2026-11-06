/** KAN-134 (maquette 45d0d6d, validée par augus le 6 nov.) : « Réglages des rendus » du node
 *  Rendus en ligne, et le compteur du quota. Ambiance (le style de l'intention, dans le plan),
 *  focale des vues de pièce (« Automatique » par défaut : la focale du calcul, pièce par pièce)
 *  et nombre de tirages par pièce (1 à 3). Focale et tirages restent dans le navigateur, par
 *  projet (décision du PO : rien dans le schéma). Logique pure, sans React (testée par
 *  `node --test`). */

import { FOCAL_MAX, FOCAL_MIN } from "./roomViews.ts";

export const DRAWS_MIN = 1;
export const DRAWS_MAX = 3;

export interface RenderSettings {
  /** Tirages par pièce à chaque « Rendre » (chacun pris sur le quota du jour). */
  draws: number;
  /** Focale imposée aux vues recalculées (mm) ; null : « Automatique ». */
  focal: number | null;
}

export const DEFAULT_SETTINGS: RenderSettings = { draws: 1, focal: null };

/** Ce qui est lu (stockage, champ) ramené à des réglages valides. */
export function cleanSettings(raw: unknown): RenderSettings {
  const r = (raw ?? {}) as Partial<Record<keyof RenderSettings, unknown>>;
  const draws =
    typeof r.draws === "number" && Number.isFinite(r.draws)
      ? Math.min(DRAWS_MAX, Math.max(DRAWS_MIN, Math.round(r.draws)))
      : DEFAULT_SETTINGS.draws;
  const focal =
    typeof r.focal === "number" &&
    Number.isFinite(r.focal) &&
    r.focal >= FOCAL_MIN &&
    r.focal <= FOCAL_MAX
      ? Math.round(r.focal)
      : null;
  return { draws, focal };
}

const key = (projectId: string) => `render-settings:${projectId}`;

type Store = Pick<Storage, "getItem" | "setItem">;

function storage(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readSettings(projectId: string, store = storage()): RenderSettings {
  try {
    const text = store?.getItem(key(projectId));
    return text ? cleanSettings(JSON.parse(text)) : DEFAULT_SETTINGS;
  } catch {
    return DEFAULT_SETTINGS;
  }
}

export function writeSettings(
  projectId: string,
  settings: RenderSettings,
  store = storage(),
): void {
  try {
    store?.setItem(key(projectId), JSON.stringify(cleanSettings(settings)));
  } catch {
    // stockage indisponible (navigation privée) : le réglage vaut pour la page
  }
}

/** Événement de fenêtre : réglages changés ou demandes de rendu envoyées hors du node
 *  (l'Assistant) ; le node Rendus relit ses réglages et sa galerie. */
export const RENDERS_EVENT = "plandesign:renders-asked";

/** Le nombre de demandes que prend un « Rendre » : pièces cochées × tirages. */
export function takes(rooms: number, draws: number): number {
  return Math.max(0, rooms) * cleanSettings({ draws }).draws;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n > 1 ? many : one}`;
}

/** Le compteur, dit avant le clic : « 3 rendus seront pris · 5 sur 20 aujourd'hui ». Sans
 *  quota connu (0), seulement ce qui sera pris ; quota dépassé, ce qui reste. */
export function quotaText(take: number, used: number, quota: number): string {
  const will =
    take > 0
      ? `${plural(take, "rendu sera pris", "rendus seront pris")}`
      : "aucun rendu ne sera pris";
  if (quota <= 0) return will;
  const left = Math.max(0, quota - used);
  const today = `${used} sur ${quota} aujourd'hui`;
  return take > left
    ? `${will} · ${today} : il en reste ${left}`
    : `${will} · ${today}`;
}

/** Sans compte exact de ce qui sera pris (vues de pièce pas encore calculées) : « 5 sur 20
 *  aujourd'hui », ou rien sans quota connu. */
export function todayText(used: number, quota: number): string {
  return quota > 0 ? `${used} sur ${quota} aujourd'hui` : "";
}

/** Le quota du jour suffit-il pour ce « Rendre » ? (Sans quota connu : oui, le serveur
 *  tranche.) */
export function fits(take: number, used: number, quota: number): boolean {
  return quota <= 0 || take <= Math.max(0, quota - used);
}

/** Les libellés du choix de focale : « Automatique », puis 24 à 35 mm. */
export function focalChoices(): { value: number | null; label: string }[] {
  const out: { value: number | null; label: string }[] = [
    { value: null, label: "Automatique (selon la pièce)" },
  ];
  for (let mm = FOCAL_MIN; mm <= FOCAL_MAX; mm += 1)
    out.push({ value: mm, label: `${mm} mm` });
  return out;
}
