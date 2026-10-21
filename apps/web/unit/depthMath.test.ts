// KAN-79 : calculs de la profondeur du navigateur (taille d'entrée, grille gardée, float16).
// Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  depthGrid,
  gridSize,
  inputSize,
  preprocess,
  toHalf,
  toHalfArray,
} from "../src/depth/depthMath.ts";

test("taille d'entrée du modèle : côté le plus proche de 518, multiples de 14", () => {
  assert.deepEqual(inputSize(1280, 960), { width: 686, height: 518 });
  assert.deepEqual(inputSize(960, 1280), { width: 518, height: 686 });
  assert.deepEqual(inputSize(4032, 3024), { width: 686, height: 518 });
  assert.deepEqual(inputSize(518, 518), { width: 518, height: 518 });
});

test("grille gardée : un pixel sur ceil(grand côté / 640)", () => {
  assert.deepEqual(gridSize(1280, 960), { width: 640, height: 480 });
  assert.deepEqual(gridSize(4032, 3024), { width: 576, height: 432 });
  assert.deepEqual(gridSize(641, 3), { width: 321, height: 2 });
  assert.deepEqual(gridSize(600, 400), { width: 600, height: 400 });
});

test("float16 : arrondi au pair le plus proche, comme numpy", () => {
  assert.equal(toHalf(1), 0x3c00);
  assert.equal(toHalf(20), 0x4d00);
  assert.equal(toHalf(0.1), 0x2e66);
  assert.equal(toHalf(65504), 0x7bff);
  assert.equal(toHalf(1e9), 0x7c00);
  assert.equal(toHalf(1e-9), 0);
  assert.equal(toHalf(2.5e-5), 0x01a3); // sous-normal
  assert.equal(toHalf(1 + 2 ** -11), 0x3c00); // milieu exact : pair
  assert.equal(toHalf(1 + 3 * 2 ** -11), 0x3c02);
  assert.deepEqual([...toHalfArray(new Float32Array([1, 2]))], [0x3c00, 0x4000]);
});

test("photo uniforme : entrée normalisée ImageNet, à la taille du modèle", () => {
  const w = 128;
  const h = 96;
  const rgba = new Uint8ClampedArray(w * h * 4).fill(128);
  const out = preprocess(rgba, w, h);
  // Agrandie : la largeur, plus proche de 518 en proportion, est calée (518 × 392).
  assert.deepEqual([out.width, out.height], [518, 392]);
  const plane = out.width * out.height;
  const expect = [
    (128 / 255 - 0.485) / 0.229,
    (128 / 255 - 0.456) / 0.224,
    (128 / 255 - 0.406) / 0.225,
  ];
  for (let c = 0; c < 3; c++)
    for (const i of [0, 1234, plane - 1])
      assert.ok(Math.abs(out.data[c * plane + i]! - expect[c]!) < 1e-6);
});

// La bicubique de torch (A = -0,75) ne reproduit pas exactement une pente : à 5e-4 près.
test("sortie du modèle → grille : lue au pixel gardé, bornée à la plage du modèle", () => {
  const dw = 686;
  const dh = 518;
  const depth = new Float32Array(dw * dh);
  for (let y = 0; y < dh; y++)
    for (let x = 0; x < dw; x++) depth[y * dw + x] = 1 + x / dw + (2 * y) / dh;
  const grid = depthGrid(depth, dw, dh, 1280, 960);
  assert.deepEqual([grid.width, grid.height], [640, 480]);
  // Loin des bords : valeur du plan au pixel gardé (x = 2i, y = 2j de la photo).
  for (const [i, j] of [
    [100, 100],
    [320, 240],
    [500, 400],
  ] as const) {
    const sx = (2 * i + 0.5) * (dw / 1280) - 0.5;
    const sy = (2 * j + 0.5) * (dh / 960) - 0.5;
    const want = 1 + sx / dw + (2 * sy) / dh;
    assert.ok(Math.abs(grid.data[j * 640 + i]! - want) < 5e-4);
  }
  const flat = depthGrid(new Float32Array(dw * dh).fill(2.5), dw, dh, 1280, 960);
  assert.ok(flat.data.every((v) => Math.abs(v - 2.5) < 1e-6));
  const far = depthGrid(new Float32Array(dw * dh).fill(50), dw, dh, 1280, 960);
  assert.equal(far.data.reduce((a, b) => Math.max(a, b), 0), 20);
  const near = depthGrid(new Float32Array(dw * dh), dw, dh, 1280, 960);
  const low = near.data.reduce((a, b) => Math.min(a, b), Infinity);
  assert.ok(Math.abs(low - 0.01) < 1e-6);
});
