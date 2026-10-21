// KAN-78, Must have 10 : ce qu'il faut animer entre deux lectures des pastilles. Lancé par
// `npm run test:unit` (node --test, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { ReactionPill } from "../src/api/client.ts";
import { diffPills } from "../src/state/reactionFx.ts";

const pill = (over: Partial<ReactionPill>): ReactionPill => ({
  item_id: "a",
  key: "love",
  emoji: "😍",
  count: 1,
  mine: false,
  names: ["Gilles"],
  ...over,
});

test("ma réaction donnée : pop, et le chiffre glisse s'il existait", () => {
  assert.deepEqual(diffPills([], [pill({ mine: true })]).pops, ["a|love"]);
  const fx = diffPills([pill({})], [pill({ count: 2, mine: true })]);
  assert.deepEqual(fx.pops, ["a|love"]);
  assert.deepEqual(fx.slides, ["a|love"]);
  assert.deepEqual(fx.floats, []);
});

test("la réaction d'un autre arrive : l'emoji monte, une fois", () => {
  assert.deepEqual(diffPills([], [pill({})]).floats, ["a|love"]);
  const fx = diffPills(
    [pill({ mine: true })],
    [pill({ mine: true, count: 2 })],
  );
  assert.deepEqual(fx.floats, ["a|love"]);
  assert.deepEqual(fx.pops, []);
});

test("je retire la mienne : fondu, ou la pastille à 0 se replie", () => {
  const kept = diffPills(
    [pill({ mine: true, count: 2 })],
    [pill({ count: 1 })],
  );
  assert.deepEqual(kept.faded, ["a|love"]);
  assert.deepEqual(kept.slides, ["a|love"]);
  const gone = diffPills([pill({ mine: true })], []);
  assert.deepEqual(
    gone.leaving.map((p) => p.key),
    ["love"],
  );
});

test("rien n'a changé : rien à animer", () => {
  const fx = diffPills([pill({})], [pill({})]);
  assert.deepEqual(fx, {
    pops: [],
    slides: [],
    faded: [],
    floats: [],
    leaving: [],
  });
});
