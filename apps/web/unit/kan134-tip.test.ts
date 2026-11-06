// KAN-134 : l'infobulle de l'app (logique pure de ui/tipLogic.ts). Lancé par
// `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LONG_PRESS_MS,
  placeTip,
  sameAsName,
  stillPressing,
  TIP_DELAY_MS,
  tipText,
} from "../src/ui/tipLogic.ts";

const view = { width: 1280, height: 800 };
const tip = { width: 120, height: 24 };

test("survol après 0,4 s ; appui long d'une demi-seconde", () => {
  assert.equal(TIP_DELAY_MS, 400);
  assert.equal(LONG_PRESS_MS, 500);
});

test("sous l'icône, centrée", () => {
  const p = placeTip({ left: 100, top: 100, width: 32, height: 32 }, tip, view);
  assert.deepEqual(p, { left: 56, top: 138, side: "below" });
});

test("au-dessus quand il n'y a pas la place dessous", () => {
  const p = placeTip({ left: 100, top: 760, width: 32, height: 32 }, tip, view);
  assert.equal(p.side, "above");
  assert.equal(p.top, 760 - 6 - 24);
});

test("jamais hors de l'écran, à 8 px du bord", () => {
  assert.equal(placeTip({ left: 0, top: 10, width: 32, height: 32 }, tip, view).left, 8);
  const right = placeTip({ left: 1260, top: 10, width: 20, height: 20 }, tip, view);
  assert.equal(right.left, 1280 - 8 - 120);
  const phone = placeTip(
    { left: 10, top: 10, width: 32, height: 32 },
    { width: 400, height: 24 },
    { width: 360, height: 640 },
  );
  assert.equal(phone.left, 8);
});

test("texte vide : pas d'infobulle ; espaces ramenés", () => {
  assert.equal(tipText(""), null);
  assert.equal(tipText("   "), null);
  assert.equal(tipText(null), null);
  assert.equal(tipText("  Sélection\n (V) "), "Sélection (V)");
});

test("pas de description quand elle répète le nom lu", () => {
  assert.equal(sameAsName("Zoomer", "zoomer"), true);
  assert.equal(sameAsName("Sélection (V)", "Sélection"), false);
  assert.equal(sameAsName("Zoomer", null), false);
});

test("un doigt qui glisse ne fait pas un appui long", () => {
  assert.equal(stillPressing({ x: 0, y: 0 }, { x: 6, y: 6 }), true);
  assert.equal(stillPressing({ x: 0, y: 0 }, { x: 12, y: 0 }), false);
});
