// KAN-141 : le panneau « Modifier » d'une vue de pièce (réglages et opérations, logique pure de
// board/roomViews.ts). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Camera, Level } from "../src/api/types.gen.ts";
import {
  eyeOp,
  eyeValue,
  focalMm,
  focalOp,
  focalPx,
  isEdited,
  roomRows,
} from "../src/board/roomViews.ts";

function camera(source: string, z = 1.55, focal = 924.4): Camera {
  return {
    id: "c1",
    type: "camera",
    name: "Chambre",
    image_id: null,
    source,
    focal_px: focal,
    position: { x: 1, y: 2, z },
    metadata: {
      room_view: "r1",
      area_m2: 12,
      note: "depuis l'entrée · 1,55 m · 26 mm · 76 % du sol visible",
    },
  } as unknown as Camera;
}

test("la focale se lit et s'écrit en équivalent 24 × 36 sur 1280 px", () => {
  assert.equal(focalMm(focalPx(26)), 26);
  assert.equal(focalMm(924.4), 26);
  assert.equal(focalPx(36), 1280);
});

test("la hauteur d'œil reste entre 1,20 et 1,70 m, au pas de 5 cm", () => {
  assert.equal(eyeValue(1.0), 1.2);
  assert.equal(eyeValue(1.9), 1.7);
  assert.ok(Math.abs(eyeValue(1.532) - 1.55) < 1e-9);
});

test("les réglages partent en update_element, focale bornée de 24 à 35 mm", () => {
  const c = camera("generated");
  const eye = eyeOp(c, 1.3);
  assert.equal(eye.op, "update_element");
  assert.deepEqual(eye.fields.position, { x: 1, y: 2, z: 1.3 });
  assert.equal(focalOp(c, 50).fields.focal_px, focalPx(35));
  assert.equal(focalOp(c, 18).fields.focal_px, focalPx(24));
});

test("une vue réglée à la main dit ses réglages, plus la note du calcul", () => {
  const plain = { elements: { c1: camera("generated") } } as unknown as Level;
  assert.equal(roomRows(plain)[0]!.note.startsWith("depuis l'entrée"), true);
  const edited = {
    elements: { c1: camera("user_defined", 1.3, focalPx(35)) },
  } as unknown as Level;
  assert.equal(isEdited(edited.elements.c1 as Camera), true);
  assert.equal(roomRows(edited)[0]!.note, "modifiée à la main · 1,30 m · 35 mm");
});
