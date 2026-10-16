/** Symboles normalisés des ouvertures en plan (E5) : jumeau exact de
 *  `openplan/geom/opening_symbols.py` (mêmes règles, mêmes constantes), pour le plan en direct
 *  et les vignettes de la bibliothèque. Coordonnées du niveau (mètres, Y vers le haut). */

import type { Level, Opening, Point2 } from "../api/types.gen";

export type OpeningKindT = Opening["kind"];

const FOLD_PANELS = 4;
const FOLD_ANGLE = (60 * Math.PI) / 180;
const SLIDE_GAP_M = 0.03;
const RAIL_GAP_M = 0.06;
const WINDOW_OVERLAP = 0.55;

const SWING_KINDS: ReadonlySet<string> = new Set([
  "door",
  "double_door",
  "folding_door",
  "french_window",
]);
const STACK_KINDS: ReadonlySet<string> = new Set([
  "sliding_door",
  "pocket_door",
]);

export interface SymbolLine {
  points: Point2[];
  /** leaf | glass | rail | arrow | hidden */
  style: string;
}
export interface SymbolArc {
  center: Point2;
  radius: number;
  startDeg: number;
  /** Signé, sens direct du repère du niveau. */
  extentDeg: number;
}
export interface OpeningSymbol {
  lines: SymbolLine[];
  arcs: SymbolArc[];
}

/** Ce que le symbole lit d'une ouverture (une ouverture posée ou un fantôme de pose). */
export interface OpeningLike {
  kind: OpeningKindT;
  offset: number;
  width: number;
  swing?: {
    hinge: "left" | "right";
    direction: "inward" | "outward";
  } | null;
  leaves?: number;
  split?: number;
}

const add = (p: Point2, v: Point2, k = 1): Point2 => ({
  x: p.x + v.x * k,
  y: p.y + v.y * k,
});
const deg = (v: Point2) => (Math.atan2(v.y, v.x) * 180) / Math.PI;

export const isSwingKind = (kind: string) => SWING_KINDS.has(kind);
export const isStackKind = (kind: string) => STACK_KINDS.has(kind);

export function isPair(o: OpeningLike): boolean {
  return (
    o.kind === "double_door" ||
    (o.kind === "french_window" && (o.leaves ?? 1) === 2)
  );
}

/** Vantaux battants : gond, direction du bord libre, largeur. */
export function leavesOf(
  o: OpeningLike,
  p0: Point2,
  p1: Point2,
  u: Point2,
): { hinge: Point2; toward: Point2; width: number }[] {
  if (!SWING_KINDS.has(o.kind)) return [];
  const left = !o.swing || o.swing.hinge === "left";
  const w = o.width;
  const back = { x: -u.x, y: -u.y };
  if (!isPair(o))
    return left
      ? [{ hinge: p0, toward: u, width: w }]
      : [{ hinge: p1, toward: back, width: w }];
  const main = w * (o.split ?? 0.5);
  return left
    ? [
        { hinge: p0, toward: u, width: main },
        { hinge: p1, toward: back, width: w - main },
      ]
    : [
        { hinge: p1, toward: back, width: main },
        { hinge: p0, toward: u, width: w - main },
      ];
}

/** Tronçon du mur où s'efface le vantail ouvert (applique, galandage). */
export function stackSpan(o: OpeningLike): [number, number] | null {
  if (!STACK_KINDS.has(o.kind)) return null;
  const left = !o.swing || o.swing.hinge === "left";
  return left
    ? [o.offset - o.width, o.offset]
    : [o.offset + o.width, o.offset + 2 * o.width];
}

function arrow(start: Point2, dir: Point2, length: number): SymbolLine[] {
  const tip = add(start, dir, length);
  const head = Math.min(0.12, length / 3);
  const out: SymbolLine[] = [{ points: [start, tip], style: "arrow" }];
  for (const sign of [1, -1]) {
    const a = ((155 * Math.PI) / 180) * sign;
    const d = {
      x: dir.x * Math.cos(a) - dir.y * Math.sin(a),
      y: dir.x * Math.sin(a) + dir.y * Math.cos(a),
    };
    out.push({ points: [tip, add(tip, d, head)], style: "arrow" });
  }
  return out;
}

/** Le symbole : `origin` départ de l'axe du mur, `u` sa direction unitaire, `half` sa
 *  demi-épaisseur. */
export function openingSymbol(
  o: OpeningLike,
  origin: Point2,
  u: Point2,
  half: number,
): OpeningSymbol {
  const n = { x: -u.y, y: u.x };
  const p0 = add(origin, u, o.offset);
  const p1 = add(p0, u, o.width);
  const side = o.swing?.direction === "outward" ? -1 : 1;
  const out = { x: n.x * side, y: n.y * side };
  const sym: OpeningSymbol = { lines: [], arcs: [] };
  const w = o.width;
  const at = (s: number, k = 0) => add(add(origin, u, s), n, k);

  if (o.kind === "folding_door") {
    const leaf = leavesOf(o, p0, p1, u)[0]!;
    const panel = w / FOLD_PANELS;
    const along = panel * Math.cos(FOLD_ANGLE);
    const depth = panel * Math.sin(FOLD_ANGLE);
    const pts: Point2[] = [leaf.hinge];
    for (let i = 1; i <= FOLD_PANELS; i++)
      pts.push(
        add(add(leaf.hinge, leaf.toward, i * along), out, i % 2 ? depth : 0),
      );
    sym.lines.push({ points: pts, style: "leaf" });
    sym.lines.push({
      points: [pts[pts.length - 1]!, add(leaf.hinge, leaf.toward, w)],
      style: "hidden",
    });
  } else if (SWING_KINDS.has(o.kind)) {
    for (const leaf of leavesOf(o, p0, p1, u)) {
      sym.lines.push({
        points: [leaf.hinge, add(leaf.hinge, out, leaf.width)],
        style: "leaf",
      });
      const start = deg(leaf.toward);
      const extent = ((((deg(out) - start + 540) % 360) + 360) % 360) - 180;
      sym.arcs.push({
        center: leaf.hinge,
        radius: leaf.width,
        startDeg: start,
        extentDeg: extent,
      });
    }
    if (o.kind === "french_window")
      for (const gap of [half / 3, -half / 3])
        sym.lines.push({
          points: [at(o.offset, gap), at(o.offset + w, gap)],
          style: "glass",
        });
  } else if (STACK_KINDS.has(o.kind)) {
    const span = stackSpan(o)!;
    const toward = span[0] < o.offset ? { x: -u.x, y: -u.y } : u;
    const mid = o.offset + w / 2;
    if (o.kind === "sliding_door") {
      const f = side * (half + SLIDE_GAP_M);
      const r = side * (half + RAIL_GAP_M);
      sym.lines.push({
        points: [at(o.offset, f), at(o.offset + w, f)],
        style: "leaf",
      });
      const lo = Math.min(span[0], o.offset);
      const hi = Math.max(span[1], o.offset + w);
      sym.lines.push({ points: [at(lo, r), at(hi, r)], style: "rail" });
      sym.lines.push(...arrow(at(mid, f + side * 0.12), toward, 0.4 * w));
    } else {
      sym.lines.push({ points: [p0, p1], style: "leaf" });
      const k = half * 0.6;
      sym.lines.push({
        points: [
          at(span[0], k),
          at(span[1], k),
          at(span[1], -k),
          at(span[0], -k),
          at(span[0], k),
        ],
        style: "hidden",
      });
      sym.lines.push(...arrow(at(mid, half + 0.12), toward, 0.4 * w));
    }
  } else if (o.kind === "sliding_window") {
    const k = half / 3;
    const cover = WINDOW_OVERLAP * w;
    sym.lines.push({
      points: [at(o.offset, k), at(o.offset + cover, k)],
      style: "glass",
    });
    sym.lines.push({
      points: [at(o.offset + w - cover, -k), at(o.offset + w, -k)],
      style: "glass",
    });
    sym.lines.push(...arrow(at(o.offset + w / 2, half + 0.12), u, 0.3 * w));
  }
  return sym;
}

export const OPENING_LABELS: Record<OpeningKindT, string> = {
  door: "Porte",
  window: "Fenêtre",
  passage: "Passage",
  double_door: "Double porte",
  sliding_door: "Porte coulissante",
  pocket_door: "Porte à galandage",
  folding_door: "Porte pliante",
  french_window: "Porte-fenêtre",
  sliding_window: "Baie vitrée coulissante",
};

/** Carte de la bibliothèque « Portes et fenêtres » : une sorte et ses cotes typiques. */
export interface OpeningCard {
  id: string;
  kind: OpeningKindT;
  label: string;
  width: number;
  height: number;
  sill: number;
  leaves?: number;
  split?: number;
}

export const OPENING_CARDS: OpeningCard[] = [
  {
    id: "door",
    kind: "door",
    label: "Porte simple",
    width: 0.83,
    height: 2.04,
    sill: 0,
  },
  {
    id: "double_door",
    kind: "double_door",
    label: "Double porte",
    width: 1.4,
    height: 2.04,
    sill: 0,
    leaves: 2,
    split: 0.5,
  },
  {
    id: "double_door_third",
    kind: "double_door",
    label: "Double porte 1/3–2/3",
    width: 1.2,
    height: 2.04,
    sill: 0,
    leaves: 2,
    split: 2 / 3,
  },
  {
    id: "sliding_door",
    kind: "sliding_door",
    label: "Porte coulissante en applique",
    width: 0.83,
    height: 2.04,
    sill: 0,
  },
  {
    id: "pocket_door",
    kind: "pocket_door",
    label: "Porte à galandage",
    width: 0.83,
    height: 2.04,
    sill: 0,
  },
  {
    id: "folding_door",
    kind: "folding_door",
    label: "Porte pliante",
    width: 0.9,
    height: 2.04,
    sill: 0,
  },
  {
    id: "french_window",
    kind: "french_window",
    label: "Porte-fenêtre",
    width: 0.95,
    height: 2.15,
    sill: 0,
    leaves: 1,
  },
  {
    id: "french_window_pair",
    kind: "french_window",
    label: "Porte-fenêtre double",
    width: 1.4,
    height: 2.15,
    sill: 0,
    leaves: 2,
    split: 0.5,
  },
  {
    id: "sliding_window",
    kind: "sliding_window",
    label: "Baie vitrée coulissante",
    width: 2.4,
    height: 2.15,
    sill: 0,
    leaves: 2,
  },
  {
    id: "window",
    kind: "window",
    label: "Fenêtre",
    width: 1.2,
    height: 1.25,
    sill: 0.9,
  },
];

/** Gonds et sens par défaut d'une carte : aucun pour une fenêtre ou une baie. */
export function defaultSwing(
  kind: OpeningKindT,
  direction: "inward" | "outward" = "inward",
): { hinge: "left"; direction: "inward" | "outward"; angle: number } | null {
  return kind === "window" || kind === "passage" || kind === "sliding_window"
    ? null
    : { hinge: "left", direction, angle: 1.5708 };
}

function inside(p: Point2, poly: Point2[]): boolean {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]!;
    const b = poly[j]!;
    if (
      a.y > p.y !== b.y > p.y &&
      p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x
    )
      hit = !hit;
  }
  return hit;
}

/** Côté du mur où se trouve la pièce (E5c), à l'abscisse `along` : +1 côté normale gauche
 *  (« inward » du format), −1 de l'autre, 0 sans pièce de part et d'autre. Ainsi
 *  « intérieur » veut dire « vers la pièce », quel que soit le sens de tracé du mur. */
export function roomSide(
  a: Point2,
  b: Point2,
  thickness: number,
  along: number,
  rooms: Point2[][],
): number {
  const len = Math.hypot(b.x - a.x, b.y - a.y);
  if (len < 1e-9) return 0;
  const u = { x: (b.x - a.x) / len, y: (b.y - a.y) / len };
  const probe = thickness / 2 + 0.05;
  const at = (k: number) => ({
    x: a.x + u.x * along - u.y * k,
    y: a.y + u.y * along + u.x * k,
  });
  for (const poly of rooms) {
    if (inside(at(probe), poly)) return 1;
    if (inside(at(-probe), poly)) return -1;
  }
  return 0;
}

/** `roomSide` d'une ouverture posée, d'après les sommets du niveau. */
export function roomSideOf(
  level: Level,
  rooms: Point2[][],
  o: { host_wall: string; offset: number; width: number },
): number {
  const w = level.elements[o.host_wall];
  if (!w || w.type !== "wall") return 0;
  const a = level.vertices[w.start_vertex]?.position;
  const b = level.vertices[w.end_vertex]?.position;
  if (!a || !b) return 0;
  return roomSide(a, b, w.thickness, o.offset + o.width / 2, rooms);
}
