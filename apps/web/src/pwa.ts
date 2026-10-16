import { registerSW } from "virtual:pwa-register";

/** Mise à jour du front installé (PWA, 4.8 ; correctif du 4 oct. 2026).
 *
 *  Le service worker met le front construit en cache. Une nouvelle version publiée par le
 *  serveur est détectée au chargement (et toutes les 30 minutes) puis installée en attente :
 *  - dans les premières secondes après un chargement, elle est appliquée tout de suite (le
 *    nouveau service worker prend la main et la page se recharge une fois d'elle-même) : un
 *    seul rechargement de l'utilisateur suffit pour voir la nouvelle version ;
 *  - plus tard (l'utilisateur est en train de travailler), un bandeau propose de recharger
 *    plutôt que de couper une saisie.
 *
 *  Le rechargement est fait ici, au changement de contrôleur demandé par nous : celui de
 *  vite-plugin-pwa (1.3) ne recharge pas quand la mise à jour a été trouvée hors de lui (au
 *  chargement de la page, par le navigateur), ce qui laissait l'onglet sur l'ancienne
 *  version. */

const YOUNG_MS_DEFAULT = 15_000;
const CHECK_EVERY_MS = 30 * 60 * 1000;

let apply: ((reload?: boolean) => Promise<void>) | null = null;
let registration: ServiceWorkerRegistration | undefined;
let ready = false;
let applying = false;
let reloading = false;
const listeners = new Set<() => void>();

function youngMs(): number {
  try {
    const raw = window.localStorage.getItem("openplan:pwa-young-ms");
    return raw === null ? YOUNG_MS_DEFAULT : Number(raw);
  } catch {
    return YOUNG_MS_DEFAULT;
  }
}

export function startPwa(): void {
  if (!("serviceWorker" in navigator)) return;
  const loadedAt = performance.now();
  const young = youngMs();
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // La première prise de contrôle (installation) ne recharge pas : seule une mise à jour
    // demandée (applyUpdate) le fait, une seule fois.
    if (applying && !reloading) {
      reloading = true;
      window.location.reload();
    }
  });
  apply = registerSW({
    immediate: true,
    onNeedRefresh() {
      if (performance.now() - loadedAt < young) {
        applyUpdate();
        return;
      }
      ready = true;
      listeners.forEach((l) => l());
    },
    onRegisteredSW(_url, r) {
      registration = r;
      if (r) window.setInterval(() => void r.update(), CHECK_EVERY_MS);
    },
  });
}

/** Une nouvelle version attend (bandeau à montrer). */
export function updateReady(): boolean {
  return ready;
}

export function subscribeUpdate(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Applique la nouvelle version : le service worker en attente prend la main, la page se
 *  recharge (une fois) au changement de contrôleur. */
export function applyUpdate(): void {
  applying = true;
  void apply?.(true);
  void navigator.serviceWorker.getRegistration().then((r) => {
    (r ?? registration)?.waiting?.postMessage({ type: "SKIP_WAITING" });
  });
}
