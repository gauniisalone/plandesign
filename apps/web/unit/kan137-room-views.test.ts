// KAN-137 : les vues par pièce du node Rendus (logique pure de board/roomViews.ts). Lancé par
// `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Level } from "../src/api/types.gen.ts";
import {
  areaText,
  hasRooms,
  isRoomView,
  rendersOfRoom,
  roomFilters,
  roomRows,
  tickOp,
} from "../src/board/roomViews.ts";

function camera(id: string, name: string, meta: Record<string, unknown> | null) {
  return { id, type: "camera", name, image_id: null, metadata: meta };
}

function level(elements: Record<string, unknown>): Level {
  return { elements } as unknown as Level;
}

const PLAN = level({
  r1: { id: "r1", type: "room", name: "Séjour" },
  c1: camera("c1", "Chambre", {
    room_view: "r2",
    area_m2: 12,
    room_view_on: true,
    note: "depuis l'entrée · 1,55 m · 26 mm · 76 % du sol visible",
  }),
  c2: camera("c2", "Séjour et cuisine", { room_view: "r1", area_m2: 31.4, room_view_on: true }),
  c3: camera("c3", "WC", { room_view: "r3", area_m2: 1.5, room_view_on: false }),
  c4: camera("c4", "Vue d'ensemble", { auto_view: "overall" }),
  c5: camera("c5", "Point de vue 1", null),
});

test("seules les vues de pièce sont listées, la plus grande pièce d'abord", () => {
  const rows = roomRows(PLAN);
  assert.deepEqual(
    rows.map((r) => r.name),
    ["Séjour et cuisine", "Chambre", "WC"],
  );
  assert.deepEqual(
    rows.map((r) => r.on),
    [true, true, false],
  );
  assert.equal(rows[1]!.note.startsWith("depuis l'entrée"), true);
  assert.equal(isRoomView(PLAN.elements.c4 as never), false);
  assert.equal(isRoomView(PLAN.elements.c5 as never), false);
});

test("la case n'est cochée que si le serveur ne dit pas le contraire", () => {
  const plan = level({ c: camera("c", "Bureau", { room_view: "r" }) });
  assert.equal(roomRows(plan)[0]!.on, true);
});

test("« Proposer une vue par pièce » n'a de sens qu'avec des pièces", () => {
  assert.equal(hasRooms(PLAN), true);
  assert.equal(hasRooms(level({ c5: camera("c5", "Vue", null) })), false);
  assert.equal(hasRooms(undefined), false);
});

test("la surface s'écrit comme sur le plan", () => {
  assert.equal(areaText(31.4), "31 m²");
  assert.equal(areaText(4.5), "4,5 m²");
  assert.equal(areaText(null), "");
});

test("la case d'une pièce part en update_element, les autres métadonnées gardées", () => {
  const row = roomRows(PLAN).find((r) => r.name === "WC")!;
  const op = tickOp(row, true);
  assert.equal(op.op, "update_element");
  assert.equal(op.element_id, "c3");
  assert.deepEqual(op.fields.metadata, {
    room_view: "r3",
    area_m2: 1.5,
    room_view_on: true,
  });
});

test("le filtre par pièce paraît dès deux pièces rendues, dans l'ordre des pièces", () => {
  const one = [{ id: "a", camera_id: "c1" }];
  assert.deepEqual(roomFilters(PLAN, one), []);
  const many = [
    { id: "a", camera_id: "c1" },
    { id: "b", camera_id: "c2" },
    { id: "c", camera_id: "c4" },
    { id: "d", camera_id: "c2", photo_id: "p" },
    { id: "e", camera_id: "c1" },
  ];
  assert.deepEqual(roomFilters(PLAN, many), ["Séjour et cuisine", "Chambre"]);
  assert.deepEqual([...rendersOfRoom(PLAN, many, "Chambre")].sort(), ["a", "e"]);
  assert.deepEqual([...rendersOfRoom(PLAN, many, "Séjour et cuisine")], ["b"]);
});
