import { useCallback, useEffect, useRef, useState } from "react";
import {
  boardApi,
  type Board,
  type BoardItem,
  type BoardPatch,
  type BoardView,
} from "../api/client";

/** État réel de la sauvegarde du tableau (B2) : jamais « Enregistré » avant la réponse. */
export type BoardSave = "saved" | "saving" | "error";

const SAVE_DELAY_MS = 500;
const VIEW_DELAY_MS = 1200;
const HISTORY_MAX = 100;
const NOT_RECREATED =
  "Un élément supprimé par quelqu'un d'autre n'a pas été recréé.";

/** Écritures en attente, par élément : l'élément tel qu'il doit être, ou null (retiré). */
type Ops = Map<string, BoardItem | null>;
/** Un geste annulable : chaque élément touché, avant et après (null : absent). */
interface Gesture {
  before: Ops;
  after: Ops;
}

/** Ce que le projet fournit (comme `AUTO_KINDS` côté serveur) : une carte par image. */
const PROVIDED = new Set([
  "photo",
  "render",
  "plan",
  "file",
  "video",
  "pdf",
  "contextual",
]);
const providedKey = (i: BoardItem) =>
  PROVIDED.has(i.kind) && i.ref ? `${i.kind}:${i.ref}` : null;

/** Le tableau du serveur, avec mes écritures pas encore confirmées par-dessus. */
function withOps(board: Board, ...layers: Ops[]): Board {
  const items = new Map(board.items.map((i) => [i.id, i]));
  for (const ops of layers)
    for (const [id, item] of ops) {
      if (item === null) items.delete(id);
      else items.set(id, item);
    }
  return { ...board, items: [...items.values()] };
}

function toPatch(ops: Ops, view: BoardView | null): BoardPatch {
  const patch: BoardPatch = { set: [], delete: [] };
  for (const [id, item] of ops) {
    if (item === null) patch.delete.push(id);
    else patch.set.push(item);
  }
  if (view) patch.view = view;
  return patch;
}

/** Tableau du projet (B2, B2c) : chargé et resynchronisé quand le projet change (photo,
 *  rendu, plan) ou qu'un autre membre l'a écrit. Chaque geste n'envoie que les éléments
 *  qu'il touche (dernière écriture gagne, élément par élément) : deux personnes qui
 *  déplacent deux cartes différentes ne se gênent plus. Mes écritures pas encore confirmées
 *  restent par-dessus chaque relecture. Annuler / rétablir sont à chacun : ils réécrivent
 *  l'état d'avant ou d'après de mes gestes, comme de nouvelles écritures visibles des
 *  autres ; un élément supprimé entre-temps par quelqu'un d'autre n'est pas recréé. */
export function useBoard(projectId: string, syncKey: string) {
  const [board, setBoard] = useState<Board | null>(null);
  const [save, setSave] = useState<BoardSave>("saved");
  const [notice, setNotice] = useState<string | null>(null);
  const [history, setHistory] = useState({ undo: 0, redo: 0 });
  const server = useRef<Board | null>(null);
  const latest = useRef<Board | null>(null);
  /** Objets d'élément déjà connus (du serveur ou d'un geste) : un geste qui les renvoie
   *  tels quels ne les a pas touchés. */
  const known = useRef(new WeakSet<BoardItem>());
  /** Ids déjà vus (du serveur ou de mes gestes), et ceux des éléments que j'ai créés. */
  const seen = useRef(new Set<string>());
  const created = useRef(new Set<string>());
  const pending = useRef<Ops>(new Map());
  const pendingView = useRef<BoardView | null>(null);
  const inflight = useRef<Ops | null>(null);
  const past = useRef<Gesture[]>([]);
  const future = useRef<Gesture[]>([]);
  const timer = useRef<number | null>(null);
  const again = useRef<() => void>(() => undefined);

  const counts = () =>
    setHistory({ undo: past.current.length, redo: future.current.length });

  /** Montre le tableau du serveur avec mes écritures en cours par-dessus. Une carte de
   *  photo (rendu, plan…) jamais vue, arrivée avec la relecture alors que je venais de poser
   *  la mienne pour la même image (dépôt d'une photo), est le doublon de la synchronisation :
   *  elle est retirée. Une copie « Dupliquer », déjà vue, ne l'est jamais. */
  const show = useCallback((fresh: Board) => {
    const mineByKey = new Map<string, string>();
    const candidates: (BoardItem | null)[] = [
      ...fresh.items,
      ...(inflight.current?.values() ?? []),
      ...pending.current.values(),
    ];
    for (const item of candidates) {
      if (!item || !created.current.has(item.id)) continue;
      const key = providedKey(item);
      if (key) mineByKey.set(key, item.id);
    }
    const doubles = fresh.items.filter((i) => {
      const key = providedKey(i);
      const mine = key ? mineByKey.get(key) : undefined;
      return !seen.current.has(i.id) && mine !== undefined && mine !== i.id;
    });
    for (const i of fresh.items) seen.current.add(i.id);
    for (const i of doubles) pending.current.set(i.id, null);
    if (doubles.length) window.setTimeout(() => again.current(), 0);
    server.current = fresh;
    const view = pendingView.current;
    const shown = withOps(
      view ? { ...fresh, view } : fresh,
      inflight.current ?? new Map(),
      pending.current,
    );
    for (const i of shown.items) known.current.add(i);
    latest.current = shown;
    setBoard(shown);
  }, []);

  // Chargement, puis relecture quand le projet change ou qu'un autre membre a écrit ; mes
  // écritures en attente restent par-dessus.
  useEffect(() => {
    let off = false;
    boardApi
      .get(projectId)
      .then((fresh) => {
        if (!off) show(fresh);
      })
      .catch((e: unknown) => {
        if (!off) setNotice(`Espace de travail indisponible : ${String(e)}`);
      });
    return () => {
      off = true;
    };
  }, [projectId, syncKey, show]);

  const flush = useCallback(async () => {
    timer.current = null;
    if (inflight.current) return;
    const ops = pending.current;
    const view = pendingView.current;
    if (ops.size === 0 && !view) {
      setSave("saved");
      return;
    }
    pending.current = new Map();
    pendingView.current = null;
    inflight.current = ops;
    setSave("saving");
    try {
      const saved = await boardApi.patch(projectId, toPatch(ops, view));
      inflight.current = null;
      show(saved);
      // Un geste fait pendant l'envoi part à son tour.
      if (pending.current.size || pendingView.current) again.current();
      else setSave("saved");
    } catch {
      // Rien n'est perdu : ces écritures repartiront, sous celles faites depuis.
      inflight.current = null;
      pending.current = new Map([...ops, ...pending.current]);
      pendingView.current = pendingView.current ?? view;
      setSave("error");
    }
  }, [projectId, show]);
  useEffect(() => {
    again.current = () => void flush();
  }, [flush]);

  const schedule = useCallback(
    (delay: number) => {
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
      const ops = pending.current;
      const view = pendingView.current;
      if (ops.size === 0 && !view) return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      pending.current = new Map();
      pendingView.current = null;
      boardApi.patchKeepalive(projectId, toPatch(ops, view));
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

  /** Applique des écritures à l'écran et les met en attente d'envoi. */
  const write = useCallback(
    (ops: Ops) => {
      const current = latest.current;
      if (!current || ops.size === 0) return;
      for (const [id, item] of ops) {
        pending.current.set(id, item);
        seen.current.add(id);
        if (item) known.current.add(item);
      }
      latest.current = withOps(current, ops);
      setBoard(latest.current);
      schedule(SAVE_DELAY_MS);
    },
    [schedule],
  );

  /** Un geste sur le tableau (la liste entière, comme l'espace de travail la voit) : seuls
   *  les éléments qu'il a créés, changés ou retirés partent ; annulable (sauf `record: false`). */
  const commit = useCallback(
    (items: BoardItem[], record = true) => {
      const current = latest.current;
      if (!current) return;
      const before: Ops = new Map();
      const after: Ops = new Map();
      const now = new Map(current.items.map((i) => [i.id, i]));
      const next = new Set<string>();
      for (const item of items) {
        next.add(item.id);
        if (known.current.has(item)) continue; // renvoyé tel quel : pas touché
        if (!now.has(item.id)) created.current.add(item.id);
        before.set(item.id, now.get(item.id) ?? null);
        after.set(item.id, item);
      }
      for (const [id, item] of now)
        if (!next.has(id)) {
          before.set(id, item);
          after.set(id, null);
        }
      if (after.size === 0) return;
      if (record) {
        past.current = [...past.current, { before, after }].slice(-HISTORY_MAX);
        future.current = [];
        counts();
      }
      write(after);
    },
    [write],
  );

  const setView = useCallback(
    (view: BoardView) => {
      const current = latest.current;
      if (!current) return;
      pendingView.current = view;
      latest.current = { ...current, view };
      setBoard(latest.current);
      schedule(VIEW_DELAY_MS);
    },
    [schedule],
  );

  /** Réécrit un état de mes éléments (avant ou après un geste). Un élément que j'avais
   *  changé (pas supprimé) et que quelqu'un d'autre a supprimé depuis n'est pas recréé. */
  const replay = useCallback(
    (target: Ops, other: Ops) => {
      const current = latest.current;
      if (!current) return;
      const here = new Set(current.items.map((i) => i.id));
      const ops: Ops = new Map();
      let skipped = false;
      for (const [id, item] of target) {
        const gone = !here.has(id) && other.get(id) !== null;
        if (item !== null && gone) {
          skipped = true;
          continue;
        }
        if (item === null && !here.has(id)) continue;
        ops.set(id, item);
      }
      if (skipped) setNotice(NOT_RECREATED);
      write(ops);
    },
    [write],
  );

  const undo = useCallback(() => {
    const gesture = past.current.at(-1);
    if (!gesture) return;
    past.current = past.current.slice(0, -1);
    future.current = [...future.current, gesture];
    counts();
    replay(gesture.before, gesture.after);
  }, [replay]);

  const redo = useCallback(() => {
    const gesture = future.current.at(-1);
    if (!gesture) return;
    future.current = future.current.slice(0, -1);
    past.current = [...past.current, gesture];
    counts();
    replay(gesture.after, gesture.before);
  }, [replay]);

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
