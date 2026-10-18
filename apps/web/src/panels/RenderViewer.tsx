import { useCapabilities } from "../state/capabilities";
import { useEffect } from "react";
import type { RenderEntry } from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { allCameras } from "../model";
import { latestRenders, rerenderLabel } from "../staleViews";
import type { PickAction } from "./PickCard";
import { ArrivalView } from "./RenderPanel";

/** Visionneuse plein écran (T7d2) : remplace l'écran de résultats. Une vue à la fois, en grand,
 *  avec tout ce que portait sa carte (périmée, favori, A / B / C, version catalogue, image
 *  Blender, « Affiner cette vue », désignation d'un élément au clic, télécharger) ; flèches
 *  (ou ← →) pour passer d'une vue à l'autre, Échap pour fermer. Les vues périmées se refont
 *  d'ici. À droite, la place des commentaires de la vue, masquée tant que B2 (travail à
 *  plusieurs) n'existe pas. */

/** B2 : colonne des commentaires de la vue ; rien de visible avant (décision PO, 18 oct.). */
const VIEWER_COMMENTS = false;

export function RenderViewer({
  project,
  level,
  renders,
  stale,
  busy,
  running,
  cameraId,
  onNavigate,
  onClose,
  onRerender,
  onRefine,
  onPickAction,
  onCatalogue,
  onUndo,
  onRedo,
}: {
  /** Annuler / rétablir dans le plan (Ctrl+Z, Ctrl+Maj+Z, Ctrl+Y). */
  onUndo: () => void;
  onRedo: () => void;
  project: Project;
  level: Level;
  renders: RenderEntry[];
  stale: Set<string>;
  busy: boolean;
  running: boolean;
  /** Vue montrée (sa caméra) ; la première vue rendue si elle n'en a plus. */
  cameraId: string;
  onNavigate: (cameraId: string) => void;
  onClose: () => void;
  onRerender: (cameraIds: string[]) => void;
  onRefine: (cameraId: string) => void;
  onPickAction: (cameraId: string, action: PickAction) => void;
  onCatalogue?: (runId: string) => void;
}) {
  const { renders: canRender } = useCapabilities();
  const latest = latestRenders(renders);
  const views = allCameras(level)
    .map((cam) => latest.get(cam.id))
    .filter((r): r is RenderEntry => r !== undefined);
  const index = Math.max(
    0,
    views.findIndex((r) => r.camera_id === cameraId),
  );
  const current = views[index];
  const staleIds = views
    .map((r) => r.camera_id ?? "")
    .filter((id) => stale.has(id));
  const go = (delta: number) => {
    if (views.length < 2) return;
    const next = views[(index + delta + views.length) % views.length];
    if (next?.camera_id) onNavigate(next.camera_id);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      // Échap ferme d'abord ce qui est ouvert par-dessus (carte de désignation, image
      // agrandie…), pas la visionneuse.
      if (document.querySelector('[role="dialog"]:not(.render-viewer)')) return;
      // Ce qu'on fait d'ici (retirer, remplacer un meuble) est dans le plan : Ctrl+Z et
      // Ctrl+Maj+Z / Ctrl+Y annulent et rétablissent dans le plan, même ouverte depuis
      // l'espace de travail.
      const mod = e.ctrlKey || e.metaKey;
      const key = e.key.toLowerCase();
      if (mod && key === "z") (e.shiftKey ? onRedo : onUndo)();
      else if (mod && key === "y") onRedo();
      else if (e.key === "Escape") onClose();
      else if (e.key === "ArrowRight") go(1);
      else if (e.key === "ArrowLeft") go(-1);
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  });

  return (
    <div
      className="render-viewer"
      role="dialog"
      aria-modal="true"
      aria-label="Visionneuse des rendus"
    >
      <header className="render-viewer-head">
        <strong>{current?.camera_name ?? "Rendus"}</strong>
        {views.length > 1 && (
          <span className="muted">
            {index + 1} / {views.length}
          </span>
        )}
        <span className="render-viewer-sp" />
        {staleIds.length > 0 && canRender && (
          <button
            type="button"
            className="render-viewer-stale"
            disabled={running}
            onClick={() => onRerender(staleIds)}
            title="Le plan, l'ambiance ou le point de vue ont changé depuis ces rendus"
          >
            {rerenderLabel(staleIds.length)}
          </button>
        )}
        <button
          type="button"
          className="icon"
          aria-label="Fermer la visionneuse"
          title="Fermer (Échap)"
          onClick={onClose}
        >
          ×
        </button>
      </header>
      <div className="render-viewer-body">
        <button
          type="button"
          className="icon render-viewer-nav"
          aria-label="Vue précédente"
          title="Vue précédente (←)"
          disabled={views.length < 2}
          onClick={() => go(-1)}
        >
          ‹
        </button>
        <div className="render-viewer-main">
          {current ? (
            <ul className="arrival-renders renders-grid render-viewer-one">
              <ArrivalView
                key={current.run_id}
                projectId={project.id}
                entry={current}
                stale={stale.has(current.camera_id ?? "")}
                onRefine={() => onRefine(current.camera_id ?? "")}
                onPickAction={(action) =>
                  onPickAction(current.camera_id ?? "", action)
                }
                onCatalogue={onCatalogue}
                busy={busy}
              />
            </ul>
          ) : (
            <p className="muted">Aucun rendu pour l'instant.</p>
          )}
        </div>
        <button
          type="button"
          className="icon render-viewer-nav"
          aria-label="Vue suivante"
          title="Vue suivante (→)"
          disabled={views.length < 2}
          onClick={() => go(1)}
        >
          ›
        </button>
        {VIEWER_COMMENTS && (
          <aside
            className="render-viewer-comments"
            aria-label="Commentaires de la vue"
          >
            <h3>Commentaires</h3>
          </aside>
        )}
      </div>
    </div>
  );
}
