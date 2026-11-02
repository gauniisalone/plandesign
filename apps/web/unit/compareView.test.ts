// KAN-99 : zoom et déplacement communs de la vue comparée. Lancé par `npm run test:unit`
// (node --test, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  FIT,
  MAX_SCALE,
  clampView,
  panBy,
  transformOf,
  zoomAt,
  zoomText,
} from "../src/board/compareView.ts";

const W = 400;
const H = 300;

test("le zoom garde le point visé sous le pointeur", () => {
  const v = zoomAt(FIT, 2, 100, 75, W, H);
  assert.equal(v.scale, 2);
  // Le point (100, 75) du cadre montre toujours le même point de l'image.
  assert.equal((100 - v.x) / v.scale, 100);
  assert.equal((75 - v.y) / v.scale, 75);
  assert.equal(zoomText(v), "200 %");
});

test("bornes : jamais en dessous de 100 %, jamais au-delà du maximum, aucun bord vide", () => {
  assert.deepEqual(zoomAt(FIT, 0.5, 200, 150, W, H), FIT);
  assert.equal(zoomAt({ scale: 5, x: 0, y: 0 }, 4, 0, 0, W, H).scale, MAX_SCALE);
  const v = zoomAt(FIT, 2, 0, 0, W, H);
  assert.deepEqual(panBy(v, 50, 50, W, H), { scale: 2, x: 0, y: 0 });
  assert.deepEqual(panBy(v, -1000, -1000, W, H), { scale: 2, x: -400, y: -300 });
  // À 100 %, aucun déplacement possible.
  assert.deepEqual(panBy(FIT, 30, -30, W, H), FIT);
  assert.deepEqual(clampView({ scale: 1, x: -5, y: 9 }, W, H), FIT);
});

test("une seule transformation CSS pour toutes les images comparées", () => {
  assert.equal(
    transformOf({ scale: 2, x: -10, y: -20 }),
    "translate(-10px, -20px) scale(2)",
  );
});
