import { renderApi, type RenderEntry } from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { allCameras } from "../model";
import { latestRenders, rerenderLabel } from "../staleViews";

/** Bande de rendus (P2) : le dernier rendu de chaque vue en vignette, sous le plan ; une vue
 *  périmée porte un point ; « Re-rendre les n vues » les refait en un clic, sans quitter le
 *  plan. Un clic sur une vignette ouvre la visionneuse sur cette vue (T7d2). */
export function RenderStrip({
  project,
  level,
  renders,
  stale,
  running,
  stage,
  onOpen,
  onRerender,
}: {
  project: Project;
  level: Level;
  renders: RenderEntry[];
  stale: Set<string>;
  /** Une tâche de rendu tourne : le bouton attend, l'étape en cours est dite. */
  running: boolean;
  stage: string | null;
  onOpen: (cameraId: string) => void;
  onRerender: (cameraIds: string[]) => void;
}) {
  const latest = latestRenders(renders);
  const views = allCameras(level)
    .map((cam) => ({ cam, r: latest.get(cam.id) }))
    .filter((v): v is { cam: (typeof v)["cam"]; r: RenderEntry } => !!v.r);
  if (views.length === 0) return null;
  const staleIds = views
    .filter((v) => stale.has(v.cam.id))
    .map((v) => v.cam.id);
  return (
    <nav className="float render-strip" aria-label="Bande de rendus">
      <ul>
        {views.map(({ cam, r }, i) => {
          const name = r.title || cam.name || `Vue ${i + 1}`;
          const isStale = stale.has(cam.id);
          return (
            <li key={cam.id}>
              <button
                type="button"
                className={isStale ? "stale" : undefined}
                onClick={() => onOpen(cam.id)}
                aria-label={`${name}${isStale ? " (périmée)" : ""}`}
                title={`${name}${isStale ? " : périmée depuis son rendu" : ""} ; voir en grand`}
              >
                <img
                  src={
                    r.finished
                      ? renderApi.finishedUrl(project.id, r)
                      : renderApi.imageUrl(project.id, r)
                  }
                  alt=""
                />
                {isStale && <span className="dot" aria-hidden="true" />}
              </button>
            </li>
          );
        })}
      </ul>
      {running ? (
        <span className="render-strip-stage" role="status">
          <span className="spinner" aria-hidden="true" />{" "}
          {stage || "rendu en cours…"}
        </span>
      ) : (
        staleIds.length > 0 && (
          <button
            type="button"
            className="primary"
            onClick={() => onRerender(staleIds)}
            title="Chaque vue est refaite comme son dernier rendu (qualité, finition) ; la lumière d'une vue automatique suit l'ambiance"
          >
            {rerenderLabel(staleIds.length)}
          </button>
        )
      )}
    </nav>
  );
}
