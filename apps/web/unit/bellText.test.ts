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
    actor_id: "f",
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
    item_kind: null,
    added: 0,
    changed: 0,
    removed: 0,
    ...over,
  };
}

test("KAN-58 : le détail des modifications de l'espace de travail et du plan", () => {
  const board = { type: "board.changed" as const, actor_name: "Gilles" };
  assert.equal(
    actionText(
      line({ ...board, label: "Photo 2", item_kind: "photo", removed: 1 }),
    ),
    "Gilles a supprimé « Photo 2 »",
  );
  assert.equal(
    actionText(line({ ...board, item_kind: "note", added: 1 })),
    "Gilles a ajouté un post-it",
  );
  // KAN-65 : seule la couleur a changé.
  assert.equal(
    actionText(
      line({
        ...board,
        label: "Canapé",
        item_kind: "note",
        changed: 1,
        recolored: 1,
      }),
    ),
    "Gilles a changé la couleur de « Canapé »",
  );
  assert.equal(
    actionText(line({ ...board, item_kind: "note", changed: 1, recolored: 1 })),
    "Gilles a changé la couleur d'un post-it",
  );
  assert.equal(
    actionText(line({ ...board, changed: 3, recolored: 3 })),
    "Gilles a changé la couleur de 3 post-its",
  );
  assert.equal(
    actionText(line({ ...board, label: "Canapé", changed: 1, recolored: 0 })),
    "Gilles a modifié « Canapé »",
  );
  assert.equal(
    actionText(
      line({
        ...board,
        label: "Canapé",
        count: 3,
        added: 1,
        changed: 1,
        removed: 1,
      }),
    ),
    "Gilles a modifié « Canapé » · 3 changements",
  );
  assert.equal(
    actionText(line({ ...board, count: 2, added: 2, removed: 1 })),
    "Gilles a modifié l'espace de travail · 2 ajoutés, 1 supprimé",
  );
  assert.equal(
    actionText(
      line({ type: "plan.changed", label: "Annulé : Ajouter un mur" }),
    ),
    "Fanny a modifié le plan · Annulé : Ajouter un mur",
  );
  assert.equal(
    actionText(line({ type: "comment.resolved", label: "Photo 2" })),
    "Fanny a résolu le fil sur « Photo 2 »",
  );
});

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

test("KAN-78 : réactions, seules ou regroupées", () => {
  const reaction = { type: "reaction.added" as const };
  assert.equal(
    actionText(line({ ...reaction, emoji: "😍", label: "Rendu 2" })),
    "Fanny a réagi 😍 à « Rendu 2 »",
  );
  assert.equal(
    actionText(line({ ...reaction, emoji: "👍", item_kind: "render" })),
    "Fanny a réagi 👍 à un rendu",
  );
  assert.equal(
    actionText(line({ ...reaction, emoji: null, count: 3, label: "Rendu 2" })),
    "Fanny a réagi 3 fois à « Rendu 2 »",
  );
  assert.equal(
    actionText(line({ ...reaction, emoji: null, count: 2 })),
    "Fanny a réagi 2 fois",
  );
});

test("heure relative", () => {
  assert.equal(relativeTime("2026-10-20T11:59:40Z", NOW), "à l'instant");
  assert.equal(plain(relativeTime("2026-10-20T09:00:00Z", NOW)), "il y a 3 h");
  assert.equal(relativeTime("2026-10-19T12:00:00Z", NOW), "hier");
});
