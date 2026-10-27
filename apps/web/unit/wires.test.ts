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

test("un port d'entrée par sorte : le plan à gauche des Rendus, le Moodboard en haut (KAN-98)", () => {
  assert.equal(entrySide("renders_view"), "left");
  assert.equal(entrySide("feeds_plan"), "left");
  assert.equal(entrySide("guides_style"), "top");
  const ends = wireEnds(wires);
  assert.equal(ends.get("v")!.b.x, renders.x);
  assert.equal(ends.get("g")!.b.y, renders.y);
  assert.equal(ends.get("g")!.bSide, "top");
  assert.ok(
    ends.get("g")!.b.x > renders.x &&
      ends.get("g")!.b.x < renders.x + renders.w,
  );
  assert.equal(ends.get("f")!.b.x, plan.x);
});

test("deux Moodboards vers le même node Rendus arrivent en haut, à deux abscisses différentes", () => {
  const other: Rect = { id: "insp2", x: 2400, y: -500, w: 624, h: 374 };
  const ends = wireEnds([
    ...wires,
    { id: "g2", kind: "guides_style", s: other, t: renders },
  ]);
  const a = ends.get("g")!.b;
  const b = ends.get("g2")!.b;
  assert.equal(a.y, b.y);
  assert.notEqual(a.x, b.x);
  // Dans l'ordre des sources : la plus à gauche arrive plus à gauche (pas de croisement).
  assert.ok(a.x < b.x);
});

test("un Moodboard posé à gauche contourne le plan et descend sur le haut des Rendus", () => {
  const ends = wireEnds(wires).get("g")!;
  const r = route(ends, cards, { skip: ["insp", "renders"] });
  const pts = points(r.d);
  // Aucun coin du tracé dans une carte (hors le départ et l'arrivée, sur leurs bords).
  for (const p of pts.slice(1, -1))
    for (const c of cards)
      assert.ok(!inside(p, c), `${JSON.stringify(p)} dans ${c.id}`);
  // Il arrive d'au-dessus, à la verticale de son port.
  const last = pts[pts.length - 1]!;
  assert.deepEqual(last, ends.b);
  const before = pts[pts.length - 2]!;
  assert.ok(before.y < last.y && Math.abs(before.x - last.x) < 1e-6);
});

test("KAN-98 : un Moodboard au-dessus des Rendus part par son bas, courbe directe", () => {
  const mood: Rect = { id: "mood", x: 1668, y: -516, w: 780, h: 468 };
  const ends = wireEnds([
    { id: "m", kind: "guides_style", s: mood, t: renders },
  ]).get("m")!;
  assert.equal(ends.aSide, "bottom");
  assert.equal(ends.a.y, mood.y + mood.h);
  assert.equal(ends.b.y, renders.y);
  const r = route(ends, [mood, plan, renders], { skip: ["mood", "renders"] });
  assert.ok(r.d.includes(" C "), "courbe directe");
  for (const c of [mood, plan, renders])
    assert.ok(!inside(r.mid, c), `pastille dans ${c.id}`);
});

test("KAN-98 : vers les Rendus du dessous, le fil du Moodboard ne traverse pas ceux du dessus", () => {
  // Deux plans en rangées (écart de 48), le Moodboard ajouté posé 48 au-dessus des Rendus du
  // premier et relié aux deux : le second fil passe par un couloir, jamais sous une carte.
  const top: Rect = { id: "r1", x: 1668, y: 0, w: 624, h: 700 };
  const low: Rect = { id: "r2", x: 1668, y: 748, w: 624, h: 700 };
  const plan2: Rect = { id: "plan2", x: 720, y: 748, w: 900, h: 700 };
  const mood: Rect = { id: "mood", x: 1668, y: -516, w: 780, h: 468 };
  const all = [mood, plan, plan2, top, low];
  const ends = wireEnds([
    { id: "a", kind: "guides_style", s: mood, t: top },
    { id: "b", kind: "guides_style", s: mood, t: low },
    { id: "v1", kind: "renders_view", s: plan, t: top },
    { id: "v2", kind: "renders_view", s: plan2, t: low },
  ]);
  const e = ends.get("b")!;
  const pts = points(route(e, all, { skip: ["mood", "r2"] }).d);
  // Chaque segment, échantillonné : hors de toute carte (hors ses deux bouts, sur les bords).
  for (let i = 1; i < pts.length; i++)
    for (let k = 0; k <= 20; k++) {
      const p = {
        x: pts[i - 1]!.x + ((pts[i]!.x - pts[i - 1]!.x) * k) / 20,
        y: pts[i - 1]!.y + ((pts[i]!.y - pts[i - 1]!.y) * k) / 20,
      };
      for (const c of all)
        assert.ok(!inside(p, c), `${JSON.stringify(p)} dans ${c.id}`);
    }
  assert.deepEqual(pts[pts.length - 1], e.b);
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
