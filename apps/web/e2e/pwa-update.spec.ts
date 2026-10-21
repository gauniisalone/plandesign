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
 * Mise à jour du front installé (PWA) : le serveur publie une nouvelle version pendant qu'un
 * onglet tourne sur l'ancienne. Un seul rechargement doit montrer la nouvelle ; une mise à
 * jour qui arrive en cours de travail affiche le bandeau « Nouvelle version disponible ».
 * Le service worker n'existe que dans le front construit : on construit deux versions (A et B)
 * et on les sert tour à tour sur un petit serveur statique.
 */

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
let current = "";
let server: Server;
let base = "";
const builds: Record<string, string> = {};

function build(id: string): string {
  const out = join(root, id);
  execSync(`npx vite build --outDir ${out} --emptyOutDir --logLevel error`, {
    env: { ...process.env, VITE_OPENPLAN_BUILD: id },
    stdio: "pipe",
  });
  return out;
}

test.beforeAll(async () => {
  test.setTimeout(240_000);
  root = mkdtempSync(join(tmpdir(), "openplan-pwa-"));
  builds.A = build("A");
  builds.B = build("B");
  current = builds.A;
  server = createServer((req, res) => {
    const path = decodeURIComponent((req.url ?? "/").split("?")[0]!);
    if (path.startsWith("/api/")) {
      res.writeHead(404).end();
      return;
    }
    let file = join(current, path);
    if (!existsSync(file) || statSync(file).isDirectory())
      file = join(current, "index.html");
    res.writeHead(200, {
      "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
      "Cache-Control": "no-cache",
    });
    createReadStream(file).pipe(res);
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  const address = server.address();
  base = `http://127.0.0.1:${typeof address === "object" && address ? address.port : 0}`;
});

test.afterAll(() => {
  server?.close();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function buildId(page: Page): Promise<string | undefined> {
  // Pendant un rechargement, le contexte de la page disparaît : on relira au tour suivant.
  return page
    .evaluate(() => document.documentElement.dataset.build)
    .catch(() => undefined);
}

async function controlled(page: Page): Promise<void> {
  await page.waitForFunction(
    () => navigator.serviceWorker?.controller != null,
    null,
    {
      timeout: 30_000,
    },
  );
}

test("une nouvelle version publiée s'affiche au premier rechargement", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  current = builds.A!;
  await page.goto(base + "/");
  await controlled(page);
  expect(await buildId(page)).toBe("A");
  current = builds.B!;
  await page.reload();
  await expect.poll(() => buildId(page), { timeout: 30_000 }).toBe("B");
  await context.close();
});

test("KAN-80 : un clic dans les premières secondes n'est jamais perdu dans un rechargement", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  current = builds.A!;
  await page.goto(base + "/");
  await controlled(page);
  expect(await buildId(page)).toBe("A");
  // Une mise en ligne (B) : la page rechargée la trouve dans ses premières secondes, mais la
  // personne clique aussitôt (comme sur « Nouveau projet » juste après s'être connectée).
  current = builds.B!;
  await page.reload();
  let loads = 0;
  page.on("load", () => {
    loads += 1;
  });
  await page.mouse.click(5, 5);
  const banner = page
    .getByRole("status")
    .filter({ hasText: "Nouvelle version disponible" });
  await expect(banner).toBeVisible({ timeout: 30_000 });
  expect(loads, "aucun rechargement d'office après le clic").toBe(0);
  expect(await buildId(page)).toBe("A");
  await banner.getByRole("button", { name: "Recharger" }).click();
  await expect.poll(() => buildId(page), { timeout: 30_000 }).toBe("B");
  await context.close();
});

test("une mise à jour en cours de travail propose de recharger", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  current = builds.A!;
  await page.goto(base + "/");
  // « En cours de travail » dès le chargement : pas d'application automatique.
  await page.evaluate(() =>
    window.localStorage.setItem("openplan:pwa-young-ms", "0"),
  );
  await page.reload();
  await controlled(page);
  expect(await buildId(page)).toBe("A");
  current = builds.B!;
  await page.evaluate(async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    await registration?.update();
  });
  const banner = page
    .getByRole("status")
    .filter({ hasText: "Nouvelle version disponible" });
  await expect(banner).toBeVisible({ timeout: 30_000 });
  expect(await buildId(page)).toBe("A");
  await banner.getByRole("button", { name: "Recharger" }).click();
  await expect.poll(() => buildId(page), { timeout: 30_000 }).toBe("B");
  await context.close();
});
