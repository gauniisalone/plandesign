// KAN-104 : Réglages › Compte, une session ouverte en mots (navigateur, voie, dates). Lancé
// par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  browserLabel,
  methodLabel,
  seenLabel,
} from "../src/state/sessionLabel.ts";

const FIREFOX_WINDOWS =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0";
const SAFARI_IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1";
const CHROME_MAC =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36";
const EDGE_WINDOWS =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Safari/537.36 Edg/131.0.2903.70";
const CHROME_ANDROID =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.85 Mobile Safari/537.36";

test("le navigateur et le système, dans le bon ordre de reconnaissance", () => {
  assert.equal(browserLabel(FIREFOX_WINDOWS), "Firefox · Windows");
  assert.equal(browserLabel(SAFARI_IPHONE), "Safari · iPhone");
  assert.equal(browserLabel(CHROME_MAC), "Chrome · macOS");
  // Edge et Android se déclarent aussi « Chrome » et « Linux » : le plus précis l'emporte.
  assert.equal(browserLabel(EDGE_WINDOWS), "Edge · Windows");
  assert.equal(browserLabel(CHROME_ANDROID), "Chrome · Android");
});

test("rien de lisible : on ne devine pas", () => {
  assert.equal(browserLabel(""), "Navigateur inconnu");
  assert.equal(browserLabel("python-httpx/0.28"), "Navigateur inconnu");
  assert.equal(browserLabel("curl/8 (Linux)"), "Linux");
});

test("la voie de connexion", () => {
  assert.equal(methodLabel("password"), "par mot de passe");
  assert.equal(methodLabel("code"), "par code");
});

test("vue : de l'instant aux jours", () => {
  const now = new Date(2026, 10, 5, 18, 30);
  const at = (d: Date) => d.toISOString();
  assert.equal(seenLabel(at(new Date(2026, 10, 5, 18, 29)), now), "à l'instant");
  assert.equal(seenLabel(at(new Date(2026, 10, 5, 18, 18)), now), "il y a 12 min");
  assert.equal(seenLabel(at(new Date(2026, 10, 5, 9, 0)), now), "aujourd'hui");
  assert.equal(seenLabel(at(new Date(2026, 10, 4, 22, 0)), now), "hier");
  assert.equal(seenLabel(at(new Date(2026, 9, 30, 12, 0)), now), "il y a 6 jours");
});
