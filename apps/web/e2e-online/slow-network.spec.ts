import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page, type WebSocketRoute } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** Connexion lente (MEP 14, 25 oct.) : augus, sur un réseau lent, restait bloqué sur « Analyse de
 *  la photo… Envoi de l'analyse », sur « Reconnexion… », et voyait le texte brut d'une miniature
 *  de rendu au lieu de l'image. Ici, réseau bridé par CDP (256 kbit/s montant), envoi qui ne
 *  progresse plus, WebSocket qui ne répond plus (la miniature en échec : renders-gallery.spec.ts,
 *  étape 7 bis, avec le compte des rendus déjà connecté). Profondeur SIMULÉE comme dans
 *  photo-depth.spec.ts ; délais raccourcis par `window.__planDesignNet` (absent en production).
 *  Chaque personne a sa propre X-Forwarded-For (198.51.100.241 à 243). */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const WEB_MODEL = path.resolve(
  ".e2e-online/weights/depth_anything_v2_metric_indoor_small_web_1.0",
);
const UNSTABLE =
  "Connexion instable : vos modifications sont enregistrées ; la mise à jour en direct reprendra dès que le réseau le permet.";

test.beforeAll(() => {
  fs.mkdirSync(WEB_MODEL, { recursive: true });
  fs.writeFileSync(path.join(WEB_MODEL, "model_fp16.onnx"), "simulé");
});

test.afterAll(() => {
  fs.rmSync(WEB_MODEL, { recursive: true, force: true });
});

/** Profondeur exacte de la pièce de la fixture (comme photo-depth.spec.ts), et le nombre
 *  d'appels (une analyse faite n'est jamais refaite). */
function boxDepth(
  width: number,
  height: number,
  gw: number,
  gh: number,
): Float32Array {
  const w = window as unknown as { __depthCalls?: number };
  w.__depthCalls = (w.__depthCalls ?? 0) + 1;
  const [px, py, pz] = [0.7, 0.6, 1.4];
  const yaw = 0.8727;
  const pitch = -0.2094;
  const f = 900;
  const forward = [
    Math.cos(yaw) * Math.cos(pitch),
    Math.sin(yaw) * Math.cos(pitch),
    Math.sin(pitch),
  ];
  const right = [Math.sin(yaw), -Math.cos(yaw), 0];
  const up = [
    -Math.cos(yaw) * Math.sin(pitch),
    -Math.sin(yaw) * Math.sin(pitch),
    Math.cos(pitch),
  ];
  const [W, D, H] = [4 - 0.15, 3 - 0.15, 2.5];
  const faces: [number[], number][] = [
    [[0, 0, -1], 0],
    [[0, 0, 1], H],
    [[-1, 0, 0], 0],
    [[1, 0, 0], W],
    [[0, -1, 0], 0],
    [[0, 1, 0], D],
  ];
  const step = Math.max(1, Math.ceil(Math.max(width, height) / 640));
  const out = new Float32Array(gw * gh);
  for (let j = 0; j < gh; j++)
    for (let i = 0; i < gw; i++) {
      const dx = (i * step - width / 2) / f;
      const dy = (j * step - height / 2) / f;
      const dir = [0, 1, 2].map(
        (k) => dx * right[k]! - dy * up[k]! + forward[k]!,
      );
      let t = Infinity;
      for (const [n, off] of faces) {
        const den = n[0]! * dir[0]! + n[1]! * dir[1]! + n[2]! * dir[2]!;
        if (den <= 1e-12) continue;
        t = Math.min(t, (off - (n[0]! * px + n[1]! * py + n[2]! * pz)) / den);
      }
      out[j * gw + i] = t;
    }
  return out;
}

async function signIn(page: Page, email: string, ip: string): Promise<void> {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": ip });
  await gotoApp(page, "/");
  const lastCode = async () => {
    const r = await page.request.get(
      `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
    );
    return r.ok() ? ((await r.json()) as { code: string }).code : "";
  };
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  // Le compte des rendus a pu demander un code juste avant (autre spec) : le serveur peut
  // renvoyer le même tant qu'il est valable ; on prend le code courant.
  let code = "";
  await expect
    .poll(async () => {
      code = await lastCode();
      return /^\d{6}$/.test(code);
    })
    .toBe(true);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

async function newProject(page: Page, name: string): Promise<string> {
  const created = await page.request.post("/api/projects", {
    data: { name },
    headers: { Origin: new URL(page.url()).origin },
  });
  expect(created.status()).toBe(201);
  return ((await created.json()) as { id: string }).id;
}

async function importPhoto(page: Page): Promise<void> {
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await expect(photos).toBeVisible({ timeout: 30_000 });
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer", exact: true }).click();
  await (await chooser).setFiles(PHOTO);
}

async function wallsOf(page: Page, id: string): Promise<number> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, { type: string }> }>;
  };
  const level = Object.values(project.levels)[0]!;
  return Object.values(level.elements).filter((e) => e.type === "wall").length;
}

test("réseau bridé : l'envoi de l'analyse progresse, compressé, et le plan se construit", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await page.addInitScript(
    `window.__planDesignDepthStub = (${boxDepth.toString()});`,
  );
  await signIn(page, "lent@example.org", "198.51.100.241");
  const id = await newProject(page, "Réseau bridé");
  await gotoApp(page, `/#/projects/${id}/board`);
  // Ce qui part : la photo (envoi multipart) puis la carte de profondeur.
  const sent: { path: string; bytes: number; encoding?: string }[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() === "POST" && p.endsWith("/images"))
      sent.push({ path: "photo", bytes: r.postDataBuffer()?.length ?? 0 });
    if (r.method() === "PUT" && p.endsWith("/depth"))
      sent.push({
        path: "profondeur",
        bytes: r.postDataBuffer()?.length ?? 0,
        encoding: r.headers()["content-encoding"],
      });
  });
  // 256 kbit/s montant, 1 Mbit/s descendant, 300 ms de latence.
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 300,
    uploadThroughput: 256_000 / 8,
    downloadThroughput: 1_000_000 / 8,
  });
  await importPhoto(page);
  const status = page.getByRole("status", { name: "Analyse de la photo" });
  // La progression réelle de l'envoi, en pourcentage (et plus un sablier sans fin).
  await expect(status).toContainText(/Envoi de l'analyse \(\d+ %\)/, {
    timeout: 120_000,
  });
  await expect(status).toHaveCount(0, { timeout: 120_000 });
  await cdp.send("Network.emulateNetworkConditions", {
    offline: false,
    latency: 0,
    uploadThroughput: -1,
    downloadThroughput: -1,
  });
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({ timeout: 90_000 });
  expect(await wallsOf(page, id)).toBeGreaterThanOrEqual(3);
  const depth = sent.filter((s) => s.path === "profondeur");
  expect(depth).toHaveLength(1);
  // Le corps d'un envoi XHR n'est pas lisible ici : la taille a été mesurée à part
  // (427 Ko bruts, 170 à 263 Ko en gzip sur les photos du banc).
  expect(depth[0]!.encoding).toBe("gzip");
  expect(
    await page.evaluate(
      () => (window as unknown as { __depthCalls?: number }).__depthCalls,
    ),
  ).toBe(1);
  // Une seule photo envoyée.
  expect(sent.filter((s) => s.path === "photo")).toHaveLength(1);
});

test("envoi qui ne progresse plus : message clair, « Réessayer » renvoie sans recalculer", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.addInitScript(
    `window.__planDesignDepthStub = (${boxDepth.toString()});
     window.__planDesignNet = { stallMs: 2000 };`,
  );
  let sends = 0;
  await page.route(/\/images\/[0-9a-f-]+\/depth\?/, async (route) => {
    sends += 1;
    // Premier envoi : rien ne revient jamais (réseau qui ne passe plus).
    if (sends === 1) return new Promise<void>(() => undefined);
    await route.continue();
  });
  await signIn(page, "lent-reessai@example.org", "198.51.100.242");
  const id = await newProject(page, "Envoi bloqué");
  await gotoApp(page, `/#/projects/${id}/board`);
  await importPhoto(page);
  const failed = page.getByRole("alert", { name: "Analyse de la photo" });
  await expect(failed).toContainText("L'envoi de l'analyse n'a pas abouti", {
    timeout: 60_000,
  });
  await expect(failed).toContainText("la connexion est trop lente ou coupée");
  await expect(failed).toContainText("la renvoie sans la refaire");
  await failed.getByRole("button", { name: "Réessayer", exact: true }).click();
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({ timeout: 90_000 });
  expect(sends).toBe(2);
  expect(
    await page.evaluate(
      () => (window as unknown as { __depthCalls?: number }).__depthCalls,
    ),
  ).toBe(1);
});

test("WebSocket muet : reconnexion bornée, « Connexion instable » et « Recharger », rien de perdu ni de doublé", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.addInitScript(
    `window.__planDesignNet = { helloMs: 800, unstableAfter: 2, pingMs: 400, silenceMs: 1500 };`,
  );
  let mute = false;
  const routes: WebSocketRoute[] = [];
  let attempts = 0;
  await page.routeWebSocket(/\/ws\/projects\//, (ws) => {
    attempts += 1;
    routes.push(ws);
    // Muet : la poignée de main aboutit mais aucun message ne revient jamais.
    if (!mute) ws.connectToServer();
  });
  await signIn(page, "lent-ws@example.org", "198.51.100.243");
  const id = await newProject(page, "Direct instable");
  await gotoApp(page, `/#/projects/${id}/board`);
  const region = page.getByRole("region", { name: "Espace de travail" });
  await expect(region.locator(".board-item.kind-frame").first()).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByText("Reconnexion…")).toHaveCount(0);

  // Coupure : la connexion tombe, les suivantes restent muettes.
  mute = true;
  const before = attempts;
  for (const ws of routes) await ws.close();
  const banner = page
    .getByRole("status")
    .filter({ hasText: "Connexion instable" });
  await expect(banner).toHaveText(new RegExp(`${UNSTABLE}\\s*Recharger`), {
    timeout: 30_000,
  });
  expect(attempts - before).toBeGreaterThanOrEqual(2);
  await expect(
    banner.getByRole("button", { name: "Recharger", exact: true }),
  ).toBeVisible();

  // Une modification pendant la coupure : enregistrée (en REST), une seule fois.
  for (let i = 0; i < 3; i++) await page.keyboard.press("-");
  const box = (await region.boundingBox())!;
  await page.keyboard.press("n");
  await page.mouse.click(box.x + box.width - 200, box.y + box.height * 0.35);
  await region
    .getByRole("textbox", { name: "Texte du post-it" })
    .fill("Pendant la coupure");
  await page.mouse.click(box.x + box.width - 60, box.y + 40);
  await expect(page.locator(".save-dot.saved")).toBeVisible({
    timeout: 20_000,
  });

  // Le réseau revient : la mise à jour en direct reprend, la bannière s'en va.
  mute = false;
  await expect(banner).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByText("Reconnexion…")).toHaveCount(0, {
    timeout: 30_000,
  });
  const board = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: { kind: string; text: string }[] };
  expect(
    board.items.filter(
      (i) => i.kind === "note" && i.text === "Pendant la coupure",
    ),
  ).toHaveLength(1);
  await expect(
    region.locator(".board-item.kind-note", { hasText: "Pendant la coupure" }),
  ).toHaveCount(1);
});
