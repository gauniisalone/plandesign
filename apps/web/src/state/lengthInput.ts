/** KAN-66 : la longueur connue saisie pour une mesure de référence sur une photo. Virgule ou
 *  point décimal ; unité m, cm ou mm (« 2,04 m », « 204 cm », « 2 m 04 ») ; un nombre seul est
 *  lu en cm s'il est entier (« 204 »), en mètres s'il a une partie décimale (« 2,04 ») : une
 *  porte de 2,04 cm n'existe pas. Les limites sont celles du serveur (5 cm à 30 m) ; il refuse
 *  aussi, ceci ne fait que le dire avant l'envoi. */

export const LENGTH_MIN_M = 0.05;
export const LENGTH_MAX_M = 30;

export type LengthParse =
  { ok: true; meters: number } | { ok: false; error: string };

const EXAMPLE = "par exemple 2,04 m ou 204 cm";

const UNITS: Record<string, number> = {
  m: 1,
  metre: 1,
  metres: 1,
  mètre: 1,
  mètres: 1,
  cm: 0.01,
  centimetre: 0.01,
  centimetres: 0.01,
  centimètre: 0.01,
  centimètres: 0.01,
  mm: 0.001,
  millimetre: 0.001,
  millimetres: 0.001,
  millimètre: 0.001,
  millimètres: 0.001,
};

const NUMBER = "(\\d+(?:[.,]\\d+)?|[.,]\\d+)";

/** « 2,04 m » : deux décimales, virgule (texte visible). */
export function formatMeters(meters: number): string {
  return `${meters.toFixed(2).replace(".", ",")} m`;
}

function toNumber(text: string): number {
  return Number(text.replace(",", "."));
}

export function parseLength(raw: string): LengthParse {
  const text = raw.trim().toLowerCase().replace(/\s+/g, " ");
  if (!text) return { ok: false, error: `Saisissez une longueur, ${EXAMPLE}.` };
  if (/^-/.test(text))
    return { ok: false, error: "Une longueur est positive." };

  let meters: number;
  // « 2 m 04 », « 2m04 » : mètres et centimètres.
  const mixed = new RegExp("^(\\d+) ?m ?(\\d{1,2})$").exec(text);
  const unit = new RegExp(`^${NUMBER} ?([a-zè]+)$`).exec(text);
  const bare = new RegExp(`^${NUMBER}$`).exec(text);
  /** Le nombre seul, entier : lu en cm (sert aussi à l'indice « écrivez 2 m »). */
  const bareInteger =
    bare && !/[.,]/.test(bare[1] ?? "") ? (bare[1] ?? "") : null;
  if (mixed) {
    meters =
      Number(mixed[1] ?? "") + Number((mixed[2] ?? "").padEnd(2, "0")) / 100;
  } else if (unit) {
    const name = unit[2] ?? "";
    const factor = UNITS[name];
    if (factor === undefined)
      return {
        ok: false,
        error: `Unité « ${name} » inconnue : écrivez m, cm ou mm (${EXAMPLE}).`,
      };
    meters = toNumber(unit[1] ?? "") * factor;
  } else if (bare) {
    const value = toNumber(bare[1] ?? "");
    meters = bareInteger !== null ? value / 100 : value;
  } else {
    return {
      ok: false,
      error: `« ${raw.trim()} » n'est pas une longueur : écrivez-la ${EXAMPLE}.`,
    };
  }

  if (!Number.isFinite(meters) || meters <= 0)
    return { ok: false, error: "Une longueur est plus grande que zéro." };
  // Arrondi au millimètre : « 204 cm » vaut exactement 2,04 m.
  meters = Math.round(meters * 1000) / 1000;
  if (meters < LENGTH_MIN_M)
    return {
      ok: false,
      error: `Trop court (${formatShort(meters)}) : une mesure fait au moins 5 cm.${
        bareInteger !== null
          ? ` Pour des mètres, écrivez « ${bareInteger} m ».`
          : ""
      }`,
    };
  if (meters > LENGTH_MAX_M)
    return {
      ok: false,
      error: `Trop long (${formatMeters(meters)}) : une mesure fait au plus 30 m.`,
    };
  return { ok: true, meters };
}

/** Une petite longueur refusée, dite dans l'unité où elle parle (« 2 cm », « 3 mm »). */
function formatShort(meters: number): string {
  if (meters >= 0.01)
    return `${String(Math.round(meters * 1000) / 10).replace(".", ",")} cm`;
  return `${Math.round(meters * 1000)} mm`;
}
