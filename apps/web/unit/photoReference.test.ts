// KAN-66 : mesure de référence et origine de l'échelle lues dans le projet. Lancé par
// `npm run test:unit` (node --test, TypeScript lu directement par Node 24, sans dépendance).
import assert from "node:assert/strict";
import { test } from "node:test";

import type { Project } from "../src/api/types.gen";
import {
  beforeAfter,
  fusionOf,
  fusionTooltip,
  measurability,
  referenceOf,
  type PhotoScaleBasis,
} from "../src/state/photoReference.ts";

const IMG = "img-1";
const basis = (over: Partial<PhotoScaleBasis> = {}): PhotoScaleBasis => ({
  source: "camera_height",
  text: "Échelle : hauteur de prise de vue supposée 1,50 m (par défaut)",
  image_id: IMG,
  segment_id: null,
  factor: 1,
  before: null,
  previous: null,
  camera_height_m: 1.5,
  ...over,
});

function project(levels: Record<string, unknown>, segments: unknown[] = []) {
  return {
    images: { [IMG]: { id: IMG, reference_segments: segments } },
    levels,
  } as unknown as Project;
}
const level = (scale_basis: unknown, elements = {}, variant_of = null) => ({
  scale_basis,
  elements,
  variant_of,
});

test("mesurable : un plan estimé depuis la photo avec un facteur", () => {
  const p = project({ a: level(basis()) });
  const m = measurability(p, IMG);
  assert.equal(m.kind, "ready");
  assert.equal(m.kind === "ready" && m.levelId, "a");
});

test("facteur absent : à réestimer sur ce plan", () => {
  const p = project({ a: level(basis({ factor: null })) });
  assert.deepEqual(measurability(p, IMG), { kind: "reestimate", levelId: "a" });
});

test("photo d'avant KAN-66 : le plan qui porte sa caméra la réestime", () => {
  const cam = { c: { type: "camera", image_id: IMG } };
  const p = project({ a: level(null), b: level(null, cam) });
  assert.deepEqual(measurability(p, IMG), { kind: "reestimate", levelId: "b" });
});

test("photo qui n'a construit aucun plan", () => {
  assert.deepEqual(measurability(project({ a: level(null) }), IMG), {
    kind: "none",
  });
});

test("l'étage passe avant une variante", () => {
  const p = project({
    v: level(basis(), {}, "a" as never),
    a: level(basis()),
  });
  const m = measurability(p, IMG);
  assert.equal(m.kind === "ready" && m.levelId, "a");
});

test("la mesure posée, p1 et p2 en nombres", () => {
  const seg = {
    id: "s",
    p1: [262, 600],
    p2: [262, 244],
    length_m: 2.04,
    label: "porte",
    author: "local",
    created_at: "2026-10-20T17:42:00Z",
  };
  const r = referenceOf(project({}, [seg]), IMG);
  assert.deepEqual(r?.p1, [262, 600]);
  assert.equal(r?.label, "porte");
  assert.equal(referenceOf(project({}), IMG), null);
});

test("KAN-95 : une photo recoupée est mesurable avec son propre facteur", () => {
  const meta = (kept: boolean, factor: number, reason: string) => ({
    fusion: {
      kept,
      reason,
      weight: kept ? 0.5 : 0,
      ridge_m: 2.4,
      pitch_deg: 33.2,
    },
    fusion_factor: factor,
    fusion_summary: "2 photos concordent à ±6 %",
  });
  const cams = {
    c1: {
      type: "camera",
      image_id: IMG,
      metadata: meta(true, 0.8, "faîtage et pente"),
    },
    c2: {
      type: "camera",
      image_id: "img-2",
      metadata: meta(true, 0.6, "pente seulement"),
    },
  };
  const p = project({ a: level(basis(), cams) });
  const m = measurability(p, "img-2");
  assert.equal(m.kind, "ready");
  assert.equal(m.kind === "ready" && m.basis.factor, 0.6);
  assert.equal(m.kind === "ready" && m.basis.image_id, "img-2");
  const lvl = (p.levels as Record<string, Parameters<typeof fusionOf>[0]>).a;
  const fused = fusionOf(lvl);
  assert.equal(fused?.summary, "2 photos concordent à ±6 %");
  const tip = fusionTooltip(fused!);
  assert.match(
    tip,
    /^Photo 1 : faîtage et pente · 50 % \(faîtage 2,40 m, pente 33°\)/,
  );
  assert.equal(tip.split("\n").length, 2);
  assert.equal(fusionOf(level(basis()) as never), null);
});

test("« 4,06 → 5,00 m » depuis la boîte d'avant et le facteur", () => {
  const lines = beforeAfter(
    basis({
      factor: 5 / 4.06,
      before: { length_m: 4.06, width_m: 3, height_m: 2.4 },
    }),
  );
  assert.equal(lines?.[0], "Longueur 4,06 → 5,00 m");
  assert.equal(lines?.[1], "largeur 3,00 → 3,69 m");
  assert.equal(beforeAfter(basis({ factor: 1 })), null);
});
