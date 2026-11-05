/** KAN-105 L2 : la raison d'un refus 401, 403 ou 429 (`reason` du corps), quand le serveur la
 *  donne ; le `detail` reste le message à montrer. Module sans dépendance (lu par
 *  `unit/refusal.test.ts`). */
export type RefusalReason =
  | "login_required"
  | "bad_credentials"
  | "token_required"
  | "origin"
  | "role"
  | "admin"
  | "no_rule"
  | "not_author"
  | "project_cap"
  | "token_cap"
  | "space_full"
  | "consent"
  | "reauth"
  | "renders_off"
  | "daily_quota"
  | "rate_limited";

/** Une 401 ne veut dire « plus de session » que pour `login_required` (ou sans raison :
 *  réponse d'avant L2, ou d'un intermédiaire). Un mot de passe faux (`bad_credentials`) ou un
 *  jeton refusé ne renvoie pas à la page de connexion. */
export function sessionLost(status: number, reason: unknown): boolean {
  return (
    status === 401 && (reason === undefined || reason === "login_required")
  );
}

/** La raison lue dans un corps de réponse JSON ; absente, ou corps sans objet : undefined. */
export function reasonOf(body: unknown): RefusalReason | undefined {
  if (body === null || typeof body !== "object") return undefined;
  const reason = (body as { reason?: unknown }).reason;
  return typeof reason === "string" ? (reason as RefusalReason) : undefined;
}
