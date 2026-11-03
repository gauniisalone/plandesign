import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { guidedLine, realisticApi, type RealisticRequest } from "../api/client";
import {
  FIT,
  WHEEL_STEP,
  ZOOM_STEP,
  panBy,
  transformOf,
  zoomAt,
  zoomText,
  type CompareView,
} from "./compareView";

/** KAN-99 (maquette validée par augus le 2 nov.) : la vue comparée plein écran, de 2 à 4
 *  rendus choisis dans le node Rendus ou la visionneuse. Chaque rendu a le même cadre 4:3 ; le
 *  zoom (molette, − / +, « Ajuster ») et le déplacement (glisser) sont communs à tous : une
 *  seule transformation, posée en variable CSS sur la grille. Sous chaque rendu, sa vue, son
 *  heure, son « Guidé par » (KAN-97) et son étoile. ← → change le rendu actif, Entrée l'ouvre
 *  seul dans la visionneuse, Échap ferme. */

export interface RenderCompareProps {
  projectId: string;
  /** Les rendus choisis, dans l'ordre des choix. */
  renders: RealisticRequest[];
  viewName: (r: RealisticRequest) => string;
  favorites: ReadonlySet<string>;
  /** Éditeur : l'étoile se pose ; sinon elle se lit seulement. */
  canStar: boolean;
  onStar: (requestId: string, on: boolean) => Promise<void>;
  onOpen: (requestId: string) => void;
  onClose: () => void;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("fr-FR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function RenderCompare(props: RenderCompareProps) {
  const { renders, onClose, onOpen } = props;
  const [view, setView] = useState<CompareView>(FIT);
  const [active, setActive] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const grid = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  /** La taille d'un cadre (tous les cadres ont la même). */
  const size = useCallback((): [number, number] => {
    const stage = grid.current?.querySelector(".rcomp-stage");
    const box = stage?.getBoundingClientRect();
    return box ? [box.width, box.height] : [1, 1];
  }, []);

  useEffect(() => {
    grid.current?.style.setProperty("--rcomp-t", transformOf(view));
  }, [view]);

  // Le focus sur la vue elle-même (pas sur « Fermer ») : Entrée ouvre le rendu actif.
  useEffect(() => {
    dialog.current?.focus();
  }, []);

  const zoomCenter = useCallback(
    (factor: number) =>
      setView((v) => {
        const [w, h] = size();
        return zoomAt(v, factor, w / 2, h / 2, w, h);
      }),
    [size],
  );

  // La molette zoome autour du pointeur (écouteur non passif : la page ne défile pas).
  useEffect(() => {
    const el = grid.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      const stage = (e.target as Element | null)?.closest(".rcomp-stage");
      if (!stage) return;
      e.preventDefault();
      const box = stage.getBoundingClientRect();
      const factor = e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP;
      setView((v) =>
        zoomAt(
          v,
          factor,
          e.clientX - box.left,
          e.clientY - box.top,
          box.width,
          box.height,
        ),
      );
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);

  // Comme la visionneuse : le clavier n'atteint pas l'espace de travail.
  const count = renders.length;
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      e.stopPropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        const step = e.key === "ArrowRight" ? 1 : count - 1;
        setActive((a) => (a + step) % count);
      } else if (e.key === "Enter" && !(e.target instanceof HTMLButtonElement)) {
        e.preventDefault();
        const r = renders[active];
        if (r) onOpen(r.id);
      } else if (e.key === "+" || e.key === "=") {
        e.preventDefault();
        zoomCenter(ZOOM_STEP);
      } else if (e.key === "-") {
        e.preventDefault();
        zoomCenter(1 / ZOOM_STEP);
      } else if (e.key === "0") {
        e.preventDefault();
        setView(FIT);
      }
    };
    window.addEventListener("keydown", down, true);
    return () => window.removeEventListener("keydown", down, true);
  }, [active, count, renders, onClose, onOpen, zoomCenter]);

  const star = async (id: string, on: boolean) => {
    setError(null);
    try {
      await props.onStar(id, on);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return createPortal(
    <div
      ref={dialog}
      tabIndex={-1}
      className="rcomp"
      role="dialog"
      aria-modal="true"
      aria-label="Comparer les rendus"
      onPointerDown={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <header className="rview-head">
        <button
          type="button"
          className="icon rview-close"
          aria-label="Fermer la comparaison"
          onClick={onClose}
        >
          ×
        </button>
        <strong className="rview-title">Comparer {count} rendus</strong>
        <span className="rview-meta">zoom et déplacement synchronisés</span>
        <span className="rcomp-zoom" role="group" aria-label="Zoom">
          <button
            type="button"
            aria-label="Dézoomer"
            disabled={view.scale <= 1}
            onClick={() => zoomCenter(1 / ZOOM_STEP)}
          >
            −
          </button>
          <output aria-live="polite" aria-label="Niveau de zoom">
            {zoomText(view)}
          </output>
          <button
            type="button"
            aria-label="Zoomer"
            onClick={() => zoomCenter(ZOOM_STEP)}
          >
            +
          </button>
          <button type="button" onClick={() => setView(FIT)}>
            Ajuster
          </button>
        </span>
        <span className="rview-keys">
          ← → passe d'un rendu à l'autre · Entrée l'ouvre seul · Échap ferme
        </span>
      </header>
      <div className="rcomp-grid" data-n={count} ref={grid}>
        {renders.map((r, i) => {
          const fav = props.favorites.has(r.id);
          const guided = guidedLine(r);
          const name = props.viewName(r);
          return (
            <div
              key={r.id}
              className={`rcomp-pane${i === active ? " active" : ""}`}
              role="group"
              aria-label={`Rendu ${i + 1} : ${name}`}
              aria-current={i === active ? "true" : undefined}
            >
              <div
                className="rcomp-stage"
                onPointerDown={(e) => {
                  setActive(i);
                  drag.current = { x: e.clientX, y: e.clientY };
                  e.currentTarget.setPointerCapture(e.pointerId);
                }}
                onPointerMove={(e) => {
                  const from = drag.current;
                  if (!from) return;
                  drag.current = { x: e.clientX, y: e.clientY };
                  const [w, h] = size();
                  setView((v) =>
                    panBy(v, e.clientX - from.x, e.clientY - from.y, w, h),
                  );
                }}
                onPointerUp={() => (drag.current = null)}
                onPointerCancel={() => (drag.current = null)}
              >
                <img
                  className="rcomp-img"
                  src={realisticApi.imageUrl(props.projectId, r.id)}
                  alt={`Rendu ${i + 1} : ${name}`}
                  draggable={false}
                />
                <span className="rcomp-num" aria-hidden="true">
                  {i + 1}
                </span>
              </div>
              <div className="rcomp-cap">
                <strong>
                  {name} · {when(r.created_at)}
                  {/* KAN-115 : « Ancien membre » pour un compte effacé. */}
                  {r.author ? ` · ${r.author}` : ""}
                </strong>
                {guided && <span className="rcomp-guided">{guided}</span>}
                {props.canStar ? (
                  <button
                    type="button"
                    className="rgal-star"
                    aria-pressed={fav}
                    aria-label={fav ? "Retirer des favoris" : "Ajouter aux favoris"}
                    onClick={() => void star(r.id, !fav)}
                  >
                    <span className="rgal-star-glyph" aria-hidden="true">
                      {fav ? "★" : "☆"}
                    </span>
                  </button>
                ) : (
                  fav && (
                    <span className="rgal-star" role="img" aria-label="Favori">
                      <span className="rgal-star-glyph">★</span>
                    </span>
                  )
                )}
              </div>
            </div>
          );
        })}
      </div>
      {error && (
        <p className="rview-error rcomp-error" role="alert">
          {error}
        </p>
      )}
    </div>,
    document.body,
  );
}
