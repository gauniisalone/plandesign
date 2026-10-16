import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  boardApi,
  type Board,
  type BoardItem,
  type BoardView,
} from "../api/client";

/** État réel de la sauvegarde du tableau (B2) : jamais « Enregistré » avant la réponse. */
export type BoardSave = "saved" | "saving" | "error";

const SAVE_DELAY_MS = 500;
const VIEW_DELAY_MS = 1200;
const HISTORY_MAX = 100;

/** Tableau du projet (B2) : chargé et resynchronisé quand le projet change (photo, rendu,
 *  plan), enregistré peu après chaque geste avec sa révision. Annuler / rétablir du tableau
 *  sont à lui, séparés de l'historique du plan. Un envoi refusé parce que le tableau a été
 *  enregistré ailleurs (409, deux onglets) recharge le tableau et le dit. */
export function useBoard(projectId: string, syncKey: string) {
  const [board, setBoard] = useState<Board | null>(null);
  const [save, setSave] = useState<BoardSave>("saved");
  const [notice, setNotice] = useState<string | null>(null);
  const [history, setHistory] = useState({ undo: 0, redo: 0 });
  const latest = useRef<Board | null>(null);
  const past = useRef<BoardItem[][]>([]);
  const future = useRef<BoardItem[][]>([]);
  const timer = useRef<number | null>(null);
  const inflight = useRef(false);
  const dirty = useRef(false);
  const again = useRef<() => void>(() => undefined);

  const counts = () =>
    setHistory({ undo: past.current.length, redo: future.current.length });

  const reload = useCallback(
    async (why: string | null = null) => {
      const fresh = await boardApi.get(projectId);
      latest.current = fresh;
      dirty.current = false;
      past.current = [];
      future.current = [];
      counts();
      setBoard(fresh);
      setSave("saved");
      if (why) setNotice(why);
    },
    [projectId],
  );

  // Chargement, puis resynchronisation quand le projet change, sauf geste en attente.
  useEffect(() => {
    let off = false;
    if (dirty.current || inflight.current) return;
    boardApi
      .get(projectId)
      .then((fresh) => {
        if (off || dirty.current || inflight.current) return;
        latest.current = fresh;
        setBoard(fresh);
      })
      .catch((e: unknown) => {
        if (!off) setNotice(`Tableau indisponible : ${String(e)}`);
      });
    return () => {
      off = true;
    };
  }, [projectId, syncKey]);

  const flush = useCallback(async () => {
    timer.current = null;
    const sent = latest.current;
    if (!sent || inflight.current) return;
    inflight.current = true;
    dirty.current = false;
    setSave("saving");
    try {
      const saved = await boardApi.put(projectId, sent);
      // Les gestes faits pendant l'envoi restent ; seule la révision avance.
      const current = latest.current ?? saved;
      latest.current = { ...current, revision: saved.revision };
      setBoard(latest.current);
      inflight.current = false;
      // Un geste fait pendant l'envoi part à son tour.
      if (dirty.current) again.current();
      else setSave("saved");
    } catch (e) {
      inflight.current = false;
      if (e instanceof ApiError && e.status === 409) {
        await reload(
          "Le tableau a été enregistré ailleurs (un autre onglet ?) : il est rechargé, votre dernier geste n'y est pas.",
        ).catch(() => setSave("error"));
        return;
      }
      dirty.current = true;
      setSave("error");
    }
  }, [projectId, reload]);
  useEffect(() => {
    again.current = () => void flush();
  }, [flush]);

  const schedule = useCallback(
    (delay: number) => {
      dirty.current = true;
      setSave("saving");
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void flush(), delay);
    },
    [flush],
  );

  // Un geste en attente part tout de suite si l'on quitte l'écran, et quand la page se
  // cache ou se ferme (B4, demande du PO) : `keepalive`, sans attendre de réponse.
  useEffect(() => {
    const onHide = () => {
      if (document.visibilityState !== "hidden" && timer.current === null)
        return;
      if (!dirty.current || !latest.current) return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      dirty.current = false;
      boardApi.putKeepalive(projectId, latest.current);
    };
    document.addEventListener("visibilitychange", onHide);
    window.addEventListener("pagehide", onHide);
    return () => {
      document.removeEventListener("visibilitychange", onHide);
      window.removeEventListener("pagehide", onHide);
    };
  }, [projectId]);
  useEffect(
    () => () => {
      if (timer.current !== null) {
        window.clearTimeout(timer.current);
        void flush();
      }
    },
    [flush],
  );

  /** Un geste sur le tableau : enregistré, et annulable (sauf `record: false`). */
  const commit = useCallback(
    (items: BoardItem[], record = true) => {
      const current = latest.current;
      if (!current) return;
      if (record) {
        past.current = [...past.current, current.items].slice(-HISTORY_MAX);
        future.current = [];
        counts();
      }
      latest.current = { ...current, items };
      setBoard(latest.current);
      schedule(SAVE_DELAY_MS);
    },
    [schedule],
  );

  const setView = useCallback(
    (view: BoardView) => {
      const current = latest.current;
      if (!current) return;
      latest.current = { ...current, view };
      setBoard(latest.current);
      schedule(VIEW_DELAY_MS);
    },
    [schedule],
  );

  const undo = useCallback(() => {
    const current = latest.current;
    const previous = past.current.at(-1);
    if (!current || !previous) return;
    past.current = past.current.slice(0, -1);
    future.current = [...future.current, current.items];
    counts();
    latest.current = { ...current, items: previous };
    setBoard(latest.current);
    schedule(SAVE_DELAY_MS);
  }, [schedule]);

  const redo = useCallback(() => {
    const current = latest.current;
    const next = future.current.at(-1);
    if (!current || !next) return;
    future.current = future.current.slice(0, -1);
    past.current = [...past.current, current.items];
    counts();
    latest.current = { ...current, items: next };
    setBoard(latest.current);
    schedule(SAVE_DELAY_MS);
  }, [schedule]);

  return {
    board,
    save,
    notice,
    canUndo: history.undo > 0,
    canRedo: history.redo > 0,
    commit,
    setView,
    undo,
    redo,
    retry: () => void flush(),
    dismissNotice: () => setNotice(null),
  };
}
