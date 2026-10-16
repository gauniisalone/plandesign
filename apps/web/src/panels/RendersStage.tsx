import { useEffect } from "react";
import type { RenderEntry } from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { allCameras, PLAN_STATUS_LABEL, planStatus, walls } from "../model";
import { latestRenders, rerenderLabel } from "../staleViews";
import type { PickAction } from "./PickCard";
import { ArrivalView } from "./RenderPanel";

/** Onglet Rendus (P1) : le dernier rendu de chaque vue, en grand, par-dessus la 3D (qui reste
 *  montée pour les vignettes des points de vue). Une vue dont ce qu'on voit a changé depuis
 *  son rendu est marquée « périmée », et « Re-rendre les n vues » les refait d'un clic (P2). */
export function RendersStage({
  project,
  level,
  renders,
  stale,
  busy,
  running,
  focus,
  onRerender,
  onRefine,
  onPickAction,
  onCatalogue,
  onEditPlan,
}: {
  /** « Modifier le plan » (A3) : ouvre l'édition du plan. */
  onEditPlan?: () => void;
  project: Project;
  level: Level;
  renders: RenderEntry[];
  stale: Set<string>;
  busy: boolean;
  /** Une tâche de rendu tourne : le bouton attend. */
  running: boolean;
  /** Vue choisie dans la bande : amenée à l'écran. */
  focus: { cameraId: string; token: number } | null;
  onRerender: (cameraIds: string[]) => void;
  /** « Affiner cette vue » (P3). */
  onRefine: (cameraId: string) => void;
  /** Un geste choisi dans la carte de désignation (P4b), pour la vue de ce rendu. */
  onPickAction: (cameraId: string, action: PickAction) => void;
  onCatalogue?: (runId: string) => void;
}) {
  const latest = latestRenders(renders);
  const views = allCameras(level)
    .map((cam) => latest.get(cam.id))
    .filter((r): r is RenderEntry => r !== undefined);
  const staleIds = views
    .map((r) => r.camera_id ?? "")
    .filter((id) => stale.has(id));
  useEffect(() => {
    if (!focus) return;
    document
      .getElementById(`render-view-${focus.cameraId}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [focus]);
  const status = planStatus(project, level);
  return (
    <section className="renders-stage" aria-label="Rendus">
      {/* A3 : le plan, son statut honnête (jamais un relevé certifié) et son édition. */}
      {walls(level).length > 0 && onEditPlan && (
        <div className="renders-plan" aria-label="Plan du projet">
          <span>Plan</span>
          {status && (
            <span className={`plan-status ${status}`}>
              {PLAN_STATUS_LABEL[status]}
            </span>
          )}
          <button type="button" onClick={onEditPlan}>
            Modifier le plan
          </button>
        </div>
      )}
      {views.length === 0 ? (
        <p className="renders-empty muted">
          Aucun rendu pour l'instant : posez des vues (« Vues automatiques »)
          puis « Rendre toutes les vues », dans le panneau de droite.
        </p>
      ) : (
        <>
          {staleIds.length > 0 && (
            <div className="renders-stale" role="status">
              <span>
                {staleIds.length === 1
                  ? "1 vue périmée : elle a changé depuis son rendu."
                  : `${staleIds.length} vues périmées : elles ont changé depuis leur rendu.`}
              </span>
              <button
                type="button"
                className="primary"
                disabled={running}
                onClick={() => onRerender(staleIds)}
              >
                {rerenderLabel(staleIds.length)}
              </button>
            </div>
          )}
          <ul className="arrival-renders renders-grid">
            {views.map((r) => (
              <ArrivalView
                key={r.run_id}
                anchorId={`render-view-${r.camera_id}`}
                projectId={project.id}
                entry={r}
                stale={stale.has(r.camera_id ?? "")}
                onRefine={() => onRefine(r.camera_id ?? "")}
                onPickAction={(action) =>
                  onPickAction(r.camera_id ?? "", action)
                }
                onCatalogue={onCatalogue}
                busy={busy}
              />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
