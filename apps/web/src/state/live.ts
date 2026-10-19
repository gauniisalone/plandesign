import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { setLiveConn } from "../api/client";

/** Présence en temps réel (B2a, note 14 § 7) : un WebSocket par projet ouvert, avec les
 *  comptes. Il reçoit qui est là et « le projet a changé ». Coupé (redémarrage du serveur,
 *  déploiement, réseau) : reconnexion avec un délai croissant et aléatoire, puis
 *  resynchronisation complète (présence, projet, espace de travail).
 *  B2b : il envoie aussi, sans rien écrire, mon curseur, ma sélection, qui je suis et (si
 *  quelqu'un me suit) ma vue ; il reçoit ceux des autres dans un petit magasin à part, pour que
 *  seuls les calques de présence se redessinent à chaque mouvement. */

export interface Peer {
  user_id: string;
  name: string;
  color: string;
}

export type LiveStatus = "off" | "connecting" | "open" | "reconnecting";

/** Vue d'une personne suivie : centre (coordonnées de l'espace de travail) et zoom. */
export interface PeerView {
  cx: number;
  cy: number;
  zoom: number;
}

type Message =
  | {
      type: "hello";
      conn: string;
      you: Peer;
      peers: Peer[];
      selections?: Record<string, string[]>;
      followed?: number;
    }
  | { type: "join"; peer: Peer }
  | { type: "leave"; user_id: string }
  | { type: "changed"; scope: "project" | "board" }
  | { type: "cursor"; user_id: string; x: number | null; y?: number }
  | { type: "select"; user_id: string; ids: string[] }
  | { type: "followed"; count: number }
  | ({ type: "view"; user_id: string } & PeerView)
  | { type: "pong" };

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 15_000;
/** Refus définitifs (origine, membre) : on ne réessaie pas ; 4401 : session perdue. */
const FINAL_CODES = new Set([4403, 4404]);
/** Derrière l'intergiciel, un refus de poignée de main n'est qu'un échec (HTTP 403, aucun code
 *  44xx) : après autant d'échecs d'affilée, on demande à /api/auth/me si la session vit. */
const FAILURES_BEFORE_CHECK = 2;
const CURSOR_EVERY_MS = 100; // 10 par seconde au plus
const VIEW_EVERY_MS = 250; // 4 par seconde au plus

/** Délai avant le n-ième essai : croissant, plafonné, et tiré au hasard (×0,5 à ×1,5) pour
 *  que tous les onglets ne reviennent pas à la même milliseconde après un redémarrage. */
export function retryDelay(attempt: number): number {
  const base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.round(base * (0.5 + Math.random()));
}

export interface PresenceSnapshot {
  cursors: Record<string, { x: number; y: number }>;
  selections: Record<string, string[]>;
  /** Dernière vue reçue de la personne que je suis. */
  view: PeerView | null;
}

const EMPTY: PresenceSnapshot = { cursors: {}, selections: {}, view: null };

/** Magasin de présence (B2b) : lu par `usePresence`, hors de l'état de l'atelier. */
export class PresenceStore {
  private snapshot: PresenceSnapshot = EMPTY;
  private listeners = new Set<() => void>();

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  get = (): PresenceSnapshot => this.snapshot;

  update(change: (s: PresenceSnapshot) => PresenceSnapshot): void {
    this.snapshot = change(this.snapshot);
    this.listeners.forEach((l) => l());
  }

  forget(userId: string): void {
    this.update((s) => {
      const cursors = { ...s.cursors };
      const selections = { ...s.selections };
      delete cursors[userId];
      delete selections[userId];
      return { ...s, cursors, selections };
    });
  }

  reset(): void {
    this.update(() => EMPTY);
  }
}

export function usePresence(
  store: PresenceStore | undefined,
): PresenceSnapshot {
  return useSyncExternalStore(
    store?.subscribe ?? noSubscribe,
    store?.get ?? emptySnapshot,
  );
}
const noSubscribe = () => () => undefined;
const emptySnapshot = () => EMPTY;

/** Ce que l'espace de travail envoie ; sans comptes, des fonctions qui ne font rien. */
export interface PresenceOut {
  store: PresenceStore;
  /** Mon curseur (coordonnées de l'espace de travail) ; null : hors du canevas. */
  cursor: (point: { x: number; y: number } | null) => void;
  select: (ids: string[]) => void;
  /** Ma vue, envoyée seulement si quelqu'un me suit. */
  view: (view: PeerView) => void;
  /** Je suis quelqu'un (null : personne). */
  following: string | null;
  follow: (userId: string | null) => void;
  /** Combien de personnes me suivent. */
  followed: number;
}

export function useLive(
  projectId: string,
  enabled: boolean,
  handlers: {
    /** Un autre membre (ou un autre onglet) a changé le projet ou le tableau. */
    onChanged: (scope: "project" | "board") => void;
    /** Reconnecté après une coupure : tout relire. */
    onResync: () => void;
    /** Session perdue (4401, ou /api/auth/me en 401 après des échecs de connexion). */
    onSignedOut: () => void;
  },
): {
  status: LiveStatus;
  peers: Peer[];
  me: Peer | null;
  presence: PresenceOut;
} {
  const [status, setStatus] = useState<LiveStatus>("off");
  const [peers, setPeers] = useState<Peer[]>([]);
  const [me, setMe] = useState<Peer | null>(null);
  const [following, setFollowing] = useState<string | null>(null);
  const [followed, setFollowed] = useState(0);
  const [store] = useState(() => new PresenceStore());
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });
  /** Envoi sur la connexion ouverte ; rien tant qu'elle ne l'est pas. */
  const sendRef = useRef<(message: object) => void>(() => undefined);
  // Ce que le serveur oublie à chaque coupure : renvoyé après chaque « hello ».
  const mine = useRef<{ selection: string[]; following: string | null }>({
    selection: [],
    following: null,
  });
  const followedRef = useRef(0);
  const throttles = useRef({
    cursor: throttle(CURSOR_EVERY_MS),
    view: throttle(VIEW_EVERY_MS),
  });

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let timer: number | null = null;
    let attempt = 0;
    let failures = 0;
    let stopped = false;
    let dropped = false;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/ws/projects/${projectId}`;

    const retry = () => {
      dropped = true;
      setStatus("reconnecting");
      timer = window.setTimeout(connect, retryDelay(attempt));
      attempt += 1;
    };

    const connect = () => {
      setStatus(dropped ? "reconnecting" : "connecting");
      let greeted = false;
      const current = new WebSocket(url);
      socket = current;
      current.onmessage = (event) => {
        let message: Message;
        try {
          message = JSON.parse(String(event.data)) as Message;
        } catch {
          return;
        }
        if (message.type === "hello") {
          greeted = true;
          attempt = 0;
          failures = 0;
          setLiveConn(message.conn);
          setMe(message.you);
          setPeers(message.peers);
          followedRef.current = message.followed ?? 0;
          setFollowed(followedRef.current);
          store.update(() => ({
            cursors: {},
            selections: message.selections ?? {},
            view: null,
          }));
          sendRef.current = (out) => {
            if (current.readyState === WebSocket.OPEN)
              current.send(JSON.stringify(out));
          };
          // Le serveur ne garde rien d'une connexion à l'autre : ma sélection, qui je suis.
          const { selection, following: target } = mine.current;
          if (selection.length)
            sendRef.current({ type: "select", ids: selection });
          if (target && message.peers.some((p) => p.user_id === target))
            sendRef.current({ type: "follow", user_id: target });
          setStatus("open");
          if (dropped) handlersRef.current.onResync();
          dropped = false;
        } else if (message.type === "join") {
          const peer = message.peer;
          setPeers((list) =>
            list.some((p) => p.user_id === peer.user_id)
              ? list
              : [...list, peer],
          );
        } else if (message.type === "leave") {
          const gone = message.user_id;
          setPeers((list) => list.filter((p) => p.user_id !== gone));
          store.forget(gone);
          if (mine.current.following === gone) {
            mine.current.following = null;
            setFollowing(null);
          }
        } else if (message.type === "changed") {
          handlersRef.current.onChanged(message.scope);
        } else if (message.type === "cursor") {
          const { user_id: who, x, y } = message;
          store.update((s) => {
            const cursors = { ...s.cursors };
            if (x === null || y === undefined) delete cursors[who];
            else cursors[who] = { x, y };
            return { ...s, cursors };
          });
        } else if (message.type === "select") {
          const { user_id: who, ids } = message;
          store.update((s) => ({
            ...s,
            selections: { ...s.selections, [who]: ids },
          }));
        } else if (message.type === "followed") {
          followedRef.current = message.count;
          setFollowed(message.count);
        } else if (message.type === "view") {
          if (message.user_id !== mine.current.following) return;
          const { cx, cy, zoom } = message;
          store.update((s) => ({ ...s, view: { cx, cy, zoom } }));
        }
      };
      current.onclose = (event) => {
        if (socket === current) {
          setLiveConn(null);
          sendRef.current = () => undefined;
        }
        if (stopped) return;
        if (event.code === 4401) {
          handlersRef.current.onSignedOut();
          return;
        }
        if (FINAL_CODES.has(event.code)) {
          setStatus("off");
          return;
        }
        if (!greeted) failures += 1;
        if (failures < FAILURES_BEFORE_CHECK) {
          retry();
          return;
        }
        // Plus de boucle aveugle : une session révoquée ramène à la connexion.
        failures = 0;
        void sessionAlive().then((alive) => {
          if (stopped) return;
          if (alive) retry();
          else handlersRef.current.onSignedOut();
        });
      };
    };
    connect();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      socket?.close();
      setLiveConn(null);
      sendRef.current = () => undefined;
      store.reset();
    };
  }, [projectId, enabled, store]);

  const [presence] = useState(() => ({
    store,
    cursor: (point: { x: number; y: number } | null) =>
      throttles.current.cursor(() =>
        sendRef.current(
          point
            ? { type: "cursor", x: round(point.x), y: round(point.y) }
            : { type: "cursor", x: null },
        ),
      ),
    select: (ids: string[]) => {
      mine.current.selection = ids;
      sendRef.current({ type: "select", ids });
    },
    view: (view: PeerView) => {
      if (followedRef.current === 0) return;
      throttles.current.view(() =>
        sendRef.current({
          type: "view",
          cx: round(view.cx),
          cy: round(view.cy),
          zoom: Math.round(view.zoom * 1000) / 1000,
        }),
      );
    },
    follow: (userId: string | null) => {
      mine.current.following = userId;
      setFollowing(userId);
      if (userId === null) store.update((s) => ({ ...s, view: null }));
      sendRef.current({ type: "follow", user_id: userId });
    },
  }));

  return {
    status: enabled ? status : "off",
    peers: enabled ? peers : [],
    me,
    presence: { ...presence, following, followed },
  };
}

/** /api/auth/me répond-il encore ? Une 401 dit « session révoquée » ; une panne réseau ou un
 *  serveur absent, non (on continue d'attendre). */
async function sessionAlive(): Promise<boolean> {
  try {
    const response = await fetch("/api/auth/me", {
      credentials: "same-origin",
    });
    return response.status !== 401;
  } catch {
    return true;
  }
}

function round(n: number): number {
  return Math.round(n * 10) / 10;
}

/** Au plus un appel par période : le premier part tout de suite, le dernier de la période
 *  part à sa fin (on n'en perd pas la position finale). */
function throttle(periodMs: number): (run: () => void) => void {
  let last = 0;
  let pending: (() => void) | null = null;
  let timer: number | null = null;
  return (run) => {
    const now = Date.now();
    if (now - last >= periodMs && timer === null) {
      last = now;
      run();
      return;
    }
    pending = run;
    if (timer !== null) return;
    timer = window.setTimeout(
      () => {
        timer = null;
        last = Date.now();
        const next = pending;
        pending = null;
        next?.();
      },
      Math.max(0, periodMs - (now - last)),
    );
  };
}
