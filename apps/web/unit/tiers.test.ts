// KAN-98 L2 : les paliers de dézoom, lus sur la largeur à l'écran du node le plus étroit.
// Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { MAQUETTE_NODE_PX, TIER_NAMES, zoomTier } from "../src/board/tiers.ts";

test("les seuils de la maquette (60 % et 25 %) pour un node de 360 px", () => {
  assert.equal(MAQUETTE_NODE_PX, 360);
  assert.equal(zoomTier(1), "detail");
  assert.equal(zoomTier(0.6), "detail");
  assert.equal(zoomTier(0.59), "thumbs");
  assert.equal(zoomTier(0.25), "thumbs");
  assert.equal(zoomTier(0.24), "titles");
});

test("un node neuf de 780 ouvert à 0,46 (360 px à l'écran) est au détail", () => {
  assert.equal(zoomTier(0.4615, 780), "detail");
  // Un node d'avant L2 (624) est plus étroit : à 0,3, il fait 187 px à l'écran (vignettes),
  // quand un node neuf au même zoom (234 px) est déjà au même palier.
  assert.equal(zoomTier(0.3, 624), "thumbs");
  assert.equal(zoomTier(0.35, 624), "detail");
  assert.equal(zoomTier(0.1, 780), "titles");
});

test("chaque palier a son nom affiché", () => {
  assert.deepEqual(TIER_NAMES, {
    detail: "détail",
    thumbs: "vignettes",
    titles: "titres",
  });
});
