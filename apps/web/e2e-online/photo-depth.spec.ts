import fs from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-79 : en ligne (comptes, serveur sans GPU ni poids du serveur), la profondeur d'une
 *  photo se calcule dans le navigateur. Ici SIMULÉE (profondeur exacte de la pièce de la
 *  fixture, sans modèle ni bibliothèque) : on éprouve le parcours, pas le modèle. « Analyse de
 *  la photo… », envoi au serveur (float16), puis le plan se construit sans modèle sur le
 *  serveur. Un envoi refusé dit pourquoi, propose « Réessayer », et l'espace de travail reste
 *  utilisable. Aucune requête ailleurs que vers PlanDesign. */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
// Le serveur des e2e en ligne lit ses poids ici (playwright.online.config.ts) : un faux
// modèle du navigateur suffit pour que /api/health l'annonce (il n'est jamais téléchargé).
const WEB_MODEL = path.resolve(
  ".e2e-online/weights/depth_anything_v2_metric_indoor_small_web_1.0",
);

test.beforeAll(() => {
  fs.mkdirSync(WEB_MODEL, { recursive: true });
  fs.writeFileSync(path.join(WEB_MODEL, "model_fp16.onnx"), "simulé");
});

test.afterAll(() => {
  fs.rmSync(WEB_MODEL, { recursive: true, force: true });
});

/** Profondeur exacte de la pièce 4 × 3 m de la fixture (tests/synthetic_depth.py, BoxDepth),
 *  aux pixels gardés (un sur `step`). */
function boxDepth(
  width: number,
  height: number,
  gw: number,
  gh: number,
): Float32Array {
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

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

test("en ligne : la photo est analysée dans le navigateur, puis le plan se construit", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.179" });
  await page.addInitScript(
    `window.__planDesignDepthStub = (${boxDepth.toString()});`,
  );
  const origins = new Set<string>();
  const failures: string[] = [];
  page.on("request", (r) => origins.add(new URL(r.url()).origin));
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  // Premier envoi refusé (comme un 422 du serveur), le second passe, après une pause qui
  // laisse voir « Analyse de la photo… ».
  let sends = 0;
  const bodies: number[] = [];
  await page.route(/\/images\/[0-9a-f-]+\/depth\?/, async (route) => {
    sends += 1;
    bodies.push(route.request().postDataBuffer()?.length ?? 0);
    if (sends === 1) {
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "profondeur refusée : valeurs non finies",
        }),
      });
      return;
    }
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });

  await signIn(page, "kan79@example.org");
  const origin = { Origin: new URL(page.url()).origin };
  const health = (await (await page.request.get("/api/health")).json()) as {
    weights: string[];
  };
  expect(health.weights).toContain(
    "depth_anything_v2_metric_indoor_small_web@1.0",
  );
  const a = (await (
    await page.request.post("/api/projects", {
      data: { name: "Pièce photo" },
      headers: origin,
    })
  ).json()) as { id: string };
  await gotoApp(page, `/#/projects/${a.id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  // Le plan depuis une photo n'est plus « bientôt en ligne ».
  await expect(board.locator(".board-item.kind-frame.role-photos")).toBeVisible(
    {
      timeout: 30_000,
    },
  );
  await expect(
    board.getByText("Plan depuis photo : bientôt en ligne"),
  ).toHaveCount(0);

  // Dépôt d'une photo : analyse dans le navigateur, envoi refusé, « Réessayer ».
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (await chooser).setFiles(PHOTO);
  const failed = page.getByRole("alert", { name: "Analyse de la photo" });
  await expect(failed).toContainText("valeurs non finies", { timeout: 30_000 });
  // L'espace de travail reste utilisable pendant ce temps (rien n'est grisé), en ton neutre.
  await expect(
    board.getByRole("button", { name: "Tracer moi-même", exact: true }),
  ).toBeEnabled();
  await expect(failed).toContainText("vous pouvez continuer à travailler");
  await failed.getByRole("button", { name: "Réessayer", exact: true }).click();
  const status = page.getByRole("status", { name: "Analyse de la photo" });
  await expect(status).toContainText("Analyse de la photo…");
  await expect(status).toHaveCount(0, { timeout: 30_000 });

  // Le plan est construit par le serveur, sans modèle, depuis la profondeur envoyée.
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({
    timeout: 90_000,
  });
  const project = (await (
    await page.request.get(`/api/projects/${a.id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, { type: string }> }>;
  };
  const level = Object.values(project.levels)[0]!;
  const walls = Object.values(level.elements).filter((e) => e.type === "wall");
  expect(walls.length).toBeGreaterThanOrEqual(3);
  // 640 × 480 valeurs float16 pour la photo de 1280 × 960.
  expect(bodies).toEqual([640 * 480 * 2, 640 * 480 * 2]);
  expect([...origins]).toEqual([new URL(page.url()).origin]);
  expect(failures).toEqual([]);
});

/** Correctif de KAN-79 : quand la bibliothèque de calcul ne démarre pas dans la page (en
 *  production : WebAssembly refusé par une ancienne CSP), une nouvelle tentative au même endroit
 *  échouerait encore (onnxruntime-web garde l'échec). Le bandeau propose alors « Recharger la
 *  page », pas « Réessayer ». Ici, la bibliothèque est refusée (404), sans profondeur simulée. */
test("correctif KAN-79 : bibliothèque non démarrée → « Recharger la page », pas « Réessayer »", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.182" });
  await page.route("**/ort/ort.webgpu.min.mjs", (route) =>
    route.fulfill({ status: 404, body: "" }),
  );
  await signIn(page, "kan79-recharger@example.org");
  const origin = { Origin: new URL(page.url()).origin };
  const a = (await (
    await page.request.post("/api/projects", {
      data: { name: "Bibliothèque absente" },
      headers: origin,
    })
  ).json()) as { id: string };
  await gotoApp(page, `/#/projects/${a.id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await expect(photos).toBeVisible({ timeout: 30_000 });
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (await chooser).setFiles(PHOTO);
  const failed = page.getByRole("alert", { name: "Analyse de la photo" });
  await expect(failed).toContainText("rechargez la page", { timeout: 30_000 });
  await expect(
    failed.getByRole("button", { name: "Recharger la page", exact: true }),
  ).toBeVisible();
  await expect(
    failed.getByRole("button", { name: "Réessayer", exact: true }),
  ).toHaveCount(0);
});

/** KAN-81 (signalé en production) : deux photos du grenier importées, « Générer le plan »
 *  disait « décrivez la pièce dans le compositeur ». Avec la profondeur du navigateur, le
 *  plan se construit depuis les photos. */
test("KAN-81 : deux photos importées, « Générer le plan » construit le plan", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.181" });
  await page.addInitScript(
    `window.__planDesignDepthStub = (${boxDepth.toString()});`,
  );
  const failures: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && (r.status() >= 500 || r.status() === 422))
      failures.push(`${r.status()} ${p}`);
  });
  await signIn(page, "kan81@example.org");
  const origin = { Origin: new URL(page.url()).origin };
  const a = (await (
    await page.request.post("/api/projects", {
      data: { name: "Grenier" },
      headers: origin,
    })
  ).json()) as { id: string; render: Record<string, unknown> };
  // Comme chez augus : les photos sont rangées d'abord, le plan est demandé ensuite.
  const off = await page.request.patch(`/api/projects/${a.id}`, {
    data: { render: { ...a.render, auto_on_drop: false } },
    headers: origin,
  });
  expect(off.ok()).toBe(true);
  await gotoApp(page, `/#/projects/${a.id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await expect(photos).toBeVisible({ timeout: 30_000 });
  for (let i = 0; i < 2; i++) {
    const chooser = page.waitForEvent("filechooser");
    await photos
      .getByRole("button", { name: /^(Importer|Ajouter des photos)$/ })
      .first()
      .click();
    await (await chooser).setFiles(PHOTO);
    await expect(board.locator(".board-item.kind-photo")).toHaveCount(i + 1, {
      timeout: 30_000,
    });
  }
  await expect(
    board.getByText("Plan depuis photo : bientôt en ligne"),
  ).toHaveCount(0);
  // Le bouton du cadre Plan vide, celui qu'augus a cliqué.
  await board
    .locator(".board-item.kind-plan")
    .getByRole("button", { name: "Générer le plan", exact: true })
    .click();
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({
    timeout: 90_000,
  });
  const project = (await (
    await page.request.get(`/api/projects/${a.id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, { type: string }> }>;
  };
  const level = Object.values(project.levels)[0]!;
  expect(
    Object.values(level.elements).filter((e) => e.type === "wall").length,
  ).toBeGreaterThanOrEqual(3);
  await expect(
    page.getByText("décrivez la pièce dans le compositeur"),
  ).toHaveCount(0);
  expect(failures).toEqual([]);
});
