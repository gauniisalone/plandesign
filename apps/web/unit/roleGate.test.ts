// KAN-47 : l'état « rôle inconnu » d'un lien direct vers l'import. Lancé par `npm run test:unit`
// (node --test, TypeScript lu directement par Node 24, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import { canEdit, importGate } from "../src/state/roleGate.ts";

test("rôle pas encore lu : l'atelier attend, rien d'éditable", () => {
  assert.equal(importGate(null), "waiting");
  assert.equal(canEdit(null), false);
});

test("qui peut éditer reste sur l'import", () => {
  assert.equal(importGate("editor"), "import");
  assert.equal(importGate("owner"), "import");
});

test("lecteur et commentateur passent à l'espace de travail, en lecture seule", () => {
  assert.equal(importGate("viewer"), "board");
  assert.equal(importGate("commenter"), "board");
  assert.equal(canEdit("viewer"), false);
  assert.equal(canEdit("commenter"), false);
});
