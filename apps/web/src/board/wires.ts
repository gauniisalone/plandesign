/** KAN-91 (retours de la MEP 14) : la géométrie des liens, sans React (testée seule).
 *
 *  - Un port d'entrée par sorte de lien : une carte Rendus reçoit son plan par la GAUCHE et
 *    ses inspirations par la DROITE ; un plan reçoit ses photos par la gauche. La source part
 *    du côté qui fait face à ce port.
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

export type Side = "left" | "right";

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

/** Le côté par lequel un lien entre dans sa cible. */
export function entrySide(kind: LinkKind): Side {
  return kind === "guides_style" ? "right" : "left";
}

const cx = (r: Rect) => r.x + r.w / 2;
const cy = (r: Rect) => r.y + r.h / 2;
const edge = (r: Rect, side: Side) => (side === "left" ? r.x : r.x + r.w);

/** Le côté d'où part la source : celui qui fait face au port d'entrée de la cible. */
export function exitSide(s: Rect, t: Rect, kind: LinkKind): Side {
  return cx(s) <= edge(t, entrySide(kind)) ? "right" : "left";
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
    push(`out:${w.s.id}:${aSide}`, w.id, cy(w.t));
    push(`in:${w.t.id}:${bSide}`, w.id, cy(w.s));
  }
  const rank = (key: string, id: string) => {
    const list = [...(slots.get(key) ?? [])].sort((p, q) => p.other - q.other);
    return { i: list.findIndex((p) => p.id === id), n: list.length };
  };
  const at = (key: string, id: string, r: Rect) => {
    const { i, n } = rank(key, id);
    return r.y + (r.h * (i + 1)) / (n + 1);
  };
  const out = new Map<string, WireEnds>();
  for (const w of wires) {
    const { aSide, bSide } = sides.get(w.id)!;
    const from = `out:${w.s.id}:${aSide}`;
    const to = `in:${w.t.id}:${bSide}`;
    out.set(w.id, {
      a: { x: edge(w.s, aSide), y: at(from, w.id, w.s) },
      aSide,
      b: { x: edge(w.t, bSide), y: at(to, w.id, w.t) },
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
