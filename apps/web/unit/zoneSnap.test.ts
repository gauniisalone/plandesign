// KAN-92 : un coin de zone de sol s'accroche aux angles puis aux faces INTÉRIEURES de la
// pièce (jamais à l'axe des murs). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { movedCorner, snapZoneCorner } from "../src/editor2d/zoneSnap.ts";

// Pièce de 4 × 3 m d'axe, murs de 15 cm : intérieur de 0,075 à 3,925 sur 0,075 à 2,925.
const inner = [
  { x: 0.075, y: 0.075 },
  { x: 3.925, y: 0.075 },
  { x: 3.925, y: 2.925 },
  { x: 0.075, y: 2.925 },
];

test("un angle intérieur proche attire le coin", () => {
  const s = snapZoneCorner({ x: 0.12, y: 0.1 }, [inner], 0.1);
  assert.deepEqual(s, { point: inner[0], label: "angle" });
});

test("sinon la face intérieure la plus proche", () => {
  const s = snapZoneCorner({ x: 1.5, y: 0.12 }, [inner], 0.1);
  assert.equal(s.label, "mur");
  assert.ok(
    Math.abs(s.point.y - 0.075) < 1e-9 && Math.abs(s.point.x - 1.5) < 1e-9,
  );
});

test("loin de tout : arrondi au centimètre ; Alt : libre", () => {
  assert.deepEqual(snapZoneCorner({ x: 1.234, y: 1.567 }, [inner], 0.1), {
    point: { x: 1.23, y: 1.57 },
    label: null,
  });
  assert.equal(
    snapZoneCorner({ x: 0.12, y: 0.1 }, [inner], 0.1, true).label,
    null,
  );
});

test("déplacer un coin ne touche pas les trois autres", () => {
  const zone = [inner[0]!, inner[1]!, inner[2]!, inner[3]!];
  const moved = movedCorner(zone, 2, { x: 3, y: 2 });
  assert.deepEqual(moved, [inner[0], inner[1], { x: 3, y: 2 }, inner[3]]);
});
