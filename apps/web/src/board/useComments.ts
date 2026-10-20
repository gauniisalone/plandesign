import { useCallback, useEffect, useState } from "react";
import {
  commentsApi,
  type BoardItem,
  type CommentTarget,
  type CommentThread,
} from "../api/client";

/** KAN-51 : commentaires de l'espace de travail. Des épingles posées n'importe où ou sur un
 *  élément (elles le suivent), un fil de réponses signées (nom, couleur, heure), Résoudre et
 *  Rouvrir, un panneau « Commentaires » (ouverts, résolus). Le texte est toujours affiché comme
 *  du texte (jamais du HTML). Les autres membres reçoivent l'avis « changed / comments » et
 *  relisent les fils. Mêmes routes en local et avec les comptes. */

export const MAX_COMMENT = 2000;

export interface CommentsController {
  threads: CommentThread[];
  canComment: boolean;
  error: string | null;
  draft: CommentTarget | null;
  openId: string | null;
  panelOpen: boolean;
  hidden: boolean;
  startDraft: (target: CommentTarget) => void;
  cancelDraft: () => void;
  publish: (text: string) => Promise<void>;
  open: (id: string | null) => void;
  setPanelOpen: (open: boolean) => void;
  setHidden: (hidden: boolean) => void;
  reply: (id: string, text: string) => Promise<void>;
  resolve: (id: string, resolved: boolean) => Promise<void>;
  removeMessage: (id: string, messageId: string) => Promise<void>;
  removeThread: (id: string) => Promise<void>;
}

export function useComments(
  projectId: string,
  canComment: boolean,
  tick: number,
): CommentsController {
  const [threads, setThreads] = useState<CommentThread[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<CommentTarget | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [hidden, setHidden] = useState(false);

  const reload = useCallback(
    () =>
      commentsApi.list(projectId).then(
        (list) => {
          setThreads(list);
          setError(null);
        },
        (e: unknown) => setError(String(e)),
      ),
    [projectId],
  );
  // Au chargement, et à chaque avis « comments » d'un autre membre (moins de 2 s).
  useEffect(() => {
    void reload();
  }, [reload, tick]);

  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      try {
        await action();
        setError(null);
      } catch (e) {
        setError(String(e));
      }
      await reload();
    },
    [reload],
  );

  return {
    threads,
    canComment,
    error,
    draft,
    openId,
    panelOpen,
    hidden,
    startDraft: (target) => {
      setDraft(target);
      setOpenId(null);
      setHidden(false);
    },
    cancelDraft: () => setDraft(null),
    publish: async (text) => {
      const target = draft;
      if (!target) return;
      setDraft(null);
      await act(async () => {
        const made = await commentsApi.create(projectId, target, text);
        setOpenId(made.id);
      });
    },
    open: (id) => {
      setOpenId(id);
      setDraft(null);
    },
    setPanelOpen,
    setHidden,
    reply: (id, text) => act(() => commentsApi.reply(projectId, id, text)),
    resolve: (id, resolved) =>
      act(() =>
        resolved
          ? commentsApi.resolve(projectId, id)
          : commentsApi.reopen(projectId, id),
      ),
    removeMessage: (id, messageId) =>
      act(() => commentsApi.removeMessage(projectId, id, messageId)),
    removeThread: (id) =>
      act(async () => {
        await commentsApi.removeThread(projectId, id);
        setOpenId(null);
      }),
  };
}

/** Où dessiner l'épingle : sur son élément s'il existe (elle le suit), sinon à la dernière
 *  position connue de l'élément (« détaché »), sinon au point posé. */
export function pinPlace(
  target: CommentTarget,
  items: BoardItem[],
): { x: number; y: number; detached: boolean } {
  if (target.item_id) {
    const item = items.find((i) => i.id === target.item_id);
    if (item)
      return {
        x: item.x + target.at.x,
        y: item.y + target.at.y,
        detached: false,
      };
    const base = target.last ?? { x: 0, y: 0 };
    return { x: base.x + target.at.x, y: base.y + target.at.y, detached: true };
  }
  return { x: target.at.x, y: target.at.y, detached: false };
}
