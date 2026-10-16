/** Le modèle d'images coupe le prompt à 77 jetons CLIP, sans prévenir (A4). */
export const CLIP_LIMIT = 77;
export const TOKEN_BUDGET = 75;

/** Estimation des jetons CLIP, la même que côté serveur (`approx_clip_tokens`) : un mot
 *  anglais courant = 1 jusqu'à neuf lettres, puis un par tranche de six ; un mot accentué
 *  = un de plus ; chaque chiffre et chaque signe = 1 ; plus les deux bornes. */
export function approxClipTokens(text: string): number {
  let count = 2;
  for (const token of text.match(/[A-Za-zÀ-ÿ]+|[0-9]|[^\sA-Za-zÀ-ÿ0-9]/g) ?? []) {
    if (/^[A-Za-zÀ-ÿ]+$/.test(token)) {
      count += token.length <= 9 ? 1 : Math.ceil(token.length / 6);
      if (/[À-ÿ]/.test(token)) count += 1;
    } else count += 1;
  }
  return count;
}
