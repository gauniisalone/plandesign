// KAN-71 : virgule décimale des textes visibles. Mêmes cas que le pytest de
// `openplan/schemas/numbers.py`. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  area,
  centimetres,
  decimal,
  length,
  metres,
  percent,
  typed,
} from "../src/format.ts";

test("decimal : virgule, arrondi, signe, -0 écrit 0", () => {
  assert.equal(decimal(1.2), "1,20");
  assert.equal(decimal(1.234, 1), "1,2");
  assert.equal(decimal(-0.001, 2), "0,00");
  assert.equal(decimal(-0.3, 2, true), "-0,30");
  assert.equal(decimal(0.3, 2, true), "+0,30");
  assert.equal(decimal(NaN), "—");
});

test("unités : m, cm, m², %", () => {
  assert.equal(metres(1.2), "1,20 m");
  assert.equal(centimetres(0.5), "50 cm");
  assert.equal(centimetres(0.025, 1), "2,5 cm");
  assert.equal(area(12.54), "12,5 m²");
  assert.equal(percent(0.125, 1), "12,5 %");
  assert.equal(percent(0.5), "50 %");
});

test("length : l'unité d'affichage du projet", () => {
  assert.equal(length(1.2, "cm"), "120 cm");
  assert.equal(length(1.2, "mm"), "1200 mm");
  assert.equal(length(1.2, "m"), "1,20 m");
});

test("typed : une saisie réaffichée sans arrondi", () => {
  assert.equal(typed("35.5"), "35,5");
});
