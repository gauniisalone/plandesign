import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  boardApi,
  type Board,
  type BoardItem,
  type BoardLink,
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
/** Un geste annulable : chaque élément touché, avant et après (null : absent). KAN-111 : les
 *  liens qu'il a posés (un node lâché sur un autre) ; annuler retire le node, et le serveur
 *  retire avec lui ses liens ; rétablir repose le node puis ses liens. */
interface Gesture {
  before: Ops;
  after: Ops;
  links?: BoardLink[];
}

/** KAN-111 : les liens pas encore confirmés par-dessus ceux du serveur (même paire : le mien). */
function withLinks(board: Board, links: BoardLink[]): Board {
  if (!links.length) return board;
  const pairs = new Set(links.map((l) => `${l.source}>${l.target}`));
  const ids = new Set(links.map((l) => l.id));
  return {
    ...board,
    links: [
      ...(board.links ?? []).filter(
        (l) => !ids.has(l.id) && !pairs.has(`${l.source}>${l.target}`),
      ),
      ...links,
    ],
  };
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

function toPatch(
  ops: Ops,
  view: BoardView | null,
  links: BoardLink[] = [],
): BoardPatch {
  const patch: BoardPatch = { set: [], delete: [] };
  for (const [id, item] of ops) {
    if (item === null) patch.delete.push(id);
    else patch.set.push(item);
  }
  if (view) patch.view = view;
  // KAN-111 : dans le même envoi que les nodes ; le serveur pose les nodes d'abord.
  if (links.length) patch.links_set = links;
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
  /** KAN-111 : liens d'un geste, envoyés avec ses nodes (en attente, puis en vol). */
  const pendingLinks = useRef<BoardLink[]>([]);
  const inflightLinks = useRef<BoardLink[]>([]);
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
    const shown = withLinks(
      withOps(
        view ? { ...fresh, view } : fresh,
        inflight.current ?? new Map(),
        pending.current,
      ),
      [...inflightLinks.current, ...pendingLinks.current],
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
    // KAN-43 : ma vue part à part (PUT …/view, lecteur compris) ; elle ne touche ni le
    // tableau ni les autres. Une vue perdue n'est pas grave : la suivante la remplace.
    const view = pendingView.current;
    pendingView.current = null;
    if (view) void boardApi.putView(projectId, view).catch(() => undefined);
    const ops = pending.current;
    const links = pendingLinks.current;
    if (ops.size === 0 && links.length === 0) {
      setSave("saved");
      return;
    }
    pending.current = new Map();
    pendingLinks.current = [];
    inflight.current = ops;
    inflightLinks.current = links;
    setSave("saving");
    try {
      const saved = await boardApi.patch(projectId, toPatch(ops, null, links));
      inflight.current = null;
      inflightLinks.current = [];
      show(saved);
      // Un geste fait pendant l'envoi part à son tour.
      if (
        pending.current.size ||
        pendingLinks.current.length ||
        pendingView.current
      )
        again.current();
      else setSave("saved");
    } catch (e) {
      inflight.current = null;
      inflightLinks.current = [];
      if (e instanceof ApiError && (e.status === 409 || e.status === 422)) {
        // KAN-91 : un geste refusé par une règle (une carte qui n'accepte pas cet élément) ne
        // repart pas en boucle : il est retiré, la raison est dite, le tableau est relu.
        setNotice(e.message);
        setSave("saved");
        void boardApi.get(projectId).then(show, () => undefined);
        return;
      }
      // Rien n'est perdu : ces écritures repartiront, sous celles faites depuis.
      pending.current = new Map([...ops, ...pending.current]);
      pendingLinks.current = [...links, ...pendingLinks.current];
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
      const links = pendingLinks.current;
      const view = pendingView.current;
      if (ops.size === 0 && links.length === 0 && !view) return;
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      pending.current = new Map();
      pendingLinks.current = [];
      pendingView.current = null;
      if (view) boardApi.putViewKeepalive(projectId, view);
      if (ops.size || links.length)
        boardApi.patchKeepalive(projectId, toPatch(ops, null, links));
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

  /** Applique des écritures à l'écran et les met en attente d'envoi. KAN-111 : avec des
   *  liens, tout part sans attendre, dans un seul envoi ; un élément retiré emporte, à
   *  l'écran, les liens qui le touchent (le serveur fait de même). */
  const write = useCallback(
    (ops: Ops, links: BoardLink[] = [], soon = false) => {
      const current = latest.current;
      if (!current || (ops.size === 0 && links.length === 0)) return;
      for (const [id, item] of ops) {
        pending.current.set(id, item);
        seen.current.add(id);
        if (item) known.current.add(item);
      }
      pendingLinks.current = [...pendingLinks.current, ...links];
      const gone = new Set(
        [...ops].filter(([, item]) => item === null).map(([id]) => id),
      );
      const shown = withLinks(withOps(current, ops), links);
      latest.current = gone.size
        ? {
            ...shown,
            links: (shown.links ?? []).filter(
              (l) => !gone.has(l.source) && !gone.has(l.target),
            ),
          }
        : shown;
      setBoard(latest.current);
      schedule(links.length || soon ? 0 : SAVE_DELAY_MS);
    },
    [schedule],
  );

  /** Un geste sur le tableau (la liste entière, comme l'espace de travail la voit) : seuls
   *  les éléments qu'il a créés, changés ou retirés partent ; annulable (sauf `record: false`).
   *  KAN-111 : `links`, les liens que le geste pose avec ses nodes (un seul pas d'historique) ;
   *  un geste non enregistré (la pose du node d'un plan neuf) part sans attendre : un Ctrl+Z
   *  qui retire ce plan juste après ne doit pas le voir revenir. */
  const commit = useCallback(
    (items: BoardItem[], record = true, links: BoardLink[] = []) => {
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
      if (after.size === 0 && links.length === 0) return;
      if (record) {
        const gesture: Gesture = links.length
          ? { before, after, links }
          : { before, after };
        past.current = [...past.current, gesture].slice(-HISTORY_MAX);
        future.current = [];
        counts();
      }
      write(after, links, !record);
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
    (target: Ops, other: Ops, links: BoardLink[] = []) => {
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
      // KAN-111 : un lien ne repart que si ses deux nodes sont là (ou reviennent).
      const back = new Set([...ops].filter(([, i]) => i).map(([id]) => id));
      const alive = (id: string) =>
        back.has(id) || (here.has(id) && ops.get(id) !== null);
      write(
        ops,
        links.filter((l) => alive(l.source) && alive(l.target)),
      );
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
    replay(gesture.after, gesture.before, gesture.links);
  }, [replay]);

  /** KAN-91 : pose ou coupe des liens, tout de suite (pas d'attente : le geste est rare et
   *  sa conséquence se lit aussitôt). Montré avant la réponse ; refusé (409, 422), le tableau
   *  revient à celui du serveur et la raison est dite. Renvoie si le serveur l'a accepté. */
  const writeLinks = useCallback(
    async (set: BoardLink[], cut: string[]): Promise<boolean> => {
      const current = latest.current;
      if (!current) return false;
      const gone = new Set([...cut, ...set.map((l) => l.id)]);
      const pairs = new Set(set.map((l) => `${l.source}>${l.target}`));
      const links = [
        ...(current.links ?? []).filter(
          (l) => !gone.has(l.id) && !pairs.has(`${l.source}>${l.target}`),
        ),
        ...set,
      ];
      latest.current = { ...current, links };
      setBoard(latest.current);
      try {
        const saved = await boardApi.patch(projectId, {
          set: [],
          delete: [],
          links_set: set,
          links_delete: cut,
        });
        show(saved);
        return true;
      } catch (e) {
        setNotice(e instanceof Error ? e.message : String(e));
        if (server.current) show(server.current);
        return false;
      }
    },
    [projectId, show],
  );

  return {
    board,
    save,
    notice,
    canUndo: history.undo > 0,
    canRedo: history.redo > 0,
    commit,
    writeLinks,
    setView,
    undo,
    redo,
    retry: () => void flush(),
    dismissNotice: () => setNotice(null),
  };
}
