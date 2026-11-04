// KAN-119 : « Supprimer tous mes projets ». Lancé par `npm run test:unit` (node --test,
// TypeScript lu directement par Node 24, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  confirmMatches,
  confirmWord,
  deleteAllButton,
  deleteAllTitle,
  foldedLabel,
  namedResult,
  ownedProjects,
  progressLine,
  quotedNames,
  resultLine,
  splitNames,
} from "../src/state/deleteAll.ts";

const p = (id: string, role?: string) => ({ id, name: id, role });

test("seuls les projets à soi partent ; en mode local, tout est à soi", () => {
  const list = [p("Séjour", "owner"), p("Chambre", "editor"), p("Combles")];
  assert.deepEqual(
    ownedProjects(list).map((x) => x.id),
    ["Séjour", "Combles"],
  );
});

test("SUPPRIMER : casse et espaces autour ignorés, rien d'autre", () => {
  assert.equal(confirmMatches("SUPPRIMER"), true);
  assert.equal(confirmMatches("  supprimer "), true);
  assert.equal(confirmMatches("SUPPRIME"), false);
  assert.equal(confirmMatches("SUP PRIMER"), false);
  assert.equal(confirmMatches(""), false);
  assert.equal(confirmWord("  supprimer "), "SUPPRIMER"); // ce qui part au serveur
});

test("titres au singulier et au pluriel", () => {
  assert.equal(deleteAllTitle(1), "Supprimer votre projet ?");
  assert.equal(deleteAllTitle(2), "Supprimer vos 2 projets ?");
  assert.equal(deleteAllButton(2), "Supprimer mes 2 projets");
  assert.equal(progressLine(1), "Suppression de votre projet…");
  assert.equal(progressLine(3), "Suppression de vos 3 projets…");
});

test("noms : « A » et « B », repliés au-delà de 10", () => {
  assert.equal(quotedNames(["Séjour"]), "« Séjour »");
  assert.equal(quotedNames(["Séjour", "Combles"]), "« Séjour » et « Combles »");
  assert.equal(quotedNames(["A", "B", "C"]), "« A », « B » et « C »");
  const ten = Array.from({ length: 10 }, (_, i) => `P${i}`);
  assert.deepEqual(splitNames(ten).folded, []);
  const many = splitNames([...ten, "P10", "P11"]);
  assert.equal(many.shown.length, 10);
  assert.deepEqual(many.folded, ["P10", "P11"]);
  assert.equal(foldedLabel(7), "et 7 autres…");
  assert.equal(foldedLabel(1), "et 1 autre…");
});

test("le bilan du serveur, nommé ; le message nomme ce qui reste", () => {
  const list = [p("Séjour"), p("Combles"), p("Bureau")];
  const result = namedResult(list, {
    deleted: ["Séjour", "Bureau"],
    failed: ["Combles"],
  });
  assert.deepEqual(result, { deleted: ["Séjour", "Bureau"], failed: ["Combles"] });
  // Sans réponse (réseau coupé, mot refusé, trop de demandes) : tout reste.
  assert.deepEqual(namedResult(list, null), {
    deleted: [],
    failed: ["Séjour", "Combles", "Bureau"],
  });
  assert.equal(
    resultLine(result),
    "2 supprimés, 1 en échec : « Combles » reste, réessayez.",
  );
  assert.equal(
    resultLine({ deleted: ["A"], failed: ["B", "C"] }),
    "1 supprimé, 2 en échec : « B » et « C » restent, réessayez.",
  );
});

test("tout est parti", () => {
  assert.equal(
    resultLine({ deleted: ["A", "B"], failed: [] }),
    "2 projets supprimés. La place est libérée.",
  );
  assert.equal(
    resultLine({ deleted: ["A"], failed: [] }),
    "1 projet supprimé. La place est libérée.",
  );
});
