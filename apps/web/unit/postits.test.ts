// KAN-65 : post-its de couleur (palette, couleur par défaut, texte qui tient, redimension).
// Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  colorOf,
  fitFontSize,
  lastColor,
  POSTIT_COLORS,
  rememberColor,
  resizedPostit,
} from "../src/board/postits.ts";

test("huit couleurs, en français, une note d'avant est jaune", () => {
  assert.deepEqual(
    POSTIT_COLORS.map((c) => c.label),
    ["Jaune", "Orange", "Rose", "Violet", "Bleu", "Vert", "Gris", "Blanc"],
  );
  assert.equal(colorOf({}), "yellow");
  assert.equal(colorOf({ color: null }), "yellow");
  assert.equal(colorOf({ color: "blue" }), "blue");
});

test("la couleur par défaut est la dernière utilisée", () => {
  const store = new Map<string, string>();
  const g = globalThis as { localStorage?: unknown };
  g.localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  };
  try {
    assert.equal(lastColor(), "yellow");
    rememberColor("violet");
    assert.equal(lastColor(), "violet");
    store.set("pd.postit.color", "rouge"); // valeur inconnue : jaune
    assert.equal(lastColor(), "yellow");
  } finally {
    delete g.localStorage;
  }
  // Sans stockage (navigation privée) : jaune, sans erreur.
  assert.equal(lastColor(), "yellow");
  rememberColor("blue");
});

test("le texte se réduit pour tenir", () => {
  // Un texte qui tient jusqu'à 17 px.
  const size = fitFontSize((s) => s <= 17);
  assert.ok(size <= 17 && size >= 16.5, String(size));
  assert.equal(
    fitFontSize(() => true),
    40,
  );
  assert.equal(
    fitFontSize(() => false),
    8,
  );
});

test("redimensionner garde un carré, un rectangle avec Maj", () => {
  assert.deepEqual(resizedPostit(240, 180, false), { w: 240, h: 240 });
  assert.deepEqual(resizedPostit(150, 300, false), { w: 300, h: 300 });
  assert.deepEqual(resizedPostit(240, 180, true), { w: 240, h: 180 });
});
