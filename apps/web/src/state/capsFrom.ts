/** Ce que ce serveur sait calculer (lot « MVP en ligne sans GPU », 19 oct.), lu dans
 *  /api/health ; la partie pure de capabilities.ts (testée par `node --test`). On décide sur
 *  les CAPACITÉS réelles, et non sur le seul mode : la machine d'augus (avec Blender et les
 *  poids) ne change pas, et une installation partielle reste honnête. */

export interface Capabilities {
  /** KAN-134 : /api/health a répondu. Faux : rien n'est encore su, et rien n'est offert (un
   *  serveur muet ne fait plus croire à des rendus) ; un nouvel essai suit. */
  known: boolean;
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

export interface Health {
  exposed?: boolean;
  accounts?: boolean;
  blender?: boolean;
  weights?: string[];
  /** Futur (B3) : ce qu'un poste de calcul connecté apporte. */
  compute?: { renders?: boolean; depth?: boolean; images?: boolean } | null;
}

/** KAN-134 : tant que /api/health n'a pas répondu, « inconnu » : rien d'offert. (Avant :
 *  « tout disponible », si bien qu'un serveur muet faisait croire à des rendus Blender et
 *  cachait le node des rendus réalistes.) */
export const UNKNOWN: Capabilities = {
  known: false,
  online: false,
  renders: false,
  photoPlan: false,
  browserDepth: false,
  images: false,
};

export function capabilitiesFrom(h: Health): Capabilities {
  const weights = new Set(h.weights ?? []);
  const online = Boolean(h.exposed || h.accounts);
  const depth = Boolean(h.compute?.depth ?? weights.has(DEPTH));
  const browserDepth = Boolean(h.accounts) && weights.has(WEB_DEPTH);
  return {
    known: true,
    online,
    renders: Boolean(h.compute?.renders ?? h.blender),
    photoPlan: !online || depth || browserDepth,
    browserDepth,
    images: Boolean(
      h.compute?.images ?? IMAGE_MODELS.every((id) => weights.has(id)),
    ),
  };
}

export function sameCapabilities(a: Capabilities, b: Capabilities): boolean {
  return (
    a.known === b.known &&
    a.online === b.online &&
    a.renders === b.renders &&
    a.photoPlan === b.photoPlan &&
    a.browserDepth === b.browserDepth &&
    a.images === b.images
  );
}
