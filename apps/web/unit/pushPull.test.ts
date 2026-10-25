// KAN-92 : Pousser/Tirer (face survolée, valeur tirée, limites, saisie au clavier, commande).
// Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bound,
  dragged,
  faceKind,
  parseTyped,
  pushPullOp,
  pushText,
} from "../src/viewer3d/pushPull.ts";

test("la face dit ce qu'on tire", () => {
  assert.equal(faceKind("wall", 0), "thickness");
  assert.equal(faceKind("wall", 1), "height");
  assert.equal(faceKind("wall", 0.5), null, "une arête biaise ne tire rien");
  assert.equal(faceKind("floor_zone", 1), "elevation");
  assert.equal(faceKind("floor_zone", 0), null);
  assert.equal(
    faceKind("furniture", 1),
    null,
    "pousser un meuble : hors périmètre",
  );
});

test("saisie au clavier : 12 cm, 0,40 m, 40, 0", () => {
  assert.equal(parseTyped("12 cm"), 0.12);
  assert.equal(parseTyped("0,40 m"), 0.4);
  assert.equal(parseTyped("40"), 0.4, "entier de 10 ou plus : cm");
  assert.equal(parseTyped("2"), 2, "entier de 1 à 9 : m");
  assert.equal(parseTyped("0"), 0);
  assert.equal(parseTyped("125 mm"), 0.125);
  assert.equal(parseTyped("douze"), null);
});

test("les limites du serveur, dites", () => {
  assert.deepEqual(bound("thickness", 0.72, 2.5), {
    value: 0.6,
    limit: "60 cm au plus",
  });
  assert.deepEqual(bound("thickness", 0.02, 2.5), {
    value: 0.05,
    limit: "5 cm au moins",
  });
  assert.deepEqual(bound("height", 2.8, 2.5), {
    value: 2.5,
    limit: "jusqu'au plafond (2,50 m)",
  });
  assert.deepEqual(bound("elevation", 1.5, 2.5), {
    value: 1.2,
    limit: "1,20 m au plus",
  });
  assert.deepEqual(bound("elevation", -0.1, 2.5), {
    value: 0,
    limit: "au sol",
  });
  assert.deepEqual(bound("elevation", 0.2, 2.5), { value: 0.2, limit: null });
});

test("glisser : un mur centré gagne deux fois l'avancée de sa face", () => {
  assert.equal(dragged("thickness", 0.15, 0.05, 2.5).value, 0.25);
  assert.equal(dragged("thickness", 0.15, 0.05, 2.5, "left").value, 0.2);
  assert.equal(dragged("elevation", 0, 0.2, 2.5).value, 0.2);
  assert.equal(dragged("height", 2.5, -1.4, 2.5).value, 1.1);
});

test("la cote près du curseur et la commande envoyée", () => {
  assert.equal(pushText("thickness", 0.22), "épaisseur du mur : 22 cm");
  assert.equal(pushText("elevation", 0.2), "hauteur de la zone : +20 cm");
  assert.equal(pushText("height", 1.1), "hauteur du mur : 1,10 m");
  assert.deepEqual(pushPullOp("w", "thickness", 0.224, 2.5), {
    op: "update_element",
    element_id: "w",
    fields: { thickness: 0.22 },
  });
  assert.deepEqual(pushPullOp("w", "height", 2.5, 2.5).fields, {
    height: null,
  });
  assert.deepEqual(pushPullOp("z", "elevation", 0.2, 2.5).fields, {
    elevation: 0.2,
  });
});
