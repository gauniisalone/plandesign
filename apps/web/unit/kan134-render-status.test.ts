// KAN-134 : le statut des rendus réalistes n'est plus figé pour la page, un échec n'est pas un
// refus, et un /api/health muet ne fait plus croire à des rendus. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { RealisticStatus } from "../src/api/client.ts";
import {
  UNKNOWN,
  capabilitiesFrom,
  sameCapabilities,
} from "../src/state/capsFrom.ts";
import {
  STATUS_DOWN,
  UNKNOWN_STATUS,
  afterAnswer,
  afterFailure,
  allowedOf,
  retryAfterMs,
  statusDown,
} from "../src/state/renderStatusState.ts";

const YES: RealisticStatus = {
  allowed: true,
  worker: "online",
  quota: 20,
  used_today: 0,
};
const NO: RealisticStatus = { ...YES, allowed: false };

/** La réponse de /api/health en production, le 5 nov. au soir (relevée par augus). */
const PROD_HEALTH = {
  status: "ok",
  blender: false,
  weights: ["depth_anything_v2_metric_indoor_small_web@1.0"],
  exposed: true,
  accounts: true,
};

test("la production : en ligne, sans Blender ni poste déclaré : le node des rendus réalistes", () => {
  const caps = capabilitiesFrom(PROD_HEALTH);
  assert.equal(caps.known, true);
  assert.equal(caps.online, true);
  assert.equal(caps.renders, false);
});

test("health muet : « inconnu », rien d'offert (avant : renders vrai, node des rendus caché)", () => {
  assert.equal(UNKNOWN.known, false);
  assert.equal(UNKNOWN.renders, false);
  assert.equal(UNKNOWN.online, false);
  assert.equal(sameCapabilities(UNKNOWN, capabilitiesFrom(PROD_HEALTH)), false);
});

test("un échec n'est pas un refus : il se dit, sans retirer un « oui » déjà reçu", () => {
  assert.equal(allowedOf(UNKNOWN_STATUS), null);
  const failed = afterFailure(UNKNOWN_STATUS);
  assert.equal(allowedOf(failed), false);
  assert.equal(statusDown(failed), true, "« Statut des rendus indisponible »");
  assert.match(STATUS_DOWN, /indisponible, nouvel essai/);
  // Un « oui », puis un échec passager : « Rendre » reste, et rien d'alarmant ne s'affiche.
  const blip = afterFailure(afterAnswer(YES));
  assert.equal(allowedOf(blip), true);
  assert.equal(statusDown(blip), false);
});

test("un « non » n'est jamais définitif : la réponse suivante le remplace", () => {
  const refused = afterAnswer(NO);
  assert.equal(allowedOf(refused), false);
  assert.equal(statusDown(refused), false, "un refus se dit « bientôt en ligne »");
  assert.equal(allowedOf(afterAnswer(YES)), true);
  // Échec, puis la bonne réponse (le cas d'augus : le bouton revient).
  const failedFirst = afterFailure(UNKNOWN_STATUS);
  assert.equal(statusDown(failedFirst), true);
  const recovered = afterAnswer(YES);
  assert.equal(allowedOf(recovered), true);
  assert.equal(statusDown(recovered), false);
});

test("nouvel essai : 3 s, puis de plus en plus espacé, jamais plus d'une minute", () => {
  const failures = [afterFailure(UNKNOWN_STATUS)];
  for (let i = 0; i < 6; i++) failures.push(afterFailure(failures.at(-1)!));
  const delays = failures.map((s) => (s.kind === "failed" ? retryAfterMs(s.failures) : -1));
  assert.deepEqual(delays, [3_000, 6_000, 12_000, 24_000, 48_000, 60_000, 60_000]);
});
