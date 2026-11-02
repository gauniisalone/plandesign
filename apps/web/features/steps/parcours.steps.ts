import fs from "node:fs";
import path from "node:path";
import { expect, request as playwrightRequest, type Page } from "@playwright/test";
import { gotoApp } from "../../e2e/app-ready";
import { showPanel } from "../../e2e/helpers";
import { E2E_WORKER_TOKEN, RENDER_ACCOUNT } from "../../e2e-online/render-account";
import { origin, signIn } from "./comptes";
import { Given, Then, When, type Ctx } from "./fixtures";

/** KAN-103, pilote (a) : le parcours court en ligne, du projet neuf au rendu pris par le poste
 *  de rendu simulé (appels HTTP du poste avec son jeton, comme realistic.spec). */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");

const board = (page: Page) => page.getByRole("region", { name: "Espace de travail" });
const renders = (page: Page) => board(page).getByRole("region", { name: "Rendus réalistes" });

function project(ctx: Ctx): { id: string; level: string } {
  if (!ctx.project) throw new Error("aucun projet : un pas « je crée le projet » manque");
  return ctx.project;
}

Given("je suis connecté avec le compte autorisé aux rendus", async ({ page, ctx }) => {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.211" });
  const me = { page, email: RENDER_ACCOUNT.email, ip: "198.51.100.211" };
  ctx.people.set("moi", me);
  await signIn(me, "Pilote");
});

When("je crée le projet {string}", async ({ page, ctx }, name: string) => {
  const made = await page.request.post("/api/projects", { data: { name }, headers: origin(page) });
  expect(made.status()).toBe(201);
  const body = (await made.json()) as { id: string; levels: Record<string, unknown> };
  ctx.project = { id: body.id, level: Object.keys(body.levels)[0]! };
  await gotoApp(page, `/#/projects/${body.id}/board`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
});

When("j'importe une photo de la pièce", async ({ page }) => {
  const photos = board(page).locator(".board-item.kind-frame.role-photos");
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (await chooser).setFiles(PHOTO);
});

Then("la photo est rangée dans les photos de la pièce", async ({ page }) => {
  await expect(board(page).locator(".board-item.kind-photo")).toHaveCount(1, { timeout: 30_000 });
});

When("je décris le projet : {string}", async ({ page, ctx }, text: string) => {
  await showPanel(page); // fermé par défaut (T7d1)
  // La photo importée reste sélectionnée : le panneau montre sa sélection, on revient à
  // l'Assistant comme on le ferait.
  const back = page.getByRole("button", { name: "← Assistant" });
  if (await back.isVisible()) await back.click();
  const field = page.getByLabel("Vos envies et contraintes");
  await field.fill(text);
  await field.blur();
  await expect
    .poll(async () => {
      const r = await page.request.get(`/api/projects/${project(ctx).id}`);
      return ((await r.json()) as { pending_instruction: string | null }).pending_instruction ?? "";
    })
    .toContain(text);
});

When("je génère le plan", async ({ page }) => {
  await page.getByRole("group", { name: "Déclencheur" }).getByRole("button", { name: "Générer le plan" }).click();
});

Then("le plan apparaît dans l'espace de travail", async ({ page }) => {
  await expect(board(page).locator(".board-item.kind-plan img.board-plan")).toBeVisible({
    timeout: 60_000,
  });
});

When("j'ajoute au plan le point de vue {string}", async ({ page, ctx }, name: string) => {
  const { id, level } = project(ctx);
  const camera = await page.request.post(`/api/projects/${id}/levels/${level}/ops`, {
    data: { op: "add_camera", position: { x: 1, y: 1 }, yaw: 0.6, name },
    headers: origin(page),
  });
  expect(camera.ok()).toBe(true);
  // Posé par l'API (comme realistic.spec) : l'espace de travail relu le montre.
  await gotoApp(page, `/#/projects/${id}/board`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
});

When("je demande le rendu de la vue {string}", async ({ page }, name: string) => {
  const menu = renders(page).getByRole("button", { name: "Rendre ▾" });
  await expect(menu).toBeEnabled({ timeout: 30_000 });
  await menu.click();
  await renders(page).getByRole("menuitem", { name: `Rendre la vue « ${name} »` }).click();
});

Then("le rendu attend le poste de rendu", async ({ page }) => {
  await expect(
    renders(page).getByText("Poste de rendu hors ligne : le rendu sera fait à son retour"),
  ).toBeVisible();
});

When("le poste de rendu prend la demande", async ({ ctx, baseURL }) => {
  // Le poste : ni cookie ni origine, son jeton seulement, sa propre adresse.
  const api = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: { Authorization: `Bearer ${E2E_WORKER_TOKEN}`, "X-Forwarded-For": "198.51.100.212" },
  });
  const claim = await api.post("/api/worker/claim", { data: { state: "idle" } });
  expect(claim.status()).toBe(200);
  const job = (await claim.json()) as { request_id: string; lease: string };
  ctx.worker = { api, requestId: job.request_id, lease: job.lease };
  for (const name of ["project", "bake", "camera"]) {
    const file = await api.get(`/api/worker/jobs/${job.request_id}/files/${name}`, {
      headers: { "X-Render-Lease": job.lease },
    });
    expect(file.status(), name).toBe(200);
  }
});

Then("le rendu est en cours", async ({ page }) => {
  await expect(renders(page).getByText("Rendu en cours", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
});

When("le poste de rendu dépose son image", async ({ ctx }) => {
  const worker = ctx.worker;
  if (!worker) throw new Error("le poste n'a pris aucune demande");
  const png = fs.readFileSync(PHOTO);
  const done = await worker.api.post(`/api/worker/jobs/${worker.requestId}/result`, {
    headers: { "X-Render-Lease": worker.lease },
    multipart: {
      meta: JSON.stringify({
        seconds: 41.6,
        vram_peak_mib: 4800,
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
        engine: "fake",
      }),
      image: { name: "render.png", mimeType: "image/png", buffer: png },
    },
  });
  expect(done.status()).toBe(200);
});

Then("l'image du rendu s'affiche", async ({ page }) => {
  const image = renders(page).locator("img.rgal-img");
  await expect(image).toBeVisible({ timeout: 15_000 });
  await expect.poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth)).toBeGreaterThan(0);
});

// Le chemin avec poids (plan-photo.feature, e2e de base) : la photo déposée sur « Ajouter une
// source » construit le plan, comme slice.spec.
When("je dépose une photo de la pièce comme source du plan", async ({ page, ctx }) => {
  // Comme slice.spec : depuis l'accueil, droit sur « Ajouter une source ».
  await gotoApp(page, "/");
  await expect(page.getByRole("heading", { name: "Vos espaces de travail" })).toBeVisible();
  await gotoApp(page, `/#/projects/${project(ctx).id}/import`);
  await expect(page).toHaveURL(/\/import$/);
  await expect(page.getByRole("heading", { name: "Ajouter une source" })).toBeVisible();
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: /^Déposer une photo ou un plan/ }).click();
  await (await chooser).setFiles(PHOTO);
});

Then("le plan se construit depuis la photo, avec ses quatre murs", async ({ page }) => {
  await expect(page.locator("polygon.wall")).toHaveCount(4, { timeout: 180_000 });
});

Then("le plan est annoncé comme estimé", async ({ page }) => {
  await expect(page.locator(".auto-banner.estimated")).toContainText("Plan estimé");
});
