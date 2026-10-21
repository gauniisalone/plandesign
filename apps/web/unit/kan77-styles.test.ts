// KAN-77 : gardes des styles de l'app (index.css), lancées par `npm run test:unit`.
// - aucune couleur violette ou indigo (hors couleurs des post-its, choisies par l'utilisateur,
//   et la lavande du site, petite touche jamais en action) ;
// - arrondis : 8 px (contrôles), 12 px (cartes, panneaux), 999 px (pastilles), cercles ;
// - contraste AA du texte sur chaque fond et sur l'ocre, calculé à partir des jetons.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const CSS = readFileSync(
  new URL("../src/index.css", import.meta.url),
  "utf8",
).replace(/\/\*[\s\S]*?\*\//g, "");

type Rgb = [number, number, number];

function hexRgb(hex: string): Rgb {
  const h =
    hex.length === 4
      ? [...hex.slice(1)].map((c) => c + c).join("")
      : hex.slice(1, 7);
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as Rgb;
}

/** oklch → sRGB (0–255), pour la teinte et la saturation seulement. */
function oklchRgb(l: number, c: number, hDeg: number): Rgb {
  const h = (hDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const l_ = l + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = l - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = l - 0.0894841775 * a - 1.291485548 * b;
  const [L, M, S] = [l_ ** 3, m_ ** 3, s_ ** 3];
  const lin = [
    4.0767416621 * L - 3.3077115913 * M + 0.2309699292 * S,
    -1.2684380046 * L + 2.6097574011 * M - 0.3413193965 * S,
    -0.0041960863 * L - 0.7034186147 * M + 1.707614701 * S,
  ];
  return lin.map((v) => {
    const x = Math.min(1, Math.max(0, v));
    return Math.round(
      255 * (x <= 0.0031308 ? 12.92 * x : 1.055 * x ** (1 / 2.4) - 0.055),
    );
  }) as Rgb;
}

function hsl([r, g, b]: Rgb): { h: number; s: number; l: number } {
  const [R, G, B] = [r / 255, g / 255, b / 255];
  const max = Math.max(R, G, B);
  const min = Math.min(R, G, B);
  const l = (max + min) / 2;
  const d = max - min;
  if (d === 0) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  let h =
    max === R
      ? ((G - B) / d) % 6
      : max === G
        ? (B - R) / d + 2
        : (R - G) / d + 4;
  h *= 60;
  if (h < 0) h += 360;
  return { h, s, l };
}

/** Chaque déclaration (propriété, valeur), hors post-its et lavande du site. */
function declarations(): { prop: string; value: string }[] {
  const out: { prop: string; value: string }[] = [];
  for (const m of CSS.matchAll(/([-\w]+)\s*:\s*([^;{}]+);/g)) {
    const prop = m[1]!;
    if (prop.startsWith("--postit-") || prop === "--pd-lavande") continue;
    out.push({ prop, value: m[2]! });
  }
  return out;
}

function colors(value: string): { text: string; rgb: Rgb }[] {
  const out: { text: string; rgb: Rgb }[] = [];
  for (const m of value.matchAll(/#[0-9a-f]{6}\b|#[0-9a-f]{3}\b/gi))
    out.push({ text: m[0], rgb: hexRgb(m[0]) });
  for (const m of value.matchAll(/rgba?\(\s*(\d+)[ ,]+(\d+)[ ,]+(\d+)/g))
    out.push({ text: m[0], rgb: [Number(m[1]), Number(m[2]), Number(m[3])] });
  for (const m of value.matchAll(
    /oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)/g,
  )) {
    const l = Number(m[1]) / (m[2] ? 100 : 1);
    out.push({ text: m[0], rgb: oklchRgb(l, Number(m[3]), Number(m[4])) });
  }
  return out;
}

test("aucune couleur violette ou indigo dans index.css (MH1)", () => {
  const bad: string[] = [];
  for (const { prop, value } of declarations())
    for (const c of colors(value)) {
      const { h, s, l } = hsl(c.rgb);
      // Violet et indigo : teinte de 245° à 330°, assez saturée pour se voir comme telle.
      if (h >= 245 && h <= 330 && s >= 0.25 && l > 0.08 && l < 0.97)
        bad.push(`${prop}: ${c.text} (teinte ${h.toFixed(0)}°)`);
    }
  assert.deepEqual(bad, []);
});

test("arrondis : 8, 12 et 999 px seulement, plus les cercles (MH2)", () => {
  const allowed = new Set([
    "var(--r-control)",
    "var(--r-card)",
    "var(--r-pill)",
    // Les mêmes échelles sous les noms du site (KAN-78).
    "var(--pd-radius-control)",
    "var(--pd-radius-card)",
    "var(--pd-radius-pill)",
    "50%",
    "0",
    "inherit",
  ]);
  const bad: string[] = [];
  for (const { prop, value } of declarations()) {
    if (!/^border(-[a-z]+)*-radius$/.test(prop)) continue;
    for (const part of value.trim().split(/\s+(?![^(]*\))/))
      if (!allowed.has(part)) bad.push(`${prop}: ${value.trim()}`);
  }
  assert.deepEqual(bad, []);
  assert.match(CSS, /--r-control:\s*8px;/);
  assert.match(CSS, /--r-card:\s*12px;/);
  assert.match(CSS, /--r-pill:\s*999px;/);
});

function lum([r, g, b]: Rgb): number {
  const f = (v: number) => {
    const c = v / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function ratio(a: string, b: string): number {
  const [x, y] = [lum(hexRgb(a)), lum(hexRgb(b))].sort((p, q) => q - p);
  return (x! + 0.05) / (y! + 0.05);
}

/** Les jetons d'un bloc marqué `/* kan77:<nom> *\/` (lus dans le fichier non commenté). */
function tokens(name: string): Record<string, string> {
  const raw = readFileSync(
    new URL("../src/index.css", import.meta.url),
    "utf8",
  );
  const start = raw.indexOf(`/* kan77:${name} */`);
  assert.ok(start >= 0, `bloc kan77:${name} absent`);
  const end = raw.indexOf("}", raw.indexOf("{", start));
  const out: Record<string, string> = {};
  for (const m of raw
    .slice(start, end)
    .matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\s*;/gi))
    out[m[1]!] = m[2]!;
  return out;
}

for (const theme of ["clair", "sombre-systeme", "sombre-choisi"]) {
  test(`contraste AA du texte sur chaque fond et sur l'ocre (${theme}, MH7)`, () => {
    const t = {
      ...tokens("clair"),
      ...(theme === "clair" ? {} : tokens(theme)),
    };
    const need = (fg: string, bg: string, min: number) => {
      const r = ratio(t[fg]!, t[bg]!);
      assert.ok(
        r >= min,
        `${theme} : ${fg} sur ${bg} = ${r.toFixed(2)}:1 (< ${min})`,
      );
    };
    for (const bg of ["--bg", "--surface", "--canvas", "--field"]) {
      need("--text", bg, 4.5);
      need("--muted", bg, 4.5);
      need("--link", bg, 4.5);
    }
    need("--on-primary", "--primary", 4.5);
    need("--on-primary", "--primary-hover", 4.5);
    for (const bg of ["--surface", "--canvas"]) {
      need("--line-strong", bg, 3);
      need("--primary-edge", bg, 3);
    }
  });
}
