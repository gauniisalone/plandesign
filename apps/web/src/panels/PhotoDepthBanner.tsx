import type { PhotoDepthState } from "../state/useProject";

/** KAN-79 : « Analyse de la photo… » (profondeur calculée dans le navigateur), avec sa
 *  progression ; au premier dépôt, le téléchargement est annoncé ; en cas d'échec, la raison
 *  et « Réessayer ». L'espace de travail reste utilisable pendant tout ce temps. */
export function PhotoDepthBanner({
  depth,
  onRetry,
  onDismiss,
}: {
  depth: PhotoDepthState;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  if (depth.error)
    return (
      <div
        className="auto-banner photo-depth failed"
        role="alert"
        aria-label="Analyse de la photo"
      >
        <span>
          <strong>L'analyse de la photo n'a pas abouti</strong> : {depth.error}.
          <span className="notice">
            La photo est rangée ; vous pouvez continuer à travailler.
          </span>
        </span>
        {depth.reload ? (
          // Correctif de KAN-79 : la bibliothèque n'a pas démarré dans cette page, une nouvelle
          // tentative ici échouerait encore ; le rechargement repart de zéro.
          <button className="primary" onClick={() => window.location.reload()}>
            Recharger la page
          </button>
        ) : (
          <button className="primary" onClick={onRetry}>
            Réessayer
          </button>
        )}
        <button className="link" onClick={onDismiss}>
          fermer
        </button>
      </div>
    );
  const percent =
    depth.fraction === null ? null : Math.round(depth.fraction * 100);
  return (
    <div
      className="auto-banner photo-depth"
      role="status"
      aria-label="Analyse de la photo"
    >
      <span className="spinner" aria-hidden="true" />
      <span>
        <strong>Analyse de la photo…</strong> {depth.label}
        {percent === null ? "" : ` (${percent} %)`}
        {depth.firstTime && (
          <span className="notice">
            Première analyse : téléchargement d'environ 50 Mo de modèle et 28 Mo
            de bibliothèque depuis PlanDesign, gardés ensuite par le navigateur.
            Le calcul se fait sur votre appareil.
          </span>
        )}
      </span>
      {percent !== null && (
        <progress max={100} value={percent} aria-label="Progression" />
      )}
    </div>
  );
}
