// KAN-66 : saisie de la longueur connue d'une mesure de référence. Lancé par `npm run test:unit`
// (node --test, TypeScript lu directement par Node 24, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import { formatMeters, parseLength } from "../src/state/lengthInput.ts";

function meters(text: string): number {
  const out = parseLength(text);
  assert.ok(out.ok, `« ${text} » refusé : ${out.ok ? "" : out.error}`);
  return out.meters;
}
function refused(text: string): string {
  const out = parseLength(text);
  assert.ok(!out.ok, `« ${text} » accepté`);
  return out.error;
}

test("mètres avec virgule ou point", () => {
  assert.equal(meters("2,04 m"), 2.04);
  assert.equal(meters("2.04 m"), 2.04);
  assert.equal(meters("2,04m"), 2.04);
});

test("centimètres et millimètres", () => {
  assert.equal(meters("204 cm"), 2.04);
  assert.equal(meters("204cm"), 2.04);
  assert.equal(meters("85,5 cm"), 0.855);
  assert.equal(meters("2040 mm"), 2.04);
});

test("un nombre entier seul est lu en cm", () => {
  assert.equal(meters("204"), 2.04);
  assert.equal(meters("  90  "), 0.9);
});

test("un nombre décimal seul est lu en mètres", () => {
  assert.equal(meters("2,04"), 2.04);
  assert.equal(meters("0,8"), 0.8);
});

test("unités écrites en toutes lettres, majuscules et espaces", () => {
  assert.equal(meters("2 mètres"), 2);
  assert.equal(meters("204 Centimètres"), 2.04);
  assert.equal(meters("2,04   M"), 2.04);
});

test("« 2 m 04 » : mètres et centimètres", () => {
  assert.equal(meters("2 m 04"), 2.04);
  assert.equal(meters("2m04"), 2.04);
  assert.equal(meters("2m5"), 2.5);
});

test("vide : on dit quoi écrire", () => {
  assert.match(refused(""), /Saisissez une longueur, par exemple 2,04 m/);
  assert.match(refused("   "), /Saisissez une longueur/);
});

test("pas un nombre", () => {
  assert.match(refused("porte"), /« porte » n'est pas une longueur/);
  assert.match(refused("2,0,4 m"), /n'est pas une longueur/);
});

test("unité inconnue", () => {
  assert.match(
    refused("2 pieds"),
    /Unité « pieds » inconnue : écrivez m, cm ou mm/,
  );
});

test("négatif ou nul", () => {
  assert.match(refused("-2 m"), /positive/);
  assert.match(refused("0"), /plus grande que zéro/);
  assert.match(refused("0 m"), /plus grande que zéro/);
});

test("trop court : moins de 5 cm, avec l'indice des mètres pour un entier seul", () => {
  const error = refused("2");
  assert.match(error, /Trop court \(2 cm\) : une mesure fait au moins 5 cm/);
  assert.match(error, /Pour des mètres, écrivez « 2 m »/);
  assert.doesNotMatch(refused("3 mm"), /Pour des mètres/);
  assert.match(refused("3 mm"), /Trop court \(3 mm\)/);
});

test("trop long : plus de 30 m", () => {
  assert.match(
    refused("31 m"),
    /Trop long \(31,00 m\) : une mesure fait au plus 30 m/,
  );
  assert.match(refused("4000"), /Trop long \(40,00 m\)/);
});

test("bornes acceptées : 5 cm et 30 m", () => {
  assert.equal(meters("5 cm"), 0.05);
  assert.equal(meters("30 m"), 30);
});

test("affichage en mètres avec virgule", () => {
  assert.equal(formatMeters(2.04), "2,04 m");
  assert.equal(formatMeters(0.856), "0,86 m");
  assert.equal(formatMeters(30), "30,00 m");
});
