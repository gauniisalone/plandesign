// KAN-105 L2 : la raison d'un refus (`reason`) lue par le front. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import { reasonOf, sessionLost } from "../src/api/refusal.ts";

test("seule une 401 login_required (ou sans raison) renvoie à la connexion", () => {
  assert.equal(sessionLost(401, "login_required"), true);
  assert.equal(sessionLost(401, undefined), true);
  // Mot de passe faux, jeton refusé : on reste sur la page.
  assert.equal(sessionLost(401, "bad_credentials"), false);
  assert.equal(sessionLost(401, "token_required"), false);
  // Une 403 n'est jamais une session perdue (preuve récente, rôle…).
  assert.equal(sessionLost(403, "reauth"), false);
  assert.equal(sessionLost(403, undefined), false);
});

test("la raison est lue dans le corps, sinon absente", () => {
  assert.equal(reasonOf({ detail: "x", reason: "space_full" }), "space_full");
  assert.equal(reasonOf({ detail: "x" }), undefined);
  assert.equal(reasonOf({ reason: 3 }), undefined);
  assert.equal(reasonOf(null), undefined);
  assert.equal(reasonOf("texte"), undefined);
});
