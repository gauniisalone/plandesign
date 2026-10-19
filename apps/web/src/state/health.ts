import { api } from "../api/client";

/** `/api/health` lu UNE fois au démarrage (demande PO du 19 oct.) : l'hébergement (mentions)
 *  et les capacités (ce que le serveur sait calculer) partagent cette réponse, au lieu de deux
 *  appels en série avant le premier affichage. Les capacités se relisent ensuite d'elles-mêmes
 *  (`freshHealth`), et chaque relecture remplace la réponse partagée. */

export type Health = Awaited<ReturnType<typeof api.health>>;

let shared: Promise<Health> | null = null;

/** La réponse du démarrage, partagée (une seule requête, même si on la demande deux fois). */
export function bootHealth(): Promise<Health> {
  shared ??= api.health();
  return shared;
}

/** Une relecture (capacités) : nouvelle requête, qui devient la réponse partagée. */
export function freshHealth(): Promise<Health> {
  const next = api.health();
  next.then(
    () => {
      shared = next;
    },
    () => undefined,
  );
  return next;
}
