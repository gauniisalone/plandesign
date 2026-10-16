import { useSyncExternalStore } from "react";
import { applyUpdate, subscribeUpdate, updateReady } from "./pwa";

/** Bandeau « Nouvelle version disponible » : une mise à jour est arrivée pendant que la page
 *  était ouverte ; recharger l'applique (rien n'est perdu : chaque modification est déjà
 *  enregistrée par le serveur). */
export function UpdateBanner() {
  const ready = useSyncExternalStore(subscribeUpdate, updateReady, () => false);
  if (!ready) return null;
  return (
    <div className="update-banner" role="status">
      <span>Nouvelle version disponible</span>
      <button type="button" onClick={applyUpdate}>
        Recharger
      </button>
    </div>
  );
}
