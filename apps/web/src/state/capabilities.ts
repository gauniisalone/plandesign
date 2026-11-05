import { useSyncExternalStore } from "react";
import {
  UNKNOWN,
  capabilitiesFrom,
  sameCapabilities,
  type Capabilities,
  type Health,
} from "./capsFrom";
import { bootHealth, freshHealth } from "./health";

/** Ce que ce serveur sait calculer (lot « MVP en ligne sans GPU », 19 oct.), lu dans
 *  /api/health (règles dans capsFrom.ts). Relu régulièrement : un poste de calcul qui se
 *  connecte en cours de session (B3, futur champ `compute` de health) rend les fonctions
 *  disponibles sans recharger la page. KAN-134 : tant que health n'a pas répondu, « inconnu »
 *  (rien d'offert), et un nouvel essai toutes les quelques secondes. */

export { capabilitiesFrom, type Capabilities } from "./capsFrom";

const REFRESH_MS = 60_000;
/** KAN-134 : health muet au démarrage (ou depuis) : nouvel essai, sans attendre la minute. */
const UNKNOWN_RETRY_MS = 5_000;

let current: Capabilities = UNKNOWN;
let retry: number | null = null;
const listeners = new Set<() => void>();

function set(next: Capabilities): void {
  if (sameCapabilities(next, current)) return;
  current = next;
  for (const listener of listeners) listener();
}

/** Lecture immédiate (hors React : décisions dans useProject). */
export function currentCapabilities(): Capabilities {
  return current;
}

/** `boot` : la réponse partagée du démarrage (aucune requête de plus, même si le démarrage
 *  se rejoue, comme en mode strict de React) ; sinon une relecture du serveur. */
export async function refreshCapabilities(
  { boot = false }: { boot?: boolean } = {},
): Promise<Capabilities> {
  try {
    set(capabilitiesFrom((await (boot ? bootHealth() : freshHealth())) as Health));
  } catch {
    // Serveur injoignable : on garde la dernière lecture ; sans aucune lecture encore,
    // « inconnu » reste, et l'on réessaie bientôt (jamais « tout disponible »).
    if (!current.known && retry === null)
      retry = window.setTimeout(() => {
        retry = null;
        void refreshCapabilities();
      }, UNKNOWN_RETRY_MS);
  }
  return current;
}

let started = false;
/** Relecture toutes les 60 s et au retour sur l'onglet. */
export function watchCapabilities(): void {
  if (started) return;
  started = true;
  window.setInterval(() => void refreshCapabilities(), REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void refreshCapabilities();
  });
}

export function useCapabilities(): Capabilities {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
