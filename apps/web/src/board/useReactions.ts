import { useCallback, useEffect, useRef, useState } from "react";
import {
  reactionsApi,
  type ReactionKey,
  type ReactionPill,
} from "../api/client";
import { LOCAL_REFRESH_MS } from "../state/bell";
import { diffPills, type PillFx } from "../state/reactionFx";

/** KAN-78 : les réactions de l'espace de travail. Relues au chargement, à chaque avis
 *  « reactions » d'un autre membre (WebSocket, avec les comptes) et, en local, comme les
 *  commentaires (KAN-68 : retour sur l'onglet, focus, 15 s). Un lecteur les voit sans pouvoir
 *  réagir ; on ne retire que sa propre réaction. */

/** KAN-78, Must have 10 : les animations en cours (classes posées un instant, puis retirées).
 *  La première lecture n'anime rien ; en mouvement réduit, rien du tout : seuls les états
 *  changent (pas de pastille qui se replie, pas d'emoji qui monte). */
const FX_MS = 650;

const EMPTY_FX: PillFx = {
  pops: [],
  slides: [],
  faded: [],
  floats: [],
  leaving: [],
};

function reducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true
  );
}

export function usePillFx(pills: ReactionPill[]): PillFx {
  const before = useRef<ReactionPill[] | null>(null);
  const [fx, setFx] = useState<PillFx>(EMPTY_FX);
  useEffect(() => {
    const previous = before.current;
    before.current = pills;
    if (previous === null || reducedMotion()) return;
    const next = diffPills(previous, pills);
    const any =
      next.pops.length +
      next.slides.length +
      next.faded.length +
      next.floats.length +
      next.leaving.length;
    if (!any) return;
    setFx(next);
    const timer = window.setTimeout(() => setFx(EMPTY_FX), FX_MS);
    return () => window.clearTimeout(timer);
  }, [pills]);
  return fx;
}

/** Le bouton du survol reste un instant après la sortie de l'élément : le temps de l'atteindre
 *  sous l'élément. */
const LEAVE_MS = 400;

/** L'élément survolé de l'espace de travail (son bouton « Réagir » apparaît sous lui). */
export function useHoveredItem(): {
  hovered: string | null;
  enter: (id: string) => void;
  leave: () => void;
} {
  const [hovered, setHovered] = useState<string | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  return {
    hovered,
    enter: (id) => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = null;
      setHovered(id);
    },
    leave: () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setHovered(null), LEAVE_MS);
    },
  };
}

export interface ReactionsController {
  pills: ReactionPill[];
  canReact: boolean;
  error: string | null;
  /** L'élément dont le sélecteur est ouvert. */
  pickerFor: string | null;
  openPicker: (itemId: string | null) => void;
  toggle: (itemId: string, key: ReactionKey) => Promise<void>;
  /** KAN-68 : en local, change quand les réactions lues ont changé (l'Activité se relit). */
  revision: number;
}

export function useReactions(
  projectId: string,
  canReact: boolean,
  tick: number,
  local = false,
): ReactionsController {
  const [pills, setPills] = useState<ReactionPill[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  /** Ce qui a été lu la dernière fois (null : rien encore). */
  const seen = useRef<string | null>(null);

  const adopt = useCallback((list: ReactionPill[]) => {
    setPills(list);
    setError(null);
    const now = JSON.stringify(list);
    if (seen.current !== null && seen.current !== now)
      setRevision((n) => n + 1);
    seen.current = now;
  }, []);

  const reload = useCallback(
    () =>
      reactionsApi
        .list(projectId)
        .then(adopt, (e: unknown) => setError(String(e))),
    [projectId, adopt],
  );
  useEffect(() => {
    void reload();
  }, [reload, tick]);
  useEffect(() => {
    if (!local) return;
    const again = () => void reload();
    const onVisible = () => {
      if (document.visibilityState === "visible") again();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", again);
    const timer = window.setInterval(again, LOCAL_REFRESH_MS);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", again);
      window.clearInterval(timer);
    };
  }, [local, reload]);

  const toggle = useCallback(
    async (itemId: string, key: ReactionKey) => {
      if (!canReact) return;
      const mine = pills.some(
        (p) => p.item_id === itemId && p.key === key && p.mine,
      );
      try {
        adopt(
          await (mine
            ? reactionsApi.remove(projectId, itemId, key)
            : reactionsApi.add(projectId, itemId, key)),
        );
      } catch (e) {
        setError(String(e));
      }
    },
    [canReact, pills, projectId, adopt],
  );

  return {
    pills,
    canReact,
    error,
    pickerFor,
    openPicker: setPickerFor,
    toggle,
    revision: local ? revision : 0,
  };
}
