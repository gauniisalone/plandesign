import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { bellApi, type BellLine } from "../api/client";
import { retryDelay } from "./live";

/** KAN-52 : la cloche. Le compteur se relit à l'ouverture, au retour sur l'onglet et, avec
 *  les comptes, à chaque « bell » du canal de mon compte (`/ws/me`, en direct, sur tous mes
 *  appareils). En local, pas de WebSocket : une relecture légère toutes les 15 s tient lieu
 *  de direct (le serveur est sur la machine). */

const LOCAL_REFRESH_MS = 15_000;

export function useBell(accounts: boolean): {
  unread: number;
  /** Change à chaque avis : le panneau ouvert se relit. */
  tick: number;
  refresh: () => void;
} {
  const [unread, setUnread] = useState(0);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => {
    void bellApi.count().then(
      (r) => setUnread(r.unread),
      () => undefined,
    );
    setTick((n) => n + 1);
  }, []);

  useEffect(() => {
    // À l'ouverture : le compteur seul (le panneau, fermé, n'a rien à relire).
    void bellApi.count().then(
      (r) => setUnread(r.unread),
      () => undefined,
    );
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", refresh);
    let stopped = false;
    let socket: WebSocket | null = null;
    let timer: number | null = null;
    let attempt = 0;
    if (accounts) {
      const scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const url = `${scheme}://${window.location.host}/ws/me`;
      const connect = () => {
        const current = new WebSocket(url);
        socket = current;
        current.onmessage = (event) => {
          if (String(event.data).includes('"bell"')) refresh();
          else if (String(event.data).includes('"hello"')) {
            // (Re)connecté : ce qui a pu arriver pendant la coupure.
            if (attempt > 0) refresh();
            attempt = 0;
          }
        };
        current.onclose = (event) => {
          // Session perdue ou autre site : rien à retenter (la page de connexion suit).
          if (stopped || event.code === 4401 || event.code === 4403) return;
          timer = window.setTimeout(connect, retryDelay(attempt));
          attempt += 1;
        };
      };
      connect();
    } else {
      timer = window.setInterval(refresh, LOCAL_REFRESH_MS);
    }
    return () => {
      stopped = true;
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", refresh);
      if (timer !== null) {
        window.clearTimeout(timer);
        window.clearInterval(timer);
      }
      socket?.close();
    };
  }, [accounts, refresh]);

  return { unread, tick, refresh };
}

/** Où mène une ligne (MH4) : lu par l'atelier du projet, une fois. */
export interface BellTarget {
  projectId: string;
  threadId: string | null;
  itemId: string | null;
  levelId: string | null;
  runId: string | null;
  plan: boolean;
  token: number;
}

let target: BellTarget | null = null;
let tokens = 0;
const listeners = new Set<() => void>();

export function setBellTarget(line: BellLine): void {
  tokens += 1;
  target = {
    projectId: line.project_id,
    threadId: line.thread_id,
    itemId: line.item_id,
    levelId: line.level_id,
    runId: line.run_id,
    plan: line.type === "plan.changed",
    token: tokens,
  };
  listeners.forEach((l) => l());
}

export function takeBellTarget(projectId: string): BellTarget | null {
  if (!target || target.projectId !== projectId) return null;
  const taken = target;
  target = null;
  return taken;
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Le jeton de la dernière ligne cliquée (pour relancer l'effet de l'atelier). */
export function useBellTargetToken(): number {
  return useSyncExternalStore(subscribe, () => target?.token ?? 0);
}
