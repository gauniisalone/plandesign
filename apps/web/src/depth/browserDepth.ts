/** KAN-79 : profondeur d'une photo calculée dans le navigateur (mode comptes, serveur sans
 *  GPU). Le modèle (ONNX fp16, ~50 Mo) vient de l'API de PlanDesign et reste dans le Cache
 *  Storage ; la bibliothèque (onnxruntime-web, ~28 Mo de WASM) est servie par le front sous
 *  /ort/. Aucune requête ailleurs que vers PlanDesign ; la photo ne quitte pas l'appareil pour
 *  ce calcul. WebGPU s'il est là, sinon WASM. */
import type * as Ort from "onnxruntime-web";
import { depthGrid, gridSize, preprocess, toHalfArray } from "./depthMath";

const ORT_DIR = "/ort/";
const ORT_MODULE = `${ORT_DIR}ort.webgpu.min.mjs`;
export const MODEL_URL = "/api/web-models/depth";
const CACHE_NAME = "plandesign-modeles-v1";

export interface DepthProgress {
  label: string;
  /** 0 à 1, ou null quand l'étape n'a pas de mesure (calcul). */
  fraction: number | null;
}

export interface BrowserDepthResult {
  /** Bits float16, ligne par ligne : la grille gardée par le serveur. */
  grid: Uint16Array;
  gridWidth: number;
  gridHeight: number;
  width: number;
  height: number;
  device: "webgpu" | "wasm" | "simulé";
}

/** La photo ne fait pas la taille connue du serveur (orientation, recompression) : relire
 *  celle du serveur. */
export class PhotoSizeMismatch extends Error {}

type DepthStub = (
  width: number,
  height: number,
  gridWidth: number,
  gridHeight: number,
) => Float32Array;

/** Tests e2e seulement : profondeur simulée, sans modèle ni bibliothèque. */
function stub(): DepthStub | undefined {
  return (globalThis as { __planDesignDepthStub?: DepthStub })
    .__planDesignDepthStub;
}

async function openCache(): Promise<Cache | null> {
  try {
    return typeof caches === "undefined" ? null : await caches.open(CACHE_NAME);
  } catch {
    return null; // navigation privée, stockage refusé : le modèle est retéléchargé
  }
}

/** Le modèle est-il déjà dans le navigateur ? (sinon, le premier dépôt l'annonce) */
export async function modelCached(): Promise<boolean> {
  if (stub()) return true;
  const cache = await openCache();
  try {
    return Boolean(cache && (await cache.match(MODEL_URL)));
  } catch {
    return false;
  }
}

async function readAll(
  res: Response,
  onProgress: (p: DepthProgress) => void,
): Promise<ArrayBuffer> {
  const total = Number(res.headers.get("Content-Length") ?? "0");
  if (!res.body) return res.arrayBuffer();
  const reader = res.body.getReader();
  const parts: Uint8Array[] = [];
  let got = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value);
    got += value.length;
    onProgress({
      label: "Téléchargement du modèle de profondeur (une seule fois)",
      fraction: total > 0 ? Math.min(1, got / total) : null,
    });
  }
  const out = new Uint8Array(got);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out.buffer;
}

async function modelBytes(
  onProgress: (p: DepthProgress) => void,
): Promise<ArrayBuffer> {
  const cache = await openCache();
  const cached = cache
    ? await cache.match(MODEL_URL).catch(() => undefined)
    : undefined;
  const headers: Record<string, string> = {};
  const etag = cached?.headers.get("ETag");
  if (etag) headers["If-None-Match"] = etag;
  let res: Response;
  try {
    // `no-store` : le Cache Storage garde le modèle, pas une seconde copie dans le cache HTTP.
    res = await fetch(MODEL_URL, {
      headers,
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch (e) {
    if (cached) return cached.arrayBuffer();
    throw e;
  }
  if (res.status === 304 && cached) return cached.arrayBuffer();
  if (!res.ok)
    throw new Error(
      res.status === 404
        ? "modèle de profondeur absent du serveur"
        : `modèle de profondeur indisponible (${res.status})`,
    );
  const body = await readAll(res, onProgress);
  if (cache)
    await cache
      .put(
        MODEL_URL,
        new Response(body.slice(0), {
          headers: {
            "Content-Type": "application/octet-stream",
            ETag: res.headers.get("ETag") ?? "",
          },
        }),
      )
      .catch(() => undefined);
  return body;
}

interface Loaded {
  ort: typeof Ort;
  session: Ort.InferenceSession;
  device: "webgpu" | "wasm";
}

let loading: Promise<Loaded> | null = null;

async function load(onProgress: (p: DepthProgress) => void): Promise<Loaded> {
  onProgress({
    label: "Chargement de la bibliothèque de calcul",
    fraction: null,
  });
  const ort = (await import(/* @vite-ignore */ ORT_MODULE)) as typeof Ort;
  ort.env.wasm.wasmPaths = ORT_DIR;
  const bytes = new Uint8Array(await modelBytes(onProgress));
  onProgress({ label: "Préparation du modèle", fraction: null });
  const gpu = typeof navigator !== "undefined" && "gpu" in navigator;
  if (gpu) {
    try {
      const session = await ort.InferenceSession.create(bytes, {
        executionProviders: ["webgpu"],
      });
      return { ort, session, device: "webgpu" };
    } catch {
      // WebGPU présent mais refusé (pilote, mémoire) : WASM.
    }
  }
  const session = await ort.InferenceSession.create(bytes, {
    executionProviders: ["wasm"],
  });
  return { ort, session, device: "wasm" };
}

function loaded(onProgress: (p: DepthProgress) => void): Promise<Loaded> {
  if (!loading)
    loading = load(onProgress).catch((e: unknown) => {
      loading = null; // « Réessayer » recommence depuis le début
      throw e;
    });
  return loading;
}

async function pixels(
  photo: Blob,
): Promise<{ data: Uint8ClampedArray; width: number; height: number }> {
  const bitmap = await createImageBitmap(photo, {
    imageOrientation: "from-image",
  });
  try {
    const { width, height } = bitmap;
    const canvas =
      typeof OffscreenCanvas !== "undefined"
        ? new OffscreenCanvas(width, height)
        : Object.assign(document.createElement("canvas"), { width, height });
    const ctx = canvas.getContext("2d") as
      CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null;
    if (!ctx)
      throw new Error("lecture de la photo impossible dans ce navigateur");
    ctx.drawImage(bitmap, 0, 0);
    return { data: ctx.getImageData(0, 0, width, height).data, width, height };
  } finally {
    bitmap.close();
  }
}

/** Profondeur de `photo`, qui doit faire `width` × `height` (la taille connue du serveur). */
export async function computeDepth(
  photo: Blob,
  width: number,
  height: number,
  onProgress: (p: DepthProgress) => void,
): Promise<BrowserDepthResult> {
  const fake = stub();
  if (fake) {
    const size = gridSize(width, height);
    onProgress({ label: "Calcul de la profondeur", fraction: null });
    const data = fake(width, height, size.width, size.height);
    return {
      grid: toHalfArray(data),
      gridWidth: size.width,
      gridHeight: size.height,
      width,
      height,
      device: "simulé",
    };
  }
  const { ort, session, device } = await loaded(onProgress);
  onProgress({ label: "Lecture de la photo", fraction: null });
  const image = await pixels(photo);
  if (image.width !== width || image.height !== height)
    throw new PhotoSizeMismatch(
      `photo de ${image.width} × ${image.height}, ${width} × ${height} attendue`,
    );
  const input = preprocess(image.data, width, height);
  onProgress({ label: "Calcul de la profondeur", fraction: null });
  const tensor = new ort.Tensor("float32", input.data, [
    1,
    3,
    input.height,
    input.width,
  ]);
  const out = await session.run({ pixel_values: tensor });
  const depth = out.predicted_depth;
  if (!depth) throw new Error("sortie du modèle de profondeur inattendue");
  const dims = depth.dims;
  const dh = dims[dims.length - 2] ?? 0;
  const dw = dims[dims.length - 1] ?? 0;
  const values = (await depth.getData()) as Float32Array;
  tensor.dispose();
  depth.dispose();
  const grid = depthGrid(values, dw, dh, width, height);
  return {
    grid: toHalfArray(grid.data),
    gridWidth: grid.width,
    gridHeight: grid.height,
    width,
    height,
    device,
  };
}
