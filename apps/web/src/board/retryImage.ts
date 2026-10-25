import { useCallback, useEffect, useRef, useState } from "react";
import { tuned } from "../netTuning";

/** Connexion lente (25 oct.) : délais des nouveaux essais automatiques d'une image qui n'a
 *  pas chargé ; après eux, la case grise « Image non chargée » et « Réessayer ». */
export const IMAGE_RETRY_MS = [2_000, 5_000, 10_000];

/** Une image qui se recharge seule quand elle échoue (adresse marquée `retry=n`, pour passer
 *  outre un cache d'échec), puis se déclare en échec ; `retry` en refait un essai. Une case
 *  par image : l'appelant change de composant (clé) quand l'image change. */
export function useRetryImage(src: string): {
  src: string;
  failed: boolean;
  onError: () => void;
  onLoad: () => void;
  retry: () => void;
} {
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );
  const onError = useCallback(() => {
    if (timer.current !== null) return;
    if (attempt >= IMAGE_RETRY_MS.length) {
      setFailed(true);
      return;
    }
    timer.current = window.setTimeout(
      () => {
        timer.current = null;
        setAttempt((n) => n + 1);
      },
      tuned("imageRetryMs", IMAGE_RETRY_MS[attempt]!),
    );
  }, [attempt]);
  const onLoad = useCallback(() => setFailed(false), []);
  const retry = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setFailed(false);
    setAttempt((n) => n + 1);
  }, []);
  const sep = src.includes("?") ? "&" : "?";
  return {
    src: attempt === 0 ? src : `${src}${sep}retry=${attempt}`,
    failed,
    onError,
    onLoad,
    retry,
  };
}
