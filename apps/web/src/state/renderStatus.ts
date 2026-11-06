import { useEffect, useSyncExternalStore } from "react";
import { realisticApi } from "../api/client";
import {
  LIVE_OPEN_EVENT,
  UNKNOWN_STATUS,
  afterAnswer,
  afterFailure,
  retryAfterMs,
  type StatusState,
} from "./renderStatusState";

/** KAN-134 : le statut des rendus réalistes, partagé par les nodes Rendus et « Rendre depuis
 *  cette photo ». Avant, il était lu UNE fois par page : une erreur au premier appel (serveur
 *  qui redémarre) ou un premier « non » restait jusqu'au rechargement, et le node Rendus
 *  restait vide pour un compte pourtant autorisé. Il se relit maintenant à chaque ouverture
 *  d'un node, au retour sur l'onglet, après une reconnexion, et de lui-même après un échec. */

let state: StatusState = UNKNOWN_STATUS;
let inflight: Promise<void> | null = null;
let retry: number | null = null;
/** Les composants qui lisent le statut : sans eux, ni relecture ni nouvel essai. */
let readers = 0;
const listeners = new Set<() => void>();

function set(next: StatusState): void {
  state = next;
  for (const listener of listeners) listener();
}

function schedule(): void {
  if (retry !== null) window.clearTimeout(retry);
  retry = null;
  if (readers === 0 || state.kind !== "failed") return;
  retry = window.setTimeout(() => {
    retry = null;
    void refreshRenderStatus();
  }, retryAfterMs(state.failures));
}

/** Une lecture (une seule à la fois, partagée). */
export function refreshRenderStatus(): Promise<void> {
  inflight ??= realisticApi
    .status()
    .then(
      (status) => set(afterAnswer(status)),
      () => set(afterFailure(state)),
    )
    .finally(() => {
      inflight = null;
      schedule();
    });
  return inflight;
}

let watching = false;
function watch(): void {
  if (watching) return;
  watching = true;
  const again = () => {
    if (readers > 0) void refreshRenderStatus();
  };
  window.addEventListener("focus", again);
  window.addEventListener(LIVE_OPEN_EVENT, again);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") again();
  });
}

/** Le statut, relu à chaque montage (ouverture d'un node) et tenu à jour ensuite. KAN-134 :
 *  `read` faux (l'Assistant hors ligne) : le dernier statut connu, sans lecture. */
export function useRenderStatus(read = true): StatusState {
  useEffect(() => {
    if (!read) return;
    readers += 1;
    watch();
    void refreshRenderStatus();
    return () => {
      readers -= 1;
      if (readers === 0) schedule(); // plus personne : le nouvel essai s'arrête
    };
  }, [read]);
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
  );
}
