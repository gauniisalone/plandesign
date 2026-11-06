// KAN-134 : « Réglages des rendus » et compteur du quota (logique pure de
// board/renderSettings.ts). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  cleanSettings,
  DEFAULT_SETTINGS,
  fits,
  focalChoices,
  quotaText,
  readSettings,
  sentText,
  takes,
  todayText,
  writeSettings,
} from "../src/board/renderSettings.ts";

function memory() {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
  };
}

test("par défaut : un tirage par pièce, focale automatique", () => {
  assert.deepEqual(DEFAULT_SETTINGS, { draws: 1, focal: null });
  assert.deepEqual(cleanSettings(undefined), DEFAULT_SETTINGS);
});

test("tirages bornés de 1 à 3, focale de 24 à 35 mm, sinon automatique", () => {
  assert.equal(cleanSettings({ draws: 0 }).draws, 1);
  assert.equal(cleanSettings({ draws: 7 }).draws, 3);
  assert.equal(cleanSettings({ draws: 2.4 }).draws, 2);
  assert.equal(cleanSettings({ focal: 23 }).focal, null);
  assert.equal(cleanSettings({ focal: 36 }).focal, null);
  assert.equal(cleanSettings({ focal: "28" }).focal, null);
  assert.equal(cleanSettings({ focal: 30 }).focal, 30);
});

test("gardés par projet dans le navigateur ; stockage absent ou cassé sans erreur", () => {
  const store = memory();
  writeSettings("p1", { draws: 3, focal: 28 }, store);
  assert.deepEqual(readSettings("p1", store), { draws: 3, focal: 28 });
  assert.deepEqual(readSettings("p2", store), DEFAULT_SETTINGS);
  store.setItem("render-settings:p3", "{pas du json");
  assert.deepEqual(readSettings("p3", store), DEFAULT_SETTINGS);
  assert.deepEqual(readSettings("p1", null), DEFAULT_SETTINGS);
  const broken = {
    getItem: () => {
      throw new Error("refusé");
    },
    setItem: () => {
      throw new Error("refusé");
    },
  };
  assert.deepEqual(readSettings("p1", broken), DEFAULT_SETTINGS);
  assert.doesNotThrow(() => writeSettings("p1", DEFAULT_SETTINGS, broken));
});

test("le compteur dit avant le clic ce qui sera pris", () => {
  assert.equal(takes(3, 1), 3);
  assert.equal(takes(2, 3), 6);
  assert.equal(takes(0, 3), 0);
  assert.equal(quotaText(3, 5, 20), "3 rendus seront pris · 5 sur 20 aujourd'hui");
  assert.equal(quotaText(1, 0, 20), "1 rendu sera pris · 0 sur 20 aujourd'hui");
  assert.equal(
    quotaText(6, 17, 20),
    "6 rendus seront pris · 17 sur 20 aujourd'hui : il en reste 3",
  );
  assert.equal(quotaText(2, 0, 0), "2 rendus seront pris");
  assert.equal(todayText(4, 20), "4 sur 20 aujourd'hui");
  assert.equal(todayText(0, 0), "");
});

test("au-delà du quota du jour, « Rendre » ne part pas", () => {
  assert.equal(fits(3, 17, 20), true);
  assert.equal(fits(4, 17, 20), false);
  assert.equal(fits(9, 0, 0), true, "quota inconnu : le serveur tranche");
});

test("le retour sous le bouton dit ce qui est vraiment parti", () => {
  assert.equal(
    sentText({ sent: 2, asked: 2, full: false }),
    "2 rendus demandés : ils arrivent dans le node Rendus.",
  );
  assert.equal(
    sentText({ sent: 1, asked: 1, full: false }),
    "1 rendu demandé : il arrive dans le node Rendus.",
  );
  assert.equal(
    sentText({ sent: 1, asked: 2, full: true }),
    "1 rendu demandé sur 2 : quota du jour atteint.",
  );
  assert.equal(
    sentText({ sent: 2, asked: 3, full: true }),
    "2 rendus demandés sur 3 : quota du jour atteint.",
  );
});

test("le choix de focale : Automatique puis 24 à 35 mm", () => {
  const c = focalChoices();
  assert.equal(c[0]!.value, null);
  assert.equal(c[1]!.value, 24);
  assert.equal(c.at(-1)!.value, 35);
  assert.equal(c.length, 13);
});
