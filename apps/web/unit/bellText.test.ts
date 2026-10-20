// KAN-52 : textes de la cloche (badge, ligne, heure relative). Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import type { BellLine } from "../src/api/client.ts";
import {
  actionText,
  badgeText,
  lineText,
  relativeTime,
} from "../src/state/bellText.ts";

const NOW = Date.parse("2026-10-20T12:00:00Z");

function line(over: Partial<BellLine>): BellLine {
  return {
    id: "1",
    type: "comment.created",
    actor_name: "Fanny",
    actor_color: "#e5484d",
    mine: false,
    project_id: "p",
    project_name: "Grenier",
    at: "2026-10-20T11:55:00Z",
    count: 1,
    unread: true,
    thread_id: "t",
    item_id: null,
    level_id: null,
    run_id: null,
    label: null,
    ...over,
  };
}

test("badge : rien quand tout est lu, 9+ au-delà de 9", () => {
  assert.equal(badgeText(0), null);
  assert.equal(badgeText(1), "1");
  assert.equal(badgeText(9), "9");
  assert.equal(badgeText(10), "9+");
});

/** Intl met des espaces insécables (« 3 h ») : comparées comme des espaces. */
const NARROW = [0xa0, 0x202f].map((c) => String.fromCharCode(c));
const plain = (s: string) =>
  NARROW.reduce((out, c) => out.split(c).join(" "), s);

test("la ligne du ticket : action, objet, projet, heure relative", () => {
  assert.equal(
    plain(lineText(line({ label: "Photo 2" }), NOW)),
    "Fanny a commenté « Photo 2 » · Grenier · il y a 5 min",
  );
  assert.equal(actionText(line({})), "Fanny a commenté l'espace de travail");
});

test("modifications regroupées, rendu prêt, arrivée", () => {
  assert.equal(
    actionText(
      line({ type: "board.changed", actor_name: "Gilles", count: 12 }),
    ),
    "Gilles a modifié l'espace de travail · 12 changements",
  );
  assert.equal(
    actionText(line({ type: "plan.changed", count: 1 })),
    "Fanny a modifié le plan",
  );
  assert.equal(
    actionText(line({ type: "render.done", mine: true })),
    "Votre rendu est prêt",
  );
  assert.equal(
    actionText(line({ type: "member.joined" })),
    "Fanny a rejoint le projet",
  );
});

test("heure relative", () => {
  assert.equal(relativeTime("2026-10-20T11:59:40Z", NOW), "à l'instant");
  assert.equal(plain(relativeTime("2026-10-20T09:00:00Z", NOW)), "il y a 3 h");
  assert.equal(relativeTime("2026-10-19T12:00:00Z", NOW), "hier");
});
