/** Réglages de l'aperçu 3D (E3) : plan de coupe, maison de poupée, rayons X ; mémorisés par
 *  projet dans le navigateur (préférence d'affichage, pas une donnée du projet). */

export type SectionAxis = "none" | "z" | "x" | "y";

export interface View3D {
  /** z : coupe horizontale (on garde ce qui est sous la hauteur) ; x, y : coupe verticale
   *  (on garde ce qui est en deçà de la position le long de l'axe du plan). */
  section: SectionAxis;
  /** Hauteur (z) ou position le long de l'axe (x, y), en mètres. */
  value: number;
  /** Garder l'autre côté du plan de coupe (aperçu seulement : le rendu Blender choisit le
   *  côté d'après la caméra). */
  flip: boolean;
  /** Toit, charpente, plafonds et murs tournés vers la caméra masqués. */
  dollhouse: boolean;
  /** Tout semi-transparent. */
  xray: boolean;
}

export const DEFAULT_VIEW3D: View3D = {
  section: "none",
  value: 1.2,
  flip: false,
  dollhouse: false,
  xray: false,
};

function key(projectId: string): string {
  return `openplan:view3d:${projectId}`;
}

export function loadView3D(projectId: string): View3D {
  try {
    const raw = window.localStorage.getItem(key(projectId));
    if (!raw) return DEFAULT_VIEW3D;
    return { ...DEFAULT_VIEW3D, ...(JSON.parse(raw) as Partial<View3D>) };
  } catch {
    return DEFAULT_VIEW3D;
  }
}

export function saveView3D(projectId: string, v: View3D): void {
  try {
    window.localStorage.setItem(key(projectId), JSON.stringify(v));
  } catch {
    // Stockage indisponible (navigation privée) : le réglage vaut pour la session.
  }
}
