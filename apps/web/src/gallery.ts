import type { RenderEntry, RenderVariant } from "./api/client";

/** Galerie des rendus (US2) : noms et ordre des images d'un rendu. */
export const VARIANT_LABELS: Record<RenderVariant, string> = {
  blender: "Blender",
  finished: "Référence",
  inspiration: "Inspiration",
  catalogue: "Catalogue",
};

/** Les images présentes pour ce rendu, dans l'ordre de la bascule. */
export function variantsOf(entry: RenderEntry): RenderVariant[] {
  const out: RenderVariant[] = [];
  if (entry.inspiration) out.push("inspiration");
  if (entry.finished) out.push("finished");
  out.push("blender");
  if (entry.catalogue) out.push("catalogue");
  return out;
}

/** L'image montrée d'abord : un favori, sinon l'inspiration, sinon la référence. */
export function firstVariant(entry: RenderEntry): RenderVariant {
  const favorites = entry.favorites ?? [];
  const available = variantsOf(entry);
  return available.find((v) => favorites.includes(v)) ?? available[0]!;
}

export const titleOf = (entry: RenderEntry) =>
  entry.title || entry.camera_name;
