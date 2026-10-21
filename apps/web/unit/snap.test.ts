// KAN-29 : tracé libre avec Maj (murs obliques). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { smartSnap, type SnapOptions } from "../src/editor2d/snap.ts";

const start = { x: 0, y: 0 };
const base: SnapOptions = {
  start,
  segments: [{ id: "w", a: { x: 0, y: -3 }, b: { x: 0, y: 0 } }],
  vertices: [
    { id: "v0", p: { x: 0, y: -3 } },
    { id: "v1", p: { x: 0, y: 0 } },
  ],
  tolerance: 0.1,
};
const deg = (p: { x: number; y: number }) =>
  (Math.atan2(p.y - start.y, p.x - start.x) * 180) / Math.PI;

test("sans Maj, rien ne change : près de l'horizontale, le mur s'accroche à 0°", () => {
  // Tout près de l'horizontale : accroché à 0° comme avant.
  const near = smartSnap({ x: 3, y: 0.05 }, base);
  assert.equal(near.kind, "angle");
  assert.equal(near.label, "0°");
  assert.ok(Math.abs(near.point.y) < 1e-9);
});

test("Maj : le mur suit le curseur à 37°, l'angle est affiché", () => {
  const raw = { x: 3 * Math.cos(0.6458), y: 3 * Math.sin(0.6458) }; // ≈ 37°
  const s = smartSnap(raw, { ...base, free: true });
  assert.equal(s.kind, "free");
  assert.equal(s.label, "37°");
  assert.ok(Math.abs(deg(s.point) - 37) < 0.5, `angle ${deg(s.point)}`);
  // Sans Maj, le même curseur, loin de tout cran (à plus de 10 cm du rayon), reste libre lui
  // aussi mais sans étiquette : c'est l'ancien comportement.
  const plain = smartSnap(raw, base);
  assert.equal(plain.label, undefined);
});

test("Maj : accroche douce à un cran de 15°, à 2° près seulement", () => {
  const at = (d: number) => ({
    x: 4 * Math.cos((d * Math.PI) / 180),
    y: 4 * Math.sin((d * Math.PI) / 180),
  });
  const soft = smartSnap(at(46.5), { ...base, free: true });
  assert.equal(soft.kind, "angle");
  assert.equal(soft.label, "45°");
  assert.ok(Math.abs(deg(soft.point) - 45) < 1e-6);
  assert.equal(soft.guides.length, 1);
  const loose = smartSnap(at(42), { ...base, free: true });
  assert.equal(loose.kind, "free");
  assert.equal(loose.label, "42°");
  // Plus de perpendiculaire ni de parallèle au mur existant avec Maj.
  const ortho = smartSnap(at(1.5), { ...base, free: true });
  assert.equal(ortho.label, "0°", "cran doux, pas « Perpendiculaire »");
});

test("Maj : un sommet existant accroche encore (refermer la pièce), avec l'angle", () => {
  const s = smartSnap({ x: 0.03, y: -2.97 }, { ...base, free: true });
  assert.equal(s.vertexId, "v0");
  assert.equal(s.kind, "endpoint");
  assert.equal(s.label, "Extrémité · 270°");
});
