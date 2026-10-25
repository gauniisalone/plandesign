// KAN-91 (retours de la MEP 14) : ports des liens, fils qui ne passent jamais sous une carte,
// pastille jamais sur une carte, chevauchements montrés. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  entrySide,
  overlapping,
  route,
  wireEnds,
  type Point,
  type Rect,
  type WireInput,
} from "../src/board/wires.ts";

// La mise en page de départ : Photos et Inspirations à gauche, le plan au centre, les Rendus
// à droite (pixels de l'espace de travail).
const photos: Rect = { id: "photos", x: 0, y: 0, w: 624, h: 374 };
const insp: Rect = { id: "insp", x: 0, y: 422, w: 624, h: 374 };
const plan: Rect = { id: "plan", x: 720, y: 0, w: 900, h: 700 };
const renders: Rect = { id: "renders", x: 1668, y: 0, w: 624, h: 700 };
const cards = [photos, insp, plan, renders];

const wires: WireInput[] = [
  { id: "f", kind: "feeds_plan", s: photos, t: plan },
  { id: "v", kind: "renders_view", s: plan, t: renders },
  { id: "g", kind: "guides_style", s: insp, t: renders },
];

const inside = (p: Point, r: Rect) =>
  p.x > r.x && p.x < r.x + r.w && p.y > r.y && p.y < r.y + r.h;

/** Les points d'un chemin SVG (M, L, Q, C : leurs coordonnées), pour vérifier le tracé. */
function points(d: string): Point[] {
  const n = d.match(/-?\d+(\.\d+)?/g)!.map(Number);
  const out: Point[] = [];
  for (let i = 0; i + 1 < n.length; i += 2)
    out.push({ x: n[i]!, y: n[i + 1]! });
  return out;
}

test("un port d'entrée par sorte : le plan à gauche des Rendus, l'inspiration à droite", () => {
  assert.equal(entrySide("renders_view"), "left");
  assert.equal(entrySide("feeds_plan"), "left");
  assert.equal(entrySide("guides_style"), "right");
  const ends = wireEnds(wires);
  assert.equal(ends.get("v")!.b.x, renders.x);
  assert.equal(ends.get("g")!.b.x, renders.x + renders.w);
  assert.equal(ends.get("g")!.bSide, "right");
  assert.equal(ends.get("f")!.b.x, plan.x);
});

test("deux inspirations vers la même carte Rendus arrivent à deux hauteurs différentes", () => {
  const other: Rect = { id: "insp2", x: 0, y: 844, w: 624, h: 374 };
  const ends = wireEnds([
    ...wires,
    { id: "g2", kind: "guides_style", s: other, t: renders },
  ]);
  const a = ends.get("g")!.b;
  const b = ends.get("g2")!.b;
  assert.equal(a.x, b.x);
  assert.notEqual(a.y, b.y);
  // Dans l'ordre des sources : la plus haute arrive plus haut, les fils ne se croisent pas.
  assert.ok(a.y < b.y);
});

test("le fil d'inspiration contourne le plan et les Rendus, sans passer dessous", () => {
  const ends = wireEnds(wires).get("g")!;
  const r = route(ends, cards, { skip: ["insp", "renders"] });
  const pts = points(r.d);
  // Aucun coin du tracé dans une carte (hors le départ et l'arrivée, sur leurs bords).
  for (const p of pts.slice(1, -1))
    for (const c of cards)
      assert.ok(!inside(p, c), `${JSON.stringify(p)} dans ${c.id}`);
  // Il revient devant le bord droit des Rendus et y entre vers la gauche.
  const last = pts[pts.length - 1]!;
  assert.deepEqual(last, ends.b);
  assert.ok(pts[pts.length - 2]!.x > last.x);
});

test("la pastille n'est jamais sur une carte, même cartes serrées", () => {
  const ends = wireEnds(wires);
  for (const id of ["f", "v", "g"]) {
    const w = wires.find((x) => x.id === id)!;
    const { mid } = route(ends.get(id)!, cards, { skip: [w.s.id, w.t.id] });
    for (const c of cards)
      assert.ok(!inside(mid, c), `${id} : pastille dans ${c.id}`);
  }
  // La capture d'augus : Inspirations posée sous la carte Rendus, à sa droite.
  const moved: Rect = { id: "insp", x: 1840, y: 560, w: 500, h: 300 };
  const all = [photos, moved, plan, renders];
  const e = wireEnds([{ id: "g", kind: "guides_style", s: moved, t: renders }]);
  const { mid } = route(e.get("g")!, all, { skip: ["insp", "renders"] });
  for (const c of all) assert.ok(!inside(mid, c), `pastille dans ${c.id}`);
});

test("les cartes qui se chevauchent sont repérées, les voisines non", () => {
  assert.deepEqual([...overlapping(cards)], []);
  const moved: Rect = { id: "insp", x: 1840, y: 560, w: 500, h: 300 };
  assert.deepEqual([...overlapping([photos, moved, plan, renders])].sort(), [
    "insp",
    "renders",
  ]);
});
