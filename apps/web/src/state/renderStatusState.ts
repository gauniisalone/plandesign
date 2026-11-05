import type { RealisticStatus } from "../api/client";

/** KAN-134 : le statut des rendus réalistes (GET /api/realistic/status), sans rien de React ni
 *  du réseau (testé par `node --test`). Un échec n'est jamais un refus : il se dit, et l'on
 *  réessaie ; un refus (`allowed: false`) n'est jamais gardé pour toute la vie de la page :
 *  le statut se relit à l'ouverture d'un node Rendus, au retour sur l'onglet et après une
 *  reconnexion (renderStatus.ts). */

export type StatusState =
  /** Pas encore de réponse (premier affichage). */
  | { kind: "unknown" }
  | { kind: "ok"; status: RealisticStatus }
  /** La dernière lecture a échoué ; `last` : la dernière réponse reçue avant, s'il y en a. */
  | { kind: "failed"; failures: number; last: RealisticStatus | null };

export const UNKNOWN_STATUS: StatusState = { kind: "unknown" };

/** Dit à la place de « bientôt en ligne » quand le statut n'a pas pu être lu. */
export const STATUS_DOWN = "Statut des rendus indisponible, nouvel essai…";

export function afterAnswer(status: RealisticStatus): StatusState {
  return { kind: "ok", status };
}

export function afterFailure(previous: StatusState): StatusState {
  if (previous.kind === "failed")
    return { ...previous, failures: previous.failures + 1 };
  return {
    kind: "failed",
    failures: 1,
    last: previous.kind === "ok" ? previous.status : null,
  };
}

/** Le droit de lancer un rendu tel qu'on le connaît ; null : rien de lu encore. Après un échec,
 *  la dernière réponse reçue vaut toujours (un échec passager ne retire pas « Rendre »). */
export function allowedOf(state: StatusState): boolean | null {
  if (state.kind === "unknown") return null;
  if (state.kind === "ok") return state.status.allowed;
  return state.last ? state.last.allowed : false;
}

/** L'échec se dit seulement s'il n'y a aucune réponse d'avant à montrer. */
export function statusDown(state: StatusState): boolean {
  return state.kind === "failed" && state.last === null;
}

/** Nouvel essai après un échec : 3 s, 6 s, 12 s… jamais plus d'une minute. */
export function retryAfterMs(failures: number): number {
  return Math.min(60_000, 3_000 * 2 ** Math.max(0, failures - 1));
}

/** Événement de fenêtre émis à chaque « hello » de la connexion de présence (live.ts) : une
 *  reconnexion relit le statut. */
export const LIVE_OPEN_EVENT = "plandesign:live-open";
