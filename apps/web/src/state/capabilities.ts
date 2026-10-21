import { useSyncExternalStore } from "react";
import { bootHealth, freshHealth } from "./health";

/** Ce que ce serveur sait calculer (lot « MVP en ligne sans GPU », 19 oct.). On décide sur les
 *  CAPACITÉS réelles, lues dans /api/health, et non sur le seul mode : la machine d'augus (avec
 *  Blender et les poids) ne change pas, et une installation partielle reste honnête. Relu
 *  régulièrement : un poste de calcul qui se connecte en cours de session (B3, futur champ
 *  `compute` de health) rend les fonctions disponibles sans recharger la page. */

export interface Capabilities {
  /** En ligne (comptes actifs ou API exposée) : les manques se disent « bientôt en ligne ». */
  online: boolean;
  /** Rendus réalistes (Blender, ou un poste de calcul). */
  renders: boolean;
  /** Plan depuis une photo : en ligne, seulement avec l'estimation de profondeur (sans elle,
   *  le plan par lignes de fuite est souvent faux ou vide : décision PO du 19 oct.). */
  photoPlan: boolean;
  /** KAN-79 : avec les comptes, la profondeur se calcule dans le navigateur (modèle servi
   *  par l'API) et le serveur la relit sans modèle. */
  browserDepth: boolean;
  /** Modèle d'images (finition, inspiration, rendus contextualisés, Version catalogue). */
  images: boolean;
}

const DEPTH = "depth_anything_v2_metric_indoor_small@1.0";
const WEB_DEPTH = "depth_anything_v2_metric_indoor_small_web@1.0";
const IMAGE_MODELS = ["stable_diffusion_1_5@1.5", "controlnet_depth_sd15@1.1"];
const REFRESH_MS = 60_000;

interface Health {
  exposed?: boolean;
  accounts?: boolean;
  blender?: boolean;
  weights?: string[];
  /** Futur (B3) : ce qu'un poste de calcul connecté apporte. */
  compute?: { renders?: boolean; depth?: boolean; images?: boolean } | null;
}

/** Tant que health n'a pas répondu : tout disponible (le mode local d'avant) ; l'application
 *  attend la première lecture avant de s'afficher, donc rien ne clignote. */
const FULL: Capabilities = {
  online: false,
  renders: true,
  photoPlan: true,
  browserDepth: false,
  images: true,
};

export function capabilitiesFrom(h: Health): Capabilities {
  const weights = new Set(h.weights ?? []);
  const online = Boolean(h.exposed || h.accounts);
  const depth = Boolean(h.compute?.depth ?? weights.has(DEPTH));
  const browserDepth = Boolean(h.accounts) && weights.has(WEB_DEPTH);
  return {
    online,
    renders: Boolean(h.compute?.renders ?? h.blender),
    photoPlan: !online || depth || browserDepth,
    browserDepth,
    images: Boolean(
      h.compute?.images ?? IMAGE_MODELS.every((id) => weights.has(id)),
    ),
  };
}

let current: Capabilities = FULL;
const listeners = new Set<() => void>();

function set(next: Capabilities): void {
  if (
    next.online === current.online &&
    next.renders === current.renders &&
    next.photoPlan === current.photoPlan &&
    next.browserDepth === current.browserDepth &&
    next.images === current.images
  )
    return;
  current = next;
  for (const listener of listeners) listener();
}

/** Lecture immédiate (hors React : décisions dans useProject). */
export function currentCapabilities(): Capabilities {
  return current;
}

/** `boot` : la réponse partagée du démarrage (aucune requête de plus, même si le démarrage
 *  se rejoue, comme en mode strict de React) ; sinon une relecture du serveur. */
export async function refreshCapabilities(
  { boot = false }: { boot?: boolean } = {},
): Promise<Capabilities> {
  try {
    set(capabilitiesFrom((await (boot ? bootHealth() : freshHealth())) as Health));
  } catch {
    /* serveur injoignable : on garde la dernière lecture */
  }
  return current;
}

let started = false;
/** Relecture toutes les 60 s et au retour sur l'onglet. */
export function watchCapabilities(): void {
  if (started) return;
  started = true;
  window.setInterval(() => void refreshCapabilities(), REFRESH_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void refreshCapabilities();
  });
}

export function useCapabilities(): Capabilities {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => current,
  );
}
