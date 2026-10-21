/** KAN-79 : calculs de la profondeur du navigateur, sans DOM ni bibliothèque (testés par
 *  `npm run test:unit`). Calqués sur le serveur pour que le navigateur donne la même carte :
 *  - entrée du modèle : processeur DPT de transformers (côté le plus proche de 518 px,
 *    multiples de 14, bicubique avec anticrénelage comme PIL, normalisation ImageNet) ;
 *  - sortie : `post_process_depth_estimation` (bicubique de torch, align_corners=False)
 *    ramenée à la taille de la photo, puis un pixel sur `step` (grille gardée par KAN-66). */

export const MODEL_SIDE = 518;
export const MULTIPLE = 14;
export const GRID_MAX_SIDE = 640;
export const DEPTH_MIN_M = 0.01;
export const DEPTH_MAX_M = 20;
const MEAN = [0.485, 0.456, 0.406] as const;
const STD = [0.229, 0.224, 0.225] as const;

/** `round` de Python (au pair le plus proche sur un .5 exact). */
function pyRound(v: number): number {
  const f = Math.floor(v);
  const d = v - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

/** Taille d'entrée du modèle pour une photo `width` × `height` (DPT, keep_aspect_ratio). */
export function inputSize(
  width: number,
  height: number,
): { width: number; height: number } {
  let sh = MODEL_SIDE / height;
  let sw = MODEL_SIDE / width;
  if (Math.abs(1 - sw) < Math.abs(1 - sh)) sh = sw;
  else sw = sh;
  const fit = (v: number) => pyRound(v / MULTIPLE) * MULTIPLE;
  return { width: fit(sw * width), height: fit(sh * height) };
}

export function gridStep(width: number, height: number): number {
  return Math.max(1, Math.ceil(Math.max(width, height) / GRID_MAX_SIDE));
}

/** Taille de la grille gardée (largeur, hauteur) : `depth[::step, ::step]`. */
export function gridSize(
  width: number,
  height: number,
): { width: number; height: number } {
  const step = gridStep(width, height);
  return { width: Math.ceil(width / step), height: Math.ceil(height / step) };
}

// ------------------------------------------------------------------ redimensionnement
function cubicPil(x: number): number {
  // PIL et torchvision (antialias) : a = -0,5.
  const a = -0.5;
  const t = Math.abs(x);
  if (t < 1) return ((a + 2) * t - (a + 3)) * t * t + 1;
  if (t < 2) return (((t - 5) * t + 8) * t - 4) * a;
  return 0;
}

interface Taps {
  start: Int32Array;
  count: Int32Array;
  weights: Float64Array;
  span: number;
}

function resampleTaps(inSize: number, outSize: number): Taps {
  const scale = inSize / outSize;
  const filterScale = Math.max(scale, 1);
  const support = 2 * filterScale;
  const span = Math.ceil(support) * 2 + 1;
  const start = new Int32Array(outSize);
  const count = new Int32Array(outSize);
  const weights = new Float64Array(outSize * span);
  for (let i = 0; i < outSize; i++) {
    const center = (i + 0.5) * scale;
    const lo = Math.max(Math.trunc(center - support + 0.5), 0);
    const hi = Math.min(Math.trunc(center + support + 0.5), inSize);
    let total = 0;
    for (let x = lo; x < hi; x++) {
      const w = cubicPil((x - center + 0.5) / filterScale);
      weights[i * span + (x - lo)] = w;
      total += w;
    }
    if (total !== 0)
      for (let k = 0; k < hi - lo; k++)
        weights[i * span + k] = weights[i * span + k]! / total;
    start[i] = lo;
    count[i] = hi - lo;
  }
  return { start, count, weights, span };
}

const clamp8 = (v: number) => Math.min(255, Math.max(0, Math.round(v)));

/** RGBA (ImageData) → entrée du modèle : Float32Array [3, h, w] normalisée. Deux passes
 *  séparables (largeur puis hauteur), arrondies en 8 bits entre les deux, comme PIL. */
export function preprocess(
  rgba: Uint8ClampedArray | Uint8Array,
  width: number,
  height: number,
): { data: Float32Array; width: number; height: number } {
  const out = inputSize(width, height);
  const tx = resampleTaps(width, out.width);
  const ty = resampleTaps(height, out.height);
  // Passe horizontale : height × out.width × 3.
  const mid = new Uint8Array(height * out.width * 3);
  for (let y = 0; y < height; y++) {
    const row = y * width * 4;
    for (let i = 0; i < out.width; i++) {
      let r = 0;
      let g = 0;
      let b = 0;
      const s = tx.start[i]!;
      const n = tx.count[i]!;
      for (let k = 0; k < n; k++) {
        const w = tx.weights[i * tx.span + k]!;
        const p = row + (s + k) * 4;
        r += w * rgba[p]!;
        g += w * rgba[p + 1]!;
        b += w * rgba[p + 2]!;
      }
      const q = (y * out.width + i) * 3;
      mid[q] = clamp8(r);
      mid[q + 1] = clamp8(g);
      mid[q + 2] = clamp8(b);
    }
  }
  const plane = out.width * out.height;
  const data = new Float32Array(3 * plane);
  for (let j = 0; j < out.height; j++) {
    const s = ty.start[j]!;
    const n = ty.count[j]!;
    for (let i = 0; i < out.width; i++) {
      let r = 0;
      let g = 0;
      let b = 0;
      for (let k = 0; k < n; k++) {
        const w = ty.weights[j * ty.span + k]!;
        const q = ((s + k) * out.width + i) * 3;
        r += w * mid[q]!;
        g += w * mid[q + 1]!;
        b += w * mid[q + 2]!;
      }
      const o = j * out.width + i;
      data[o] = (clamp8(r) / 255 - MEAN[0]) / STD[0];
      data[plane + o] = (clamp8(g) / 255 - MEAN[1]) / STD[1];
      data[2 * plane + o] = (clamp8(b) / 255 - MEAN[2]) / STD[2];
    }
  }
  return { data, width: out.width, height: out.height };
}

// ------------------------------------------------------------------ sortie → grille
function cubicTorch(t: number): [number, number, number, number] {
  // torch upsample_bicubic2d : A = -0,75.
  const A = -0.75;
  const c1 = (x: number) => ((A + 2) * x - (A + 3)) * x * x + 1;
  const c2 = (x: number) => ((A * x - 5 * A) * x + 8 * A) * x - 4 * A;
  return [c2(t + 1), c1(t), c1(1 - t), c2(2 - t)];
}

/** Profondeur du modèle (`depth`, `dw` × `dh`) lue aux pixels gardés de la photo
 *  (`width` × `height`, un sur `step`), comme le serveur : bicubique de torch vers la taille
 *  de la photo puis `[::step, ::step]`. Bornée à la plage du modèle. */
export function depthGrid(
  depth: Float32Array,
  dw: number,
  dh: number,
  width: number,
  height: number,
): { data: Float32Array; width: number; height: number } {
  const step = gridStep(width, height);
  const size = gridSize(width, height);
  const axis = (n: number, inSize: number, outSize: number) => {
    const idx = new Int32Array(n * 4);
    const w = new Float64Array(n * 4);
    const scale = inSize / outSize;
    for (let i = 0; i < n; i++) {
      const src = (i * step + 0.5) * scale - 0.5;
      const f = Math.floor(src);
      const c = cubicTorch(src - f);
      for (let k = 0; k < 4; k++) {
        idx[i * 4 + k] = Math.min(Math.max(f - 1 + k, 0), inSize - 1);
        w[i * 4 + k] = c[k]!;
      }
    }
    return { idx, w };
  };
  const ax = axis(size.width, dw, width);
  const ay = axis(size.height, dh, height);
  const data = new Float32Array(size.width * size.height);
  for (let j = 0; j < size.height; j++) {
    for (let i = 0; i < size.width; i++) {
      let v = 0;
      for (let a = 0; a < 4; a++) {
        const row = ay.idx[j * 4 + a]! * dw;
        const wy = ay.w[j * 4 + a]!;
        for (let b = 0; b < 4; b++)
          v += wy * ax.w[i * 4 + b]! * depth[row + ax.idx[i * 4 + b]!]!;
      }
      data[j * size.width + i] = Math.min(
        DEPTH_MAX_M,
        Math.max(DEPTH_MIN_M, Number.isFinite(v) ? v : DEPTH_MIN_M),
      );
    }
  }
  return { data, width: size.width, height: size.height };
}

// ------------------------------------------------------------------ float16
const f32 = new Float32Array(1);
const u32 = new Uint32Array(f32.buffer);

/** Float32 → bits float16 (arrondi au pair le plus proche), comme `astype(np.float16)`. */
export function toHalf(value: number): number {
  f32[0] = value;
  const x = u32[0]!;
  const sign = (x >>> 16) & 0x8000;
  const exp = (x >>> 23) & 0xff;
  let mant = x & 0x7fffff;
  if (exp === 0xff) return sign | 0x7c00 | (mant ? 0x200 : 0);
  let e = exp - 127 + 15;
  if (e >= 0x1f) return sign | 0x7c00;
  if (e <= 0) {
    if (e < -10) return sign;
    mant |= 0x800000;
    const shift = 14 - e;
    let half = mant >>> shift;
    const rest = mant & ((1 << shift) - 1);
    const mid = 1 << (shift - 1);
    if (rest > mid || (rest === mid && half & 1)) half++;
    return sign | half;
  }
  let half = (e << 10) | (mant >>> 13);
  const rest = mant & 0x1fff;
  if (rest > 0x1000 || (rest === 0x1000 && half & 1)) half++;
  e = half >>> 10;
  return sign | (e >= 0x1f ? 0x7c00 : half);
}

export function toHalfArray(values: Float32Array): Uint16Array {
  const out = new Uint16Array(values.length);
  for (let i = 0; i < values.length; i++) out[i] = toHalf(values[i]!);
  return out;
}
