// KAN-100 L1 : « Ranger » : pose et tailles de KAN-98 L2, déterministe, idempotent, membres
// gardés dans leur node. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { BoardItem, BoardLink } from "../src/api/client.ts";
import {
  arrange,
  COL_GAP,
  NODE_W,
  ROW_GAP,
  type Box,
} from "../src/board/arrange.ts";

function item(
  id: string,
  kind: string,
  x: number,
  y: number,
  w: number,
  h: number,
  extra: Partial<BoardItem> = {},
): BoardItem {
  return {
    id,
    kind,
    x,
    y,
    w,
    h,
    z: 0,
    locked: false,
    frame: null,
    ref: null,
    text: id,
    role: null,
    ...extra,
  } as BoardItem;
}
const link = (
  id: string,
  kind: BoardLink["kind"],
  source: string,
  target: string,
): BoardLink => ({ id, kind, source, target });

/** Un espace posé à l'ancienne (KAN-91, avant L2) : deux plans en rangées, nodes de 624,
 *  Photos et Moodboard dans la colonne de gauche, deux photos dans le node Photos, une note
 *  libre posée là où ira la pose, une autre loin, un Moodboard ajouté à la main sans lien. */
function legacy(): { items: BoardItem[]; links: BoardLink[] } {
  const items = [
    item("photos", "frame", 0, 0, 624, 374, { role: "photos" }),
    item("p1", "photo", 24, 160, 280, 190, { frame: "photos", ref: "i1" }),
    item("p2", "photo", 320, 160, 280, 190, { frame: "photos", ref: "i2" }),
    item("mood", "frame", 0, 422, 624, 374, { role: "inspirations" }),
    item("plan1", "plan", 720, 0, 900, 700, { ref: "L1" }),
    item("rendus1", "frame", 1668, 0, 624, 700, { role: "renders" }),
    item("photos2", "frame", 0, 748, 624, 374, { role: "photos", ref: "L2" }),
    item("plan2", "plan", 720, 748, 900, 700, { ref: "L2" }),
    item("rendus2", "frame", 1668, 748, 624, 700, {
      role: "renders",
      ref: "L2",
    }),
    item("seul", "frame", 3000, 0, 624, 374, {
      role: "inspirations",
      ref: "seul",
    }),
    item("note-dans", "note", 2400, 900, 300, 170),
    item("note-loin", "note", -900, -900, 300, 170),
  ];
  const links = [
    link("a", "feeds_plan", "photos", "plan1"),
    link("b", "renders_view", "plan1", "rendus1"),
    link("c", "guides_style", "mood", "rendus1"),
    link("d", "guides_style", "mood", "rendus2"),
    link("e", "feeds_plan", "photos2", "plan2"),
    link("f", "renders_view", "plan2", "rendus2"),
  ];
  return { items, links };
}

const apply = (items: BoardItem[], moves: Map<string, Box>) =>
  items.map((i) => ({ ...i, ...(moves.get(i.id) ?? {}) }));
const at = (items: BoardItem[], id: string) => items.find((i) => i.id === id)!;
const apart = (a: Box, b: Box) =>
  a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;

test("la pose de KAN-98 L2 : 780 de large, Moodboard au-dessus de Rendus, une rangée par plan", () => {
  const { items, links } = legacy();
  const after = apply(items, arrange(items, links));
  const [photos, plan1, rendus1, mood] = [
    "photos",
    "plan1",
    "rendus1",
    "mood",
  ].map((id) => at(after, id));
  for (const n of [photos!, rendus1!, mood!]) assert.equal(n.w, NODE_W);
  // Photos → Plan → Rendus, 240 entre les colonnes.
  assert.equal(plan1!.x - (photos!.x + photos!.w), COL_GAP);
  assert.equal(rendus1!.x - (plan1!.x + plan1!.w), COL_GAP);
  assert.equal(photos!.y, plan1!.y);
  assert.equal(rendus1!.y, plan1!.y);
  // Le Moodboard au-dessus de Rendus, dans sa colonne, à 160.
  assert.equal(mood!.x, rendus1!.x);
  assert.equal(rendus1!.y - (mood!.y + mood!.h), ROW_GAP);
  // La deuxième rangée dessous, 160 plus bas que la plus haute de la première.
  const plan2 = at(after, "plan2");
  assert.equal(plan2.y, plan1!.y + 700 + ROW_GAP);
  assert.equal(at(after, "photos2").y, plan2.y);
  assert.equal(at(after, "rendus2").y, plan2.y);
  // Ancrée au coin haut gauche d'avant.
  assert.equal(
    Math.min(
      ...after.filter((i) => !i.frame && i.kind !== "note").map((i) => i.x),
    ),
    0,
  );
  // Aucun node ne chevauche un autre, notes comprises.
  const tops = after.filter((i) => !i.frame);
  for (const a of tops)
    for (const b of tops)
      if (a.id !== b.id) assert.ok(apart(a, b), `${a.id} chevauche ${b.id}`);
});

test("les membres restent dans leur node, rangés dans ses cases (règle C3)", () => {
  const { items, links } = legacy();
  const after = apply(items, arrange(items, links));
  const photos = at(after, "photos");
  for (const id of ["p1", "p2"]) {
    const p = at(after, id);
    assert.equal(p.frame, "photos", "toujours membre");
    assert.ok(
      p.x >= photos.x && p.y >= photos.y,
      `${id} : coin haut gauche dans le node`,
    );
    assert.ok(
      p.x + p.w <= photos.x + photos.w + 0.01 &&
        p.y + p.h <= photos.y + photos.h + 0.01,
      `${id} : coin bas droit dans le node`,
    );
  }
  // Deux cases côte à côte qui remplissent la largeur (24 de marge, 16 entre elles).
  const [a, b] = [at(after, "p1"), at(after, "p2")];
  assert.equal(a.y, b.y);
  assert.ok(Math.abs(b.x + b.w - (photos.x + photos.w - 24)) < 0.01);
});

test("ce qui n'est pas relié ne bouge pas, sauf s'il chevauche la pose (poussé à droite)", () => {
  const { items, links } = legacy();
  const moves = arrange(items, links);
  assert.equal(moves.has("note-loin"), false);
  assert.equal(
    moves.has("seul"),
    false,
    "Moodboard sans lien, loin : à sa place",
  );
  const after = apply(items, moves);
  const right = Math.max(
    ...["rendus1", "rendus2", "mood"].map((id) => {
      const n = at(after, id);
      return n.x + n.w;
    }),
  );
  const note = at(after, "note-dans");
  assert.ok(moves.has("note-dans"));
  assert.ok(note.x >= right, "poussée à droite de la pose");
});

test("déterministe : même entrée (dans n'importe quel ordre), même sortie", () => {
  const { items, links } = legacy();
  const one = arrange(items, links);
  const two = arrange([...items].reverse(), [...links].reverse());
  assert.deepEqual(
    [...one].sort(([a], [b]) => a.localeCompare(b)),
    [...two].sort(([a], [b]) => a.localeCompare(b)),
  );
});

test("idempotent : ranger un espace rangé ne change rien", () => {
  const { items, links } = legacy();
  const after = apply(items, arrange(items, links));
  assert.equal(arrange(after, links).size, 0);
});

test("rien à ranger sans plan", () => {
  const items = [item("n", "note", 0, 0, 300, 170)];
  assert.equal(arrange(items, []).size, 0);
});
