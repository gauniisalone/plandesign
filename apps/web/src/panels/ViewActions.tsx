import type { Op } from "../api/ops";
import type { Camera, Violation } from "../api/types.gen";
import { decimal, metres } from "../format";
import {
  excludeViewOp,
  hasObservations,
  isExcludedView,
  observedExtents,
} from "../model";

/** Ce qu'une photo a apporté au plan, en clair, et la décision en un clic : garder la photo
 *  (le plan la suit), l'écarter (elle reste dans le projet, sans peser sur le plan) ou la
 *  tourner (l'orientation retenue n'est connue qu'à un quart de tour près). */
export function ViewSummary({ camera }: { camera: Camera }) {
  const excluded = isExcludedView(camera);
  const extents = observedExtents(camera);
  const seen = Object.entries(extents)
    .map(
      ([axis, m]) =>
        `${axis === "x" ? "largeur" : "profondeur"} vue ${metres(m)}`,
    )
    .join(", ");
  const ambiguous = camera.metadata["orientation_ambiguous"] === true;
  const turned = camera.metadata["orientation_user_fixed"] === true;
  return (
    <span className="muted">
      <span
        className={`badge ${excluded ? "view-excluded" : "source-estimated"}`}
      >
        {camera.name ?? "Photo"}
        {excluded ? " · écartée" : ""}
      </span>{" "}
      caméra en ({decimal(camera.position.x)} ; {decimal(camera.position.y)}
      ), regarde vers {Math.round((camera.yaw * 180) / Math.PI)}°
      {seen ? ` · ${seen}` : " · aucun couple de murs opposés vu"}
      {turned
        ? " · orientation choisie par vous"
        : ambiguous
          ? " · orientation ambiguë (pièce symétrique)"
          : ""}
    </span>
  );
}

export function ViewActions({
  camera,
  violation,
  busy,
  onOp,
  onAdoptView,
  onRotateView,
}: {
  camera: Camera;
  violation: Violation | undefined;
  busy: boolean;
  onOp: (op: Op) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
}) {
  const excluded = isExcludedView(camera);
  const canAdopt = !excluded && Object.keys(observedExtents(camera)).length > 0;
  const canRotate = !excluded && hasObservations(camera);
  const ambiguous = camera.metadata["orientation_ambiguous"] === true;
  return (
    <div className="view-actions">
      {violation && <p className="warn">{violation.message}</p>}
      <div className="row">
        {canAdopt && (
          <button
            disabled={busy}
            onClick={() => onAdoptView(camera.id)}
            title="Le plan suit cette photo : le mur le moins sûr est déplacé à la distance vue ; un mur saisi ou mesuré n'est jamais déplacé"
          >
            Garder cette photo
          </button>
        )}
        {canRotate && (
          <button
            disabled={busy}
            className={ambiguous ? "" : "link"}
            onClick={() => onRotateView(camera.id)}
            title="L'orientation d'une photo n'est connue qu'à un quart de tour près : la caméra tourne d'un quart de tour autour de la pièce (position et visée), aucun mur ne bouge ; recommencez jusqu'à ce que la caméra soit du bon côté"
          >
            Tourner la vue
          </button>
        )}
        {excluded ? (
          <button
            disabled={busy}
            onClick={() => onOp(excludeViewOp(camera, false))}
            title="La photo pèse à nouveau sur le plan"
          >
            Réintégrer cette photo
          </button>
        ) : (
          <button
            disabled={busy}
            onClick={() => onOp(excludeViewOp(camera, true))}
            title="La photo et sa caméra restent dans le projet, mais ne pèsent plus sur le plan"
          >
            Écarter cette photo
          </button>
        )}
      </div>
    </div>
  );
}
