/** KAN-98 L2 : les paliers de dézoom de l'espace de travail (maquette E) : le détail complet,
 *  les vignettes (titre gardé ; compteurs, consignes et « auto » masqués), les titres seuls
 *  (tuiles pleines à la couleur du type, sans étiquettes de lien).
 *
 *  La maquette donne ses seuils (60 % et 25 %) pour des nodes de 360 px à 100 % ; les nodes
 *  de l'espace de travail sont plus larges (780, ou 624 pour un espace d'avant L2). Le palier
 *  se lit donc sur la largeur À L'ÉCRAN du node le plus étroit : un node de 360 px à l'écran
 *  est au palier « détail » de la maquette à 100 %. */
export type ZoomTier = "detail" | "thumbs" | "titles";

/** La largeur d'un node de la maquette A, à 100 %. */
export const MAQUETTE_NODE_PX = 360;

export function zoomTier(zoom: number, nodeWidth = MAQUETTE_NODE_PX): ZoomTier {
  const scale = (zoom * nodeWidth) / MAQUETTE_NODE_PX;
  if (scale >= 0.6) return "detail";
  return scale >= 0.25 ? "thumbs" : "titles";
}

/** Le nom du palier, affiché dans le zoom (« 45 % · vignettes »). */
export const TIER_NAMES: Record<ZoomTier, string> = {
  detail: "détail",
  thumbs: "vignettes",
  titles: "titres",
};
