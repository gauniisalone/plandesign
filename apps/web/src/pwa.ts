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
 *  version.
 *
 *  KAN-80 : « les premières secondes » s'arrêtent au premier geste. Une page sur laquelle la
 *  personne a commencé à agir (un clic, une touche) n'est jamais rechargée d'office : juste
 *  après une mise en ligne, le premier clic sur « Nouveau projet » se perdait dans ce
 *  rechargement. La mise à jour passe alors par le bandeau. */

const YOUNG_MS_DEFAULT = 15_000;
const CHECK_EVERY_MS = 30 * 60 * 1000;

let apply: ((reload?: boolean) => Promise<void>) | null = null;
let registration: ServiceWorkerRegistration | undefined;
let ready = false;
let applying = false;
let reloading = false;
let acted = false;
/** Application automatique (premières secondes), annulée si la personne agit entre-temps. */
let automatic = false;
/** La nouvelle version a déjà pris la main sans recharger (geste pendant l'application). */
let activated = false;
const listeners = new Set<() => void>();

function showBanner(): void {
  ready = true;
  listeners.forEach((l) => l());
}

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
  const act = () => {
    acted = true;
  };
  window.addEventListener("pointerdown", act, { capture: true, once: true });
  window.addEventListener("keydown", act, { capture: true, once: true });
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    // La première prise de contrôle (installation) ne recharge pas : seule une mise à jour
    // demandée (applyUpdate) le fait, une seule fois.
    if (!applying || reloading) return;
    if (automatic && acted) {
      // KAN-80 : la personne a agi pendant l'application automatique : pas de rechargement.
      activated = true;
      applying = false;
      showBanner();
      return;
    }
    reloading = true;
    window.location.reload();
  });
  apply = registerSW({
    immediate: true,
    onNeedRefresh() {
      if (performance.now() - loadedAt < young && !acted) {
        automatic = true;
        applyUpdate();
        return;
      }
      showBanner();
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
  if (activated) {
    // Déjà aux commandes (KAN-80) : il ne reste qu'à recharger.
    reloading = true;
    window.location.reload();
    return;
  }
  applying = true;
  // Application automatique : vite-plugin-pwa ne recharge pas lui-même, c'est notre écoute du
  // changement de contrôleur qui décide (rechargement, ou bandeau si la personne a agi).
  void apply?.(!automatic);
  void navigator.serviceWorker.getRegistration().then((r) => {
    (r ?? registration)?.waiting?.postMessage({ type: "SKIP_WAITING" });
  });
}
