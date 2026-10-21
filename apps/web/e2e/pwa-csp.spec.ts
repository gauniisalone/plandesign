import { test, expect, type Page } from "@playwright/test";
import { execSync } from "node:child_process";
import {
  createReadStream,
  existsSync,
  mkdtempSync,
  rmSync,
  statSync,
} from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { extname, join } from "node:path";

/**
 * Correctif de KAN-79, suite : les en-têtes de la page (dont la CSP) viennent du serveur, pas
 * du service worker. Après la MEP 10 (CSP corrigée côté serveur, front inchangé), le service
 * worker servait encore l'index.html précaché à la MEP 9, avec l'ancienne CSP : WebAssembly
 * refusé même après un rechargement normal. Un seul front construit, servi d'abord avec la CSP
 * sans 'wasm-unsafe-eval', puis avec : un rechargement normal doit suffire.
 */

const OLD_CSP =
  "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; " +
  "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src 'self' blob:";
const NEW_CSP = OLD_CSP.replace(
  "script-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
);

const TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".webmanifest": "application/manifest+json",
  ".json": "application/json",
};

let root = "";
let built = "";
let csp = OLD_CSP;
let server: Server;
let base = "";

test.beforeAll(async () => {
  test.setTimeout(240_000);
  root = mkdtempSync(join(tmpdir(), "openplan-pwa-csp-"));
  built = join(root, "front");
  execSync(`npx vite build --outDir ${built} --emptyOutDir --logLevel error`, {
    env: { ...process.env, VITE_OPENPLAN_BUILD: "csp" },
    stdio: "pipe",
  });
  server = createServer((req, res) => {
    if (down) {
      req.socket.destroy(); // serveur coupé : aucune réponse
      return;
    }
    const path = decodeURIComponent((req.url ?? "/").split("?")[0]!);
    if (path.startsWith("/api/")) {
      res.writeHead(404).end();
      return;
    }
    let file = join(built, path);
    if (!existsSync(file) || statSync(file).isDirectory())
      file = join(built, "index.html");
    res.writeHead(200, {
      "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
      "Cache-Control": "no-cache",
      "Content-Security-Policy": csp,
    });
    createReadStream(file).pipe(res);
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  base = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
});

let down = false;

test.afterAll(() => {
  server?.close();
  if (root) rmSync(root, { recursive: true, force: true });
});

/** La page peut-elle compiler du WebAssembly (module vide) sous sa CSP ? */
async function wasmAllowed(page: Page): Promise<boolean> {
  return page.evaluate(async () => {
    try {
      await WebAssembly.compile(
        new Uint8Array([0x00, 0x61, 0x73, 0x6d, 0x01, 0x00, 0x00, 0x00]),
      );
      return true;
    } catch {
      return false;
    }
  });
}

test("correctif KAN-79 : une CSP changée sur le serveur s'applique au premier rechargement", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  csp = OLD_CSP;
  await page.goto(base + "/");
  await page.waitForFunction(
    () => navigator.serviceWorker?.controller != null,
    null,
    {
      timeout: 30_000,
    },
  );
  expect(await wasmAllowed(page)).toBe(false);
  // MEP du serveur seul : même front, nouvelle CSP. Un rechargement normal (pas Ctrl+Maj+R).
  csp = NEW_CSP;
  await page.reload();
  await expect.poll(() => wasmAllowed(page), { timeout: 10_000 }).toBe(true);
  await context.close();
});

test("correctif KAN-79 : hors ligne, la page s'ouvre depuis le cache des pages", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  csp = NEW_CSP;
  down = false;
  await page.goto(base + "/");
  await page.waitForFunction(
    () => navigator.serviceWorker?.controller != null,
    null,
    { timeout: 30_000 },
  );
  // Une visite sous le service worker garde la page (réseau d'abord, puis cache des pages).
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => document.documentElement.dataset.build))
    .toBe("csp");
  down = true; // serveur coupé
  try {
    await page.reload();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dataset.build), {
        timeout: 15_000,
      })
      .toBe("csp");
    await expect(page.locator("#root")).not.toBeEmpty();
  } finally {
    down = false;
    await context.close();
  }
});
