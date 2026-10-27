/** KAN-91 (retours de la MEP 14) : la géométrie des liens, sans React (testée seule).
 *
 *  - Un port d'entrée par sorte de lien : un node Rendus reçoit son plan par la GAUCHE et
 *    son Moodboard par le HAUT (KAN-98 L1b) ; un plan reçoit ses photos par la gauche. La
 *    source part du côté qui fait face à ce port (par son bas, posée au-dessus).
 *  - Plusieurs fils sur le même côté d'une carte s'y répartissent (aucune flèche n'arrive sur
 *    une autre), dans l'ordre de hauteur de l'autre bout (ils ne se croisent pas en arrivant).
 *  - Un fil ne passe jamais sous une carte : courbe directe si la voie est libre, sinon un
 *    chemin à angles arrondis par un couloir libre au-dessus ou au-dessous des cartes.
 *  - La pastille se pose au milieu du fil, jamais sur une carte. */

import type { LinkKind } from "../api/client";

export interface Rect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Point {
  x: number;
  y: number;
}

/** KAN-98 L1b : « top » (le Moodboard arrive en haut de Rendus) et « bottom » (il en part,
 *  posé au-dessus) ; les autres liens restent horizontaux. */
export type Side = "left" | "right" | "top" | "bottom";
const vertical = (side: Side) => side === "top" || side === "bottom";

export interface WireInput {
  id: string;
  kind: LinkKind;
  s: Rect;
  t: Rect;
}

export interface WireEnds {
  a: Point;
  aSide: Side;
  b: Point;
  bSide: Side;
  /** Rang du fil parmi ceux qui partagent sa source ou sa cible : son couloir propre. */
  lane?: number;
}

export interface Route {
  d: string;
  /** Où poser la pastille : au milieu du fil, hors des cartes. */
  mid: Point;
  /** La place libre autour de `mid` (distance à la carte la plus proche). */
  room: number;
}

/** Le côté par lequel un lien entre dans sa cible. KAN-98 : le Moodboard guide le style par
 *  le HAUT du node Rendus (il est posé au-dessus de lui) ; le plan y entre par la gauche. */
export function entrySide(kind: LinkKind): Side {
  return kind === "guides_style" ? "top" : "left";
}

const cx = (r: Rect) => r.x + r.w / 2;
const cy = (r: Rect) => r.y + r.h / 2;
const edge = (r: Rect, side: Side) => (side === "left" ? r.x : r.x + r.w);

/** Le côté d'où part la source : celui qui fait face au port d'entrée de la cible. Vers un
 *  port du haut, une source posée au-dessus part par son bas ; sinon par le côté qui fait
 *  face à la cible. */
export function exitSide(s: Rect, t: Rect, kind: LinkKind): Side {
  const into = entrySide(kind);
  if (into === "top")
    return s.y + s.h <= t.y ? "bottom" : cx(s) <= cx(t) ? "right" : "left";
  return cx(s) <= edge(t, into) ? "right" : "left";
}

/** Le point d'un port : le long d'un côté vertical (à hauteur `k`), ou d'un côté horizontal
 *  (haut, bas : à l'abscisse `k`). */
function port(r: Rect, side: Side, k: number): Point {
  if (side === "top") return { x: k, y: r.y };
  if (side === "bottom") return { x: k, y: r.y + r.h };
  return { x: edge(r, side), y: k };
}

/** Les deux bouts de chaque fil, ports répartis le long de chaque côté occupé. */
export function wireEnds(wires: WireInput[]): Map<string, WireEnds> {
  const slots = new Map<string, { id: string; other: number }[]>();
  const sides = new Map<string, { aSide: Side; bSide: Side }>();
  const push = (key: string, id: string, other: number) => {
    const list = slots.get(key) ?? [];
    list.push({ id, other });
    slots.set(key, list);
  };
  for (const w of wires) {
    const bSide = entrySide(w.kind);
    const aSide = exitSide(w.s, w.t, w.kind);
    sides.set(w.id, { aSide, bSide });
    // Ordre sur un côté : la hauteur de l'autre bout (côtés verticaux), son abscisse (haut,
    // bas) : les fils ne se croisent pas en arrivant.
    push(`out:${w.s.id}:${aSide}`, w.id, vertical(aSide) ? cx(w.t) : cy(w.t));
    push(`in:${w.t.id}:${bSide}`, w.id, vertical(bSide) ? cx(w.s) : cy(w.s));
  }
  const rank = (key: string, id: string) => {
    const list = [...(slots.get(key) ?? [])].sort((p, q) => p.other - q.other);
    return { i: list.findIndex((p) => p.id === id), n: list.length };
  };
  const at = (key: string, id: string, r: Rect, side: Side) => {
    const { i, n } = rank(key, id);
    return vertical(side)
      ? r.x + (r.w * (i + 1)) / (n + 1)
      : r.y + (r.h * (i + 1)) / (n + 1);
  };
  const out = new Map<string, WireEnds>();
  for (const w of wires) {
    const { aSide, bSide } = sides.get(w.id)!;
    const from = `out:${w.s.id}:${aSide}`;
    const to = `in:${w.t.id}:${bSide}`;
    out.set(w.id, {
      a: port(w.s, aSide, at(from, w.id, w.s, aSide)),
      aSide,
      b: port(w.t, bSide, at(to, w.id, w.t, bSide)),
      bSide,
      lane: rank(from, w.id).i + rank(to, w.id).i,
    });
  }
  return out;
}

/** Un segment horizontal (y, de x1 à x2) ou vertical traverse-t-il un rectangle ? */
function hits(p: Point, q: Point, r: Rect, margin: number): boolean {
  const left = Math.min(p.x, q.x);
  const right = Math.max(p.x, q.x);
  const top = Math.min(p.y, q.y);
  const bottom = Math.max(p.y, q.y);
  return (
    right > r.x - margin &&
    left < r.x + r.w + margin &&
    bottom > r.y - margin &&
    top < r.y + r.h + margin
  );
}

const STUB = 32;
const LANE_STEP = 18;

function bezier(a: Point, c1: Point, c2: Point, b: Point, t: number): Point {
  const u = 1 - t;
  return {
    x:
      u * u * u * a.x +
      3 * u * u * t * c1.x +
      3 * u * t * t * c2.x +
      t * t * t * b.x,
    y:
      u * u * u * a.y +
      3 * u * u * t * c1.y +
      3 * u * t * t * c2.y +
      t * t * t * b.y,
  };
}

/** La distance d'un point à la carte la plus proche (0 s'il est dessus). */
export function room(p: Point, cards: Rect[]): number {
  let best = Infinity;
  for (const r of cards) {
    const dx = Math.max(r.x - p.x, 0, p.x - (r.x + r.w));
    const dy = Math.max(r.y - p.y, 0, p.y - (r.y + r.h));
    best = Math.min(best, Math.hypot(dx, dy));
  }
  return best;
}

/** Le premier point du fil, en partant du milieu, à `clearance` de toute carte ; sinon le
 *  point le plus dégagé (la pastille s'y fera plus petite, `room`). */
function freePoint(
  at: (t: number) => Point,
  cards: Rect[],
  clearance: number,
): { mid: Point; room: number } {
  let best = { mid: at(0.5), room: -1 };
  for (const t of [0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82]) {
    const p = at(t);
    const free = room(p, cards);
    if (free >= clearance) return { mid: p, room: free };
    if (free > best.room) best = { mid: p, room: free };
  }
  return best;
}

/** Le couloir horizontal libre le plus proche (au-dessus ou au-dessous) pour aller de x1 à
 *  x2, à `gap` des cartes (la place de la pastille) ; `lane` décale les fils qui partagent
 *  un couloir. */
function freeLane(
  x1: number,
  x2: number,
  y1: number,
  y2: number,
  cards: Rect[],
  lane: number,
  gap: number,
): number {
  const span = (y: number) =>
    cards.filter((r) => hits({ x: x1, y }, { x: x2, y }, r, gap / 2));
  let up = Math.min(y1, y2);
  for (let guard = 0; guard < 50; guard++) {
    const block = span(up);
    if (!block.length) break;
    up = Math.min(...block.map((r) => r.y)) - gap;
  }
  let down = Math.max(y1, y2);
  for (let guard = 0; guard < 50; guard++) {
    const block = span(down);
    if (!block.length) break;
    down = Math.max(...block.map((r) => r.y + r.h)) + gap;
  }
  const costUp = Math.abs(y1 - up) + Math.abs(y2 - up);
  const costDown = Math.abs(y1 - down) + Math.abs(y2 - down);
  return costUp <= costDown ? up - lane * LANE_STEP : down + lane * LANE_STEP;
}

/** Un chemin à angles arrondis par les points donnés. */
function rounded(points: Point[]): string {
  let d = `M ${points[0]!.x} ${points[0]!.y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i - 1]!;
    const c = points[i]!;
    const n = points[i + 1]!;
    const r = Math.min(
      16,
      Math.hypot(c.x - p.x, c.y - p.y) / 2,
      Math.hypot(n.x - c.x, n.y - c.y) / 2,
    );
    const into = (q: Point) => {
      const len = Math.hypot(q.x - c.x, q.y - c.y) || 1;
      return {
        x: c.x + ((q.x - c.x) * r) / len,
        y: c.y + ((q.y - c.y) * r) / len,
      };
    };
    const s = into(p);
    const e = into(n);
    d += ` L ${s.x} ${s.y} Q ${c.x} ${c.y} ${e.x} ${e.y}`;
  }
  const last = points[points.length - 1]!;
  return `${d} L ${last.x} ${last.y}`;
}

/** Le tracé d'un fil de `a` (côté `aSide` de sa carte) à `b` (côté `bSide`), qui ne passe
 *  sous aucune des `cards` (toutes les cartes, source et cible comprises). `clearance` : la
 *  marge de la pastille ; `gap` : l'écart d'un couloir aux cartes (unités de l'espace de
 *  travail : la pastille garde sa taille à l'écran, l'appelant les agrandit au dézoom). */
export function route(
  ends: WireEnds,
  cards: Rect[],
  { clearance = 20, gap = 48, skip = [] as string[] } = {},
): Route {
  const { a, aSide, b, bSide, lane = 0 } = ends;
  if (vertical(bSide)) return routeDown(ends, cards, { clearance, gap, skip });
  const out = aSide === "right" ? 1 : -1;
  const back = bSide === "right" ? 1 : -1;
  // Les fils qui partagent une carte s'en écartent chacun de leur pas (verticales parallèles).
  const stub = STUB + lane * LANE_STEP;
  const a1 = { x: a.x + out * stub, y: a.y };
  const b1 = { x: b.x + back * stub, y: b.y };
  const others = cards.filter((r) => !skip.includes(r.id));
  // Courbe directe : de droite à gauche vers l'avant (même l'écart étroit entre un plan et
  // sa carte Rendus), voie libre entre les deux bouts.
  const forward = aSide === "right" && bSide === "left" && b.x > a.x + 8;
  const box = {
    id: "",
    x: a.x + 4,
    y: Math.min(a.y, b.y),
    w: Math.max(0, b.x - a.x - 8),
    h: Math.abs(b.y - a.y),
  };
  const blocked = others.some((r) =>
    hits({ x: box.x, y: box.y }, { x: box.x + box.w, y: box.y + box.h }, r, 8),
  );
  if (forward && !blocked) {
    const dx = Math.max(Math.min(60, b.x - a.x), (b.x - a.x) / 2);
    const c1 = { x: a.x + dx, y: a.y };
    const c2 = { x: b.x - dx, y: b.y };
    return {
      d: `M ${a.x} ${a.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${b.x} ${b.y}`,
      ...freePoint((t) => bezier(a, c1, c2, b, t), cards, clearance),
    };
  }
  // Sinon : sortir de la carte, rejoindre un couloir libre, revenir devant le port d'entrée.
  const y = freeLane(a1.x, b1.x, a.y, b.y, cards, lane, gap);
  const points = [a, a1, { x: a1.x, y }, { x: b1.x, y }, b1, b];
  const p = { x: a1.x, y };
  const q = { x: b1.x, y };
  return {
    d: rounded(points),
    ...freePoint((t) => ({ x: p.x + (q.x - p.x) * t, y }), cards, clearance),
  };
}

/** Le couloir horizontal libre le plus bas au-dessus de `y` (de x1 à x2), à `gap` des cartes. */
function laneAbove(
  x1: number,
  x2: number,
  y: number,
  cards: Rect[],
  lane: number,
  gap: number,
): number {
  let up = y;
  for (let guard = 0; guard < 50; guard++) {
    const block = cards.filter((r) =>
      hits({ x: x1, y: up }, { x: x2, y: up }, r, gap / 2),
    );
    if (!block.length) break;
    up = Math.min(...block.map((r) => r.y)) - gap;
  }
  return up - lane * LANE_STEP;
}

/** Un point du tracé `points` (de 0 à 1, par la longueur), hors du premier et du dernier
 *  segment (les talons collés aux cartes). */
function along(points: Point[]): (t: number) => Point {
  const inner = points.length > 3 ? points.slice(1, -1) : points;
  const lengths = inner
    .slice(1)
    .map((q, i) => Math.hypot(q.x - inner[i]!.x, q.y - inner[i]!.y));
  const total = lengths.reduce((s, l) => s + l, 0) || 1;
  return (t) => {
    let left = t * total;
    for (let i = 0; i < lengths.length; i++) {
      const l = lengths[i]!;
      if (left <= l || i === lengths.length - 1) {
        const k = l ? Math.min(1, left / l) : 0;
        const p = inner[i]!;
        const q = inner[i + 1]!;
        return { x: p.x + (q.x - p.x) * k, y: p.y + (q.y - p.y) * k };
      }
      left -= l;
    }
    return inner[0]!;
  };
}

/** Sans les points en double ni ceux au milieu d'une droite. */
function simplified(points: Point[]): Point[] {
  const out: Point[] = [];
  for (const p of points) {
    const last = out[out.length - 1];
    if (last && Math.abs(last.x - p.x) < 0.5 && Math.abs(last.y - p.y) < 0.5)
      continue;
    const prev = out[out.length - 2];
    if (
      prev &&
      last &&
      ((prev.x === last.x && last.x === p.x) ||
        (prev.y === last.y && last.y === p.y))
    )
      out.pop();
    out.push(p);
  }
  return out;
}

/** KAN-98 L1b : le plus court chemin à angles droits de `a1` (sortie de la source) au port
 *  du haut `b`, par des couloirs entre les cartes : une rangée (y1) au départ, une colonne
 *  (x), puis l'écart juste au-dessus de la cible (y2) d'où il descend sur le port. Aucun
 *  segment ne touche une carte (les talons ne touchent que leur propre carte). Null si
 *  aucun couloir ne passe. */
function corridorPath(
  a: Point,
  a1: Point,
  b: Point,
  stub: number,
  cards: Rect[],
  gap: number,
): Point[] | null {
  const margin = 6;
  const free = (p: Point, q: Point, among: Rect[]) =>
    !among.some((r) => hits(p, q, r, margin));
  // La carte dont le bord porte ce point (la source pour `a`, la cible pour `b`).
  const owner = (p: Point) => (r: Rect) =>
    p.x >= r.x - 0.5 &&
    p.x <= r.x + r.w + 0.5 &&
    p.y >= r.y - 0.5 &&
    p.y <= r.y + r.h + 0.5;
  const notSource = cards.filter((r) => !owner(a)(r));
  const notTarget = cards.filter((r) => !owner(b)(r));
  // Rangées : au-dessus et au-dessous de chaque carte, au milieu des écarts étroits.
  const rows = new Set<number>([b.y - stub, a1.y]);
  const cols = new Set<number>([a1.x, b.x]);
  for (const r of cards) {
    rows.add(r.y - gap / 2).add(r.y + r.h + gap / 2);
    cols.add(r.x - gap / 2).add(r.x + r.w + gap / 2);
    for (const o of cards) {
      const dy = o.y - (r.y + r.h);
      if (dy > 0 && dy < gap) rows.add(r.y + r.h + dy / 2);
      const dx = o.x - (r.x + r.w);
      if (dx > 0 && dx < gap) cols.add(r.x + r.w + dx / 2);
    }
  }
  // Le dernier palier : au-dessus de la cible, d'où la descente sur le port est libre.
  const lastRows = [...rows].filter(
    (y) => y < b.y - margin && free({ x: b.x, y }, b, notTarget),
  );
  // Le premier palier (p1), sans repasser sur la source. Par le bas, le fil descend tout
  // droit jusqu'à une rangée libre (un talon plus long que l'écart sous la source tomberait
  // sur la carte d'en dessous) ; de côté, il passe par son talon `a1`.
  const starts: { head: Point[]; p1: Point }[] = [];
  for (const y of rows) {
    if (a1.x === a.x) {
      const p1 = { x: a.x, y };
      if (y > a.y + margin && free(a, p1, notSource))
        starts.push({ head: [a], p1 });
    } else if (free(a, a1, notSource) && free(a1, { x: a1.x, y }, cards))
      starts.push({ head: [a, a1], p1: { x: a1.x, y } });
  }
  if (!lastRows.length || !starts.length) return null;
  let best: Point[] | null = null;
  let cost = Infinity;
  for (const y2 of lastRows)
    for (const { head, p1 } of starts)
      for (const x of cols) {
        const p2 = { x, y: p1.y };
        const p3 = { x, y: y2 };
        const p4 = { x: b.x, y: y2 };
        const length =
          Math.abs(p1.y - a.y) +
          Math.abs(x - p1.x) +
          Math.abs(y2 - p1.y) +
          Math.abs(b.x - x) +
          Math.abs(b.y - y2);
        if (length >= cost) continue;
        if (
          !free(p1, p2, cards) ||
          !free(p2, p3, cards) ||
          !free(p3, p4, cards)
        )
          continue;
        cost = length;
        best = [...head, p1, p2, p3, p4, b];
      }
  return best && simplified(best);
}

/** KAN-98 L1b : un fil qui entre par le HAUT de sa cible (le Moodboard vers le node Rendus).
 *  Source au-dessus (sortie par le bas) et voie libre : une courbe verticale directe ; sinon
 *  le fil sort de sa carte, rejoint un couloir libre au-dessus de la cible et descend sur le
 *  port. Il ne passe jamais sous une carte (hors les deux bouts). */
function routeDown(
  ends: WireEnds,
  cards: Rect[],
  { clearance, gap, skip }: { clearance: number; gap: number; skip: string[] },
): Route {
  const { a, aSide, b, lane = 0 } = ends;
  const stub = STUB + lane * LANE_STEP;
  const others = cards.filter((r) => !skip.includes(r.id));
  if (aSide === "bottom" && b.y > a.y + 8) {
    const blocked = others.some((r) =>
      hits(
        { x: Math.min(a.x, b.x), y: a.y + 4 },
        { x: Math.max(a.x, b.x), y: b.y - 4 },
        r,
        8,
      ),
    );
    if (!blocked) {
      const dy = Math.max(Math.min(60, b.y - a.y), (b.y - a.y) / 2);
      const c1 = { x: a.x, y: a.y + dy };
      const c2 = { x: b.x, y: b.y - dy };
      return {
        d: `M ${a.x} ${a.y} C ${c1.x} ${c1.y}, ${c2.x} ${c2.y}, ${b.x} ${b.y}`,
        ...freePoint((t) => bezier(a, c1, c2, b, t), cards, clearance),
      };
    }
  }
  // Sortie : vers le bas (source au-dessus), sinon à gauche ou à droite de la carte.
  const a1 =
    aSide === "bottom"
      ? { x: a.x, y: a.y + stub }
      : { x: a.x + (aSide === "right" ? stub : -stub), y: a.y };
  const path = corridorPath(a, a1, b, stub, cards, gap);
  if (path)
    return { d: rounded(path), ...freePoint(along(path), cards, clearance) };
  const y = laneAbove(
    Math.min(a1.x, b.x),
    Math.max(a1.x, b.x),
    Math.min(b.y - stub, aSide === "bottom" ? a1.y : b.y - stub),
    others,
    lane,
    gap,
  );
  const points =
    aSide === "bottom" && y >= a1.y
      ? [a, { x: a.x, y }, { x: b.x, y }, b]
      : [a, a1, { x: a1.x, y }, { x: b.x, y }, b];
  const p = { x: a1.x, y };
  const q = { x: b.x, y };
  return {
    d: rounded(points),
    ...freePoint((t) => ({ x: p.x + (q.x - p.x) * t, y }), cards, clearance),
  };
}

/** Les cartes métier qui en recouvrent une autre (au-delà d'une marge de 4 unités). */
export function overlapping(cards: Rect[]): Set<string> {
  const out = new Set<string>();
  for (let i = 0; i < cards.length; i++)
    for (let j = i + 1; j < cards.length; j++) {
      const p = cards[i]!;
      const q = cards[j]!;
      if (
        p.x + 4 < q.x + q.w &&
        q.x + 4 < p.x + p.w &&
        p.y + 4 < q.y + q.h &&
        q.y + 4 < p.y + p.h
      ) {
        out.add(p.id);
        out.add(q.id);
      }
    }
  return out;
}
