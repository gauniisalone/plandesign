/** KAN-120 : le bas du node Photos de la pièce. Le bouton principal dit combien de photos il
 *  lira ; une phrase invite à en ajouter ; pendant le calcul, la progression « photo k sur
 *  N » ; une fois le plan là, « Régénérer le plan » demande confirmation (le plan actuel est
 *  remplacé en une seule étape d'annulation). Rien ne se lance sans ce clic. */

import { useEffect, useState } from "react";

import {
  generateLabel,
  photosHint,
  REGENERATE_TITLE,
  regenerateWarning,
  type PhotosState,
} from "./photoStatus";

export interface PhotosProgress {
  label: string;
  fraction: number | null;
}

interface Props {
  /** Le bas du node Photos, en coordonnées de l'espace de travail : la barre s'y pose,
   *  juste dessous (au-dessus des pastilles de réactions des photos). */
  at: { x: number; y: number; w: number };
  state: PhotosState;
  progress: PhotosProgress | null;
  /** Un autre calcul occupe le plan (construction, chemin automatique). */
  busy: boolean;
  furniture: number;
  onGenerate: (replace: boolean) => void;
}

export function PhotosGenerate({ at, state, progress, busy, furniture, onGenerate }: Props) {
  const [asking, setAsking] = useState(false);
  useEffect(() => {
    if (!asking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAsking(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [asking]);
  if (state.total === 0) return null;
  return (
    <span
      className="board-photos-generate"
      style={{ left: at.x, top: at.y, width: at.w }}
      onPointerDown={(e) => e.stopPropagation()}
    >
      {progress ? (
        <span className="board-photos-progress" role="status">
          <span className="board-photos-progress-line">
            <span className="spinner" aria-hidden="true" />
            {progress.label}
          </span>
          {progress.fraction !== null && (
            <span className="board-photos-bar" aria-hidden="true">
              <i style={{ width: `${Math.round(progress.fraction * 100)}%` }} />
            </span>
          )}
        </span>
      ) : (
        <>
          <span className="board-photos-hint">
            {state.summary && state.hasPlan ? `${state.summary} · ` : ""}
            {photosHint(state)}
          </span>
          <button
            type="button"
            className={state.hasPlan ? "board-photos-button" : "primary board-photos-button"}
            disabled={busy}
            onClick={() => (state.hasPlan ? setAsking(true) : onGenerate(false))}
          >
            {generateLabel(state)}
          </button>
        </>
      )}
      {asking && (
        <span
          className="board-photos-confirm"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="board-photos-confirm-title"
        >
          <b id="board-photos-confirm-title">{REGENERATE_TITLE}</b>
          <span>{regenerateWarning(state.total, furniture)}</span>
          <span className="board-photos-confirm-actions">
            <button type="button" autoFocus onClick={() => setAsking(false)}>
              Annuler
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => {
                setAsking(false);
                onGenerate(true);
              }}
            >
              Régénérer le plan
            </button>
          </span>
        </span>
      )}
    </span>
  );
}
