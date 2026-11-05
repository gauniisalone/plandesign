// KAN-130 : désactiver un mot de « Guidé par » sur les images des Moodboards reliés, et la
// caméra d'une photo (son cône au survol). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { BoardItem, BoardLink } from "../src/api/client.ts";
import type { Level } from "../src/api/types.gen.ts";
import { photoCamera } from "../src/board/photoCamera.ts";
import {
  ignoredKeys,
  styleMembers,
  tagOf,
  toggleWord,
} from "../src/board/styleWords.ts";

const item = (id: string, extra: Partial<BoardItem> = {}): BoardItem =>
  ({
    id,
    kind: "file",
    x: 0,
    y: 0,
    w: 10,
    h: 10,
    z: 0,
    text: "",
    ref: `ref-${id}`,
    frame: null,
    reference: false,
    ...extra,
  }) as BoardItem;

const board = (): { items: BoardItem[]; links: BoardLink[] } => ({
  items: [
    item("mood-a", { kind: "frame", ref: null }),
    item("mood-b", { kind: "frame", ref: null }),
    item("renders", { kind: "frame", ref: null }),
    item("a1", { frame: "mood-a", reference: true }),
    item("a2", { frame: "mood-a", reference: true }),
    item("b1", { frame: "mood-b", reference: true }),
    item("libre", { reference: true }),
  ],
  links: [
    { id: "l1", source: "mood-a", target: "renders", kind: "guides_style" },
  ],
});

test("le motif du schéma : les espaces de la clé deviennent « _ »", () => {
  assert.equal(tagOf("white and beige"), "s:white_and_beige");
  assert.equal(tagOf("mid-century modern"), "s:mid-century_modern");
});

test("seuls les membres des Moodboards reliés guident (C1)", () => {
  const { items, links } = board();
  const ids = styleMembers(items, links, "renders").map((i) => i.id);
  assert.deepEqual(ids, ["a1", "a2"]);
});

test("un clic désactive le mot sur chaque image, un second le réactive", () => {
  const { items, links } = board();
  const members = styleMembers(items, links, "renders");
  const off = toggleWord(items, members, "white and beige", true);
  assert.deepEqual(
    off.filter((i) => i.ignored_tags?.length).map((i) => i.id),
    ["a1", "a2"],
  );
  assert.equal(off[5], items[5], "l'image d'un autre Moodboard ne change pas");
  const offMembers = styleMembers(off, links, "renders");
  assert.deepEqual([...ignoredKeys(offMembers)], ["white and beige"]);
  const again = toggleWord(off, offMembers, "white and beige", true);
  assert.deepEqual(again[3].ignored_tags, ["s:white_and_beige"], "pas de doublon");
  const on = toggleWord(off, offMembers, "white and beige", false);
  assert.equal(ignoredKeys(styleMembers(on, links, "renders")).size, 0);
});

test("une étiquette de matière n'est pas un mot de style", () => {
  const members = [item("x", { ignored_tags: ["m:lin", "s:bright"] })];
  assert.deepEqual([...ignoredKeys(members)], ["bright"]);
});

test("la caméra d'une photo, sur son niveau ; aucune si elle n'a servi à aucun plan", () => {
  const levels = {
    rdc: { elements: { w: { id: "w", type: "wall" } } },
    etage: {
      elements: {
        cam: { id: "cam", type: "camera", image_id: "photo-1" },
        libre: { id: "libre", type: "camera", image_id: null },
      },
    },
  } as unknown as Record<string, Level>;
  assert.deepEqual(photoCamera(levels, "photo-1"), {
    levelId: "etage",
    cameraId: "cam",
  });
  assert.equal(photoCamera(levels, "photo-2"), null);
});
