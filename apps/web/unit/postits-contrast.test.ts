// KAN-65 : chaque couleur de post-it, en clair comme en sombre, garde un texte en contraste
// AA (4,5:1 au moins). Lit les jetons de index.css. Lancé par `npm run test:unit`.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const CSS = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const COLORS = [
  "yellow",
  "orange",
  "pink",
  "violet",
  "blue",
  "green",
  "gray",
  "white",
];

function block(name: string): string {
  const start = CSS.indexOf(`/* postits:${name} */`);
  assert.ok(start >= 0, `bloc ${name} absent`);
  const end = CSS.indexOf("/* postits:", start + 10);
  return CSS.slice(start, end);
}

function token(css: string, name: string): string {
  const m = new RegExp(`--postit-${name}:\\s*(#[0-9a-f]{6});`, "i").exec(css);
  assert.ok(m, `--postit-${name} absent`);
  return m[1];
}

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

for (const theme of ["light", "dark-system", "dark-chosen"]) {
  test(`texte AA sur les huit couleurs (${theme})`, () => {
    const css = block(theme);
    for (const color of COLORS) {
      const ratio = contrast(token(css, color), token(css, `${color}-ink`));
      assert.ok(ratio >= 4.5, `${theme} ${color} : ${ratio.toFixed(2)}:1`);
    }
  });
}

test("le sombre du système et le sombre choisi sont les mêmes", () => {
  const sys = block("dark-system");
  const chosen = block("dark-chosen");
  for (const color of COLORS)
    for (const name of [color, `${color}-ink`])
      assert.equal(token(sys, name), token(chosen, name), name);
});
