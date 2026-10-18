import { useEffect, useRef, useState } from "react";
import { setLiveConn } from "../api/client";

/** Présence en temps réel (B2a, note 14 § 7) : un WebSocket par projet ouvert, avec les
 *  comptes. Il ne sert qu'à recevoir : qui est là, et « le projet a changé ». Coupé
 *  (redémarrage du serveur, déploiement, réseau) : reconnexion avec un délai croissant et
 *  aléatoire, puis resynchronisation complète (présence, projet, espace de travail). */

export interface Peer {
  user_id: string;
  name: string;
  color: string;
}

export type LiveStatus = "off" | "connecting" | "open" | "reconnecting";

type Message =
  | { type: "hello"; conn: string; you: Peer; peers: Peer[] }
  | { type: "join"; peer: Peer }
  | { type: "leave"; user_id: string }
  | { type: "changed"; scope: "project" | "board" }
  | { type: "pong" };

const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 15_000;
/** Refus définitifs (origine, membre) : on ne réessaie pas ; 4401 : session perdue. */
const FINAL_CODES = new Set([4403, 4404]);

/** Délai avant le n-ième essai : croissant, plafonné, et tiré au hasard (×0,5 à ×1,5) pour
 *  que tous les onglets ne reviennent pas à la même milliseconde après un redémarrage. */
export function retryDelay(attempt: number): number {
  const base = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.round(base * (0.5 + Math.random()));
}

export function useLive(
  projectId: string,
  enabled: boolean,
  handlers: {
    /** Un autre membre (ou un autre onglet) a changé le projet ou le tableau. */
    onChanged: (scope: "project" | "board") => void;
    /** Reconnecté après une coupure : tout relire. */
    onResync: () => void;
    /** Session perdue (4401). */
    onSignedOut: () => void;
  },
): { status: LiveStatus; peers: Peer[]; me: Peer | null } {
  const [status, setStatus] = useState<LiveStatus>("off");
  const [peers, setPeers] = useState<Peer[]>([]);
  const [me, setMe] = useState<Peer | null>(null);
  const handlersRef = useRef(handlers);
  useEffect(() => {
    handlersRef.current = handlers;
  });

  useEffect(() => {
    if (!enabled) return;
    let socket: WebSocket | null = null;
    let timer: number | null = null;
    let attempt = 0;
    let stopped = false;
    let dropped = false;
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${window.location.host}/ws/projects/${projectId}`;

    const connect = () => {
      setStatus(dropped ? "reconnecting" : "connecting");
      socket = new WebSocket(url);
      socket.onmessage = (event) => {
        let message: Message;
        try {
          message = JSON.parse(String(event.data)) as Message;
        } catch {
          return;
        }
        if (message.type === "hello") {
          attempt = 0;
          setLiveConn(message.conn);
          setMe(message.you);
          setPeers(message.peers);
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
        } else if (message.type === "changed") {
          handlersRef.current.onChanged(message.scope);
        }
      };
      socket.onclose = (event) => {
        setLiveConn(null);
        if (stopped) return;
        if (event.code === 4401) {
          handlersRef.current.onSignedOut();
          return;
        }
        if (FINAL_CODES.has(event.code)) {
          setStatus("off");
          return;
        }
        dropped = true;
        setStatus("reconnecting");
        timer = window.setTimeout(connect, retryDelay(attempt));
        attempt += 1;
      };
    };
    connect();
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      socket?.close();
      setLiveConn(null);
    };
  }, [projectId, enabled]);

  return { status: enabled ? status : "off", peers: enabled ? peers : [], me };
}
