/** Placement des étiquettes du plan sans chevauchement (audit responsive, défaut 1.3).
 *
 *  Les cotes restent collées à leur mur (obstacles fixes) ; les étiquettes de pièce, de
 *  meuble et de variante fantôme sont décalées verticalement, par petits pas, jusqu'à ne
 *  plus recouvrir ni une cote ni une étiquette déjà posée. Le décalage est borné : une
 *  étiquette reste près de ce qu'elle nomme. Largeur de texte estimée (pas de mesure DOM). */

export interface LabelBox {
  /** Centre horizontal (text-anchor middle) et ligne de base, en pixels d'écran. */
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LabelRequest extends LabelBox {
  key: string;
  /** Pas maximal de décalage (en hauteurs d'étiquette) ; 0 = fixe. */
  maxSteps?: number;
  /** Obstacles propres à cette étiquette (ex. escaliers et meubles posés pour le nom d'une
   *  pièce), en plus des cotes et des étiquettes déjà posées. */
  avoid?: LabelBox[];
}

/** Boîte d'écran (même convention que les étiquettes : centre, bas) d'un contour, élargie de
 *  `margin` pixels (les jambages du texte passent sous la ligne de base). */
export function boxOf(
  points: { x: number; y: number }[],
  margin = 5,
): LabelBox | null {
  if (points.length === 0) return null;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const x0 = Math.min(...xs) - margin;
  const x1 = Math.max(...xs) + margin;
  const y0 = Math.min(...ys) - margin;
  const y1 = Math.max(...ys) + margin;
  return { x: (x0 + x1) / 2, y: y1, width: x1 - x0, height: y1 - y0 };
}

const CHAR_PX = 6.2;
const PADDING = 2;

/** Largeur estimée d'un texte de 10-11 px : suffisant pour éviter les collisions. */
export function textWidth(text: string, charPx = CHAR_PX): number {
  return Math.max(8, text.length * charPx);
}

function overlaps(a: LabelBox, b: LabelBox): boolean {
  const ax0 = a.x - a.width / 2 - PADDING;
  const ax1 = a.x + a.width / 2 + PADDING;
  const ay0 = a.y - a.height - PADDING;
  const ay1 = a.y + PADDING;
  const bx0 = b.x - b.width / 2;
  const bx1 = b.x + b.width / 2;
  const by0 = b.y - b.height;
  const by1 = b.y;
  return ax0 < bx1 && bx0 < ax1 && ay0 < by1 && by0 < ay1;
}

/** Positions retenues, par clé : la demande telle quelle, ou décalée vers le bas puis le
 *  haut (1, −1, 2, −2…) jusqu'à la première place libre. Les obstacles ne bougent pas. */
export function layoutLabels(
  requests: LabelRequest[],
  obstacles: LabelBox[],
): Map<string, { x: number; y: number }> {
  const placed: LabelBox[] = [...obstacles];
  const out = new Map<string, { x: number; y: number }>();
  for (const req of requests) {
    const steps = req.maxSteps ?? 2;
    const dy = req.height + 2 * PADDING;
    let chosen: LabelBox = req;
    const offsets = [0];
    for (let k = 1; k <= steps; k++) offsets.push(k, -k);
    const blockers = req.avoid ? [...placed, ...req.avoid] : placed;
    for (const k of offsets) {
      const candidate = { ...req, y: req.y + k * dy };
      if (!blockers.some((p) => overlaps(candidate, p))) {
        chosen = candidate;
        break;
      }
      // Faute de place, la dernière position essayée (la plus éloignée) est gardée ; une
      // étiquette à obstacles propres (nom de pièce) reste alors au centre de sa pièce.
      chosen = req.avoid ? req : candidate;
    }
    placed.push(chosen);
    out.set(req.key, { x: chosen.x, y: chosen.y });
  }
  return out;
}
