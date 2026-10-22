// KAN-91 (lot L6) : galerie de taille fixe du cadre Rendus, durée annoncée, avancement, numéro de
// photo. Lancé par `npm run test:unit` (node --test, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { RealisticRequest } from "../src/api/client";
import type { Project } from "../src/api/types.gen";
import {
  etaText,
  gallery,
  photoNumber,
  progress,
} from "../src/board/renderGallery.ts";

let n = 0;
function req(
  state: RealisticRequest["state"],
  minute: number,
  over: Partial<RealisticRequest> = {},
): RealisticRequest {
  n += 1;
  return {
    id: `r${n}`,
    level_id: "l",
    camera_id: "c",
    state,
    created_at: `2026-10-22T14:${String(minute).padStart(2, "0")}:00Z`,
    finished_at: null,
    expires_at: "2026-10-23T00:00:00Z",
    seconds: null,
    width: 64,
    height: 48,
    error: null,
    image: state === "done",
    ...over,
  };
}

test("la plus récente d'abord, quatre cases au plus, « +N » dans la dernière", () => {
  const done = [1, 2, 3, 4, 5, 6, 7, 8].map((m) => req("done", m));
  const running = req("taken", 9);
  const g = gallery([...done, running]);
  assert.equal(g.tiles.length, 4);
  assert.equal(g.tiles[0]!.kind, "running");
  assert.deepEqual(
    g.tiles.slice(1, 3).map((t) => t.request.created_at.slice(14, 16)),
    ["08", "07"],
  );
  const more = g.tiles[3]!;
  assert.equal(more.kind, "more");
  assert.equal(more.kind === "more" && more.count, 6);
  assert.equal(g.done.length, 8);
  assert.equal(g.done[0]!.created_at.slice(14, 16), "08");
});

test("tout tient : pas de case « plus anciens »", () => {
  const g = gallery([req("done", 1), req("done", 2)]);
  assert.deepEqual(
    g.tiles.map((t) => t.kind),
    ["done", "done"],
  );
});

test("un échec plus récent que tout rendu fait est signalé, pas un ancien", () => {
  const old = gallery([req("failed", 1), req("done", 2)]);
  assert.equal(old.problem, null);
  const recent = gallery([req("done", 1), req("expired", 2)]);
  assert.equal(recent.problem?.state, "expired");
  // Un rendu fait sans image (fichier perdu) n'occupe pas de case.
  assert.equal(gallery([req("done", 3, { image: false })]).tiles.length, 0);
});

test("durée annoncée : la médiane du journal, sinon ~2 min", () => {
  assert.equal(etaText(undefined), "~2 min");
  assert.equal(etaText(112), "~2 min");
  assert.equal(etaText(42), "~40 s");
  assert.equal(etaText(300), "~5 min");
});

test("avancement : seulement pendant le calcul, jamais 100 % avant l'image", () => {
  const taken = req("taken", 1, { taken_at: "2026-10-22T14:01:00Z" });
  const start = Date.parse("2026-10-22T14:01:00Z");
  assert.equal(progress(taken, 120, start + 60_000), 0.5);
  assert.equal(progress(taken, 120, start + 600_000), 0.95);
  assert.equal(progress(req("pending", 1), 120, start), null);
});

test("numéro de photo : celui de la bande de photos", () => {
  const project = { images: { a: {}, b: {} } } as unknown as Project;
  assert.equal(photoNumber(project, "b"), 2);
  assert.equal(photoNumber(project, "z"), null);
});
