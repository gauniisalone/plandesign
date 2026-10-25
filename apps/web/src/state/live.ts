import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { setLiveConn } from "../api/client";
import { tuned } from "../netTuning";

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

/** B2d : qui tient un plan (le bail d'édition), et jusqu'à quand (horloge de l'onglet). */
export interface PlanLease {
  holder: Peer;
  expiresAt: number;
}

type LeaseMessage = {
  type: "lease";
  level_id: string;
  holder: Peer | null;
  expires_in?: number;
};

type Message =
  | {
      type: "hello";
      conn: string;
      you: Peer;
      peers: Peer[];
      selections?: Record<string, string[]>;
      followed?: number;
      leases?: Record<string, LeaseMessage>;
    }
  | LeaseMessage
  | { type: "lease.request"; level_id: string; from: string }
  | { type: "join"; peer: Peer }
  | { type: "leave"; user_id: string }
  | {
      type: "changed";
      /** KAN-78 : « reactions », les pastilles seulement. */
      scope: "project" | "board" | "comments" | "reactions";
    }
  | {
      type: "cursor";
      user_id: string;
      x: number | null;
      y?: number;
      level_id?: string;
    }
  | { type: "select"; user_id: string; ids: string[] }
  | { type: "followed"; count: number }
  | ({ type: "view"; user_id: string } & PeerView)
  | { type: "pong" };

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 30_000;
/** Connexion lente (25 oct.) : une poignée de main sans « hello » dans ce délai est
 *  abandonnée et retentée (avant, elle pouvait rester ouverte pour toujours). */
export const HELLO_TIMEOUT_MS = 20_000;
/** Battement de cœur : un ping toutes les 25 s ; sans aucun message pendant 60 s, la
 *  connexion est tenue pour morte et refaite. */
export const PING_EVERY_MS = 25_000;
export const SILENCE_MS = 60_000;
/** Après autant d'essais ratés d'affilée, le message « Connexion instable » remplace
 *  « Reconnexion… ». */
export const UNSTABLE_AFTER = 4;
/** Vérification de session (après des échecs) : jamais plus longue que cela. */
const SESSION_CHECK_MS = 10_000;
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
  /** B2d : curseurs sur un plan (onglet Plan), en mètres. */
  planCursors: Record<string, { x: number; y: number; level: string }>;
  selections: Record<string, string[]>;
  /** Dernière vue reçue de la personne que je suis. */
  view: PeerView | null;
}

const EMPTY: PresenceSnapshot = {
  cursors: {},
  planCursors: {},
  selections: {},
  view: null,
};

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
      const planCursors = { ...s.planCursors };
      const selections = { ...s.selections };
      delete cursors[userId];
      delete planCursors[userId];
      delete selections[userId];
      return { ...s, cursors, planCursors, selections };
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
  /** B2d : mon curseur sur un plan, en mètres ; null : hors du plan. */
  planCursor: (level: string, point: { x: number; y: number } | null) => void;
  /** B2d : les plans tenus (bail d'édition), et la dernière demande de main reçue. */
  leases: Record<string, PlanLease>;
  leaseRequest: { level: string; from: string } | null;
  clearLeaseRequest: () => void;
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
    /** Un autre membre (ou un autre onglet) a changé le projet, le tableau ou (KAN-51) les
     *  commentaires. */
    onChanged: (scope: "project" | "board" | "comments" | "reactions") => void;
    /** Reconnecté après une coupure : tout relire. */
    onResync: () => void;
    /** Session perdue (4401, ou /api/auth/me en 401 après des échecs de connexion). */
    onSignedOut: () => void;
  },
): {
  status: LiveStatus;
  /** Connexion lente (25 oct.) : plusieurs reconnexions ratées d'affilée. */
  unstable: boolean;
  peers: Peer[];
  me: Peer | null;
  presence: PresenceOut;
} {
  const [status, setStatus] = useState<LiveStatus>("off");
  const [unstable, setUnstable] = useState(false);
  const [peers, setPeers] = useState<Peer[]>([]);
  const [me, setMe] = useState<Peer | null>(null);
  const [following, setFollowing] = useState<string | null>(null);
  const [followed, setFollowed] = useState(0);
  const [leases, setLeases] = useState<Record<string, PlanLease>>({});
  const [leaseRequest, setLeaseRequest] = useState<{
    level: string;
    from: string;
  } | null>(null);
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
    let helloTimer: number | null = null;
    let heartbeat: number | null = null;
    let lastSeen = Date.now();
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/ws/projects/${projectId}`;

    const clearTimers = () => {
      if (helloTimer !== null) window.clearTimeout(helloTimer);
      if (heartbeat !== null) window.clearInterval(heartbeat);
      helloTimer = heartbeat = null;
    };

    const retry = () => {
      dropped = true;
      setStatus("reconnecting");
      setUnstable(attempt >= tuned("unstableAfter", UNSTABLE_AFTER));
      if (timer !== null) window.clearTimeout(timer);
      timer = window.setTimeout(connect, retryDelay(attempt));
      attempt += 1;
    };

    /** La connexion est perdue (fermée par le serveur, poignée de main sans fin, silence) :
     *  réessai borné, vérification de la session après des échecs d'affilée. */
    const lost = (code: number, greeted: boolean) => {
      clearTimers();
      if (stopped) return;
      if (code === 4401) {
        handlersRef.current.onSignedOut();
        return;
      }
      if (FINAL_CODES.has(code)) {
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

    /** Abandonne une connexion qui ne répond plus, sans attendre sa fermeture (sur un réseau
     *  mort, la fermeture elle-même peut ne jamais aboutir). */
    const abandon = (current: WebSocket, greeted: boolean) => {
      if (socket !== current) return;
      socket = null;
      current.onclose = null;
      current.onmessage = null;
      try {
        current.close();
      } catch {
        /* déjà fermée */
      }
      setLiveConn(null);
      sendRef.current = () => undefined;
      lost(1006, greeted);
    };

    const connect = () => {
      timer = null;
      setStatus(dropped ? "reconnecting" : "connecting");
      let greeted = false;
      const current = new WebSocket(url);
      socket = current;
      clearTimers();
      helloTimer = window.setTimeout(
        () => abandon(current, false),
        tuned("helloMs", HELLO_TIMEOUT_MS),
      );
      current.onmessage = (event) => {
        lastSeen = Date.now();
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
          setUnstable(false);
          if (helloTimer !== null) window.clearTimeout(helloTimer);
          helloTimer = null;
          heartbeat = window.setInterval(
            () => {
              if (Date.now() - lastSeen > tuned("silenceMs", SILENCE_MS)) {
                abandon(current, true);
                return;
              }
              if (current.readyState === WebSocket.OPEN)
                current.send('{"type":"ping"}');
            },
            tuned("pingMs", PING_EVERY_MS),
          );
          setLiveConn(message.conn);
          setMe(message.you);
          setPeers(message.peers);
          followedRef.current = message.followed ?? 0;
          setFollowed(followedRef.current);
          store.update(() => ({
            cursors: {},
            planCursors: {},
            selections: message.selections ?? {},
            view: null,
          }));
          setLeases(
            Object.values(message.leases ?? {}).reduce(
              (all, l) => withLease(all, l),
              {} as Record<string, PlanLease>,
            ),
          );
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
          const { user_id: who, x, y, level_id: level } = message;
          store.update((s) => {
            const cursors = { ...s.cursors };
            const planCursors = { ...s.planCursors };
            delete cursors[who];
            delete planCursors[who];
            if (x !== null && y !== undefined) {
              if (level) planCursors[who] = { x, y, level };
              else cursors[who] = { x, y };
            }
            return { ...s, cursors, planCursors };
          });
        } else if (message.type === "lease") {
          const lease = message;
          setLeases((all) => withLease(all, lease));
        } else if (message.type === "lease.request") {
          setLeaseRequest({ level: message.level_id, from: message.from });
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
        if (socket !== current) return; // déjà abandonnée
        socket = null;
        setLiveConn(null);
        sendRef.current = () => undefined;
        lost(event.code, greeted);
      };
    };
    // Le réseau revient : on n'attend pas la fin du recul.
    const online = () => {
      if (stopped || socket !== null || timer === null) return;
      window.clearTimeout(timer);
      connect();
    };
    window.addEventListener("online", online);
    connect();
    return () => {
      stopped = true;
      window.removeEventListener("online", online);
      clearTimers();
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
    // B2d : sur un plan, en mètres (au centimètre), avec l'id du plan ; même débit.
    planCursor: (level: string, point: { x: number; y: number } | null) =>
      throttles.current.cursor(() =>
        sendRef.current(
          point
            ? {
                type: "cursor",
                x: Math.round(point.x * 100) / 100,
                y: Math.round(point.y * 100) / 100,
                level_id: level,
              }
            : { type: "cursor", x: null },
        ),
      ),
    clearLeaseRequest: () => setLeaseRequest(null),
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
    unstable: enabled && status === "reconnecting" && unstable,
    peers: enabled ? peers : [],
    me,
    presence: { ...presence, following, followed, leases, leaseRequest },
  };
}

/** Un message « lease » appliqué aux baux connus (holder nul : le plan est libre). */
function withLease(
  all: Record<string, PlanLease>,
  message: LeaseMessage,
): Record<string, PlanLease> {
  const next = { ...all };
  if (message.holder === null) delete next[message.level_id];
  else
    next[message.level_id] = {
      holder: message.holder,
      expiresAt: Date.now() + (message.expires_in ?? 0) * 1000,
    };
  return next;
}

/** /api/auth/me répond-il encore ? Une 401 dit « session révoquée » ; une panne réseau ou un
 *  serveur absent, non (on continue d'attendre). */
async function sessionAlive(): Promise<boolean> {
  // Connexion lente (25 oct.) : sans réponse en 10 s, on continue d'attendre (réessai), au
  // lieu de rester suspendu à une requête qui ne revient pas.
  const stop = new AbortController();
  const timer = window.setTimeout(() => stop.abort(), SESSION_CHECK_MS);
  try {
    const response = await fetch("/api/auth/me", {
      credentials: "same-origin",
      signal: stop.signal,
    });
    return response.status !== 401;
  } catch {
    return true;
  } finally {
    window.clearTimeout(timer);
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
