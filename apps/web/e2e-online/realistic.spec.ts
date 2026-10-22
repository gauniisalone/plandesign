import fs from "node:fs";
import path from "node:path";
import {
  expect,
  request as playwrightRequest,
  test,
  type Page,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { E2E_WORKER_TOKEN, RENDER_ACCOUNT } from "./render-account";

/** KAN-89 : en ligne (serveur sans Blender ni GPU), le compte autorisé demande un rendu
 *  réaliste depuis le cadre Rendus ; un poste SIMULÉ (appels HTTP du poste avec son jeton) le
 *  prend, lit le projet, le bake et la caméra, et dépose une image, qui s'affiche. Un autre
 *  compte n'a ni le bouton ni l'API. Chaque personne a sa propre X-Forwarded-For. */

const IMAGE = path.resolve("../../tests/fixtures/rect_room/image.png");

async function signIn(page: Page, email: string, ip: string): Promise<void> {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": ip });
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

/** Un espace avec une pièce de 4 × 3 m et un point de vue « Vue fenêtre ». */
async function projectWithView(
  page: Page,
): Promise<{ id: string; level: string }> {
  const origin = { Origin: new URL(page.url()).origin };
  const created = await page.request.post("/api/projects", {
    data: { name: "Chambre rendue" },
    headers: origin,
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(project.levels)[0]!;
  const ops = `/api/projects/${project.id}/levels/${level}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
  for (let i = 0; i < 4; i++) {
    const [a, b] = [corners[i]!, corners[(i + 1) % 4]!];
    const wall = await page.request.post(ops, {
      data: {
        op: "add_wall",
        start: { x: a[0], y: a[1] },
        end: { x: b[0], y: b[1] },
      },
      headers: origin,
    });
    expect(wall.ok()).toBe(true);
  }
  const camera = await page.request.post(ops, {
    data: {
      op: "add_camera",
      position: { x: 1, y: 1 },
      yaw: 0.6,
      name: "Vue fenêtre",
    },
    headers: origin,
  });
  expect(camera.ok()).toBe(true);
  return { id: project.id, level };
}

test("rendu réaliste : demande, poste simulé, image affichée", async ({
  page,
  baseURL,
}) => {
  test.setTimeout(120_000);
  const failures: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  await signIn(page, RENDER_ACCOUNT.email, "198.51.100.221");
  const { id } = await projectWithView(page);
  await gotoApp(page, `/#/projects/${id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const frame = board.getByRole("region", { name: "Rendus réalistes" });
  // KAN-91 : le menu « Rendre ▾ » liste les vues du plan.
  const menu = frame.getByRole("button", { name: "Rendre ▾" });
  await expect(menu).toBeEnabled({ timeout: 30_000 });
  await expect(
    board.getByText("Rendus réalistes : bientôt en ligne"),
  ).toHaveCount(0);

  // Demande : poste jamais vu, donc hors ligne ; la demande attend son retour.
  await menu.click();
  await frame
    .getByRole("menuitem", { name: /Rendre la vue « Vue fenêtre »/ })
    .click();
  await expect(
    frame.getByText(
      "Poste de rendu hors ligne : le rendu sera fait à son retour",
    ),
  ).toBeVisible();

  // Le poste simulé : ni cookie ni origine, son jeton seulement, sa propre adresse.
  const worker = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${E2E_WORKER_TOKEN}`,
      "X-Forwarded-For": "198.51.100.223",
    },
  });
  const claim = await worker.post("/api/worker/claim", {
    data: { state: "idle" },
  });
  expect(claim.status()).toBe(200);
  const job = (await claim.json()) as { request_id: string; lease: string };
  const leased = { "X-Render-Lease": job.lease };
  for (const name of ["project", "bake", "camera"]) {
    const file = await worker.get(
      `/api/worker/jobs/${job.request_id}/files/${name}`,
      { headers: leased },
    );
    expect(file.status(), name).toBe(200);
    expect(file.headers()["content-type"]).toContain("application/json");
  }
  await expect(frame.getByText("Rendu en cours", { exact: true })).toBeVisible({
    timeout: 15_000,
  });

  const png = fs.readFileSync(IMAGE);
  const [width, height] = [png.readUInt32BE(16), png.readUInt32BE(20)];
  const done = await worker.post(`/api/worker/jobs/${job.request_id}/result`, {
    headers: leased,
    multipart: {
      meta: JSON.stringify({
        seconds: 41.6,
        vram_peak_mib: 4800,
        width,
        height,
        engine: "fake",
      }),
      image: { name: "render.png", mimeType: "image/png", buffer: png },
    },
  });
  expect(done.status()).toBe(200);
  await worker.dispose();

  const image = frame.locator("img.rgal-img");
  await expect(image).toBeVisible({ timeout: 15_000 });
  await expect
    .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBe(width);
  await expect(menu).toBeEnabled();
  expect(failures).toEqual([]);
});

test("rendu réaliste : un autre compte n'a ni le bouton ni l'API", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await signIn(page, "voisine@example.org", "198.51.100.222");
  const { id, level } = await projectWithView(page);
  await gotoApp(page, `/#/projects/${id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(
    board.getByText("Rendus réalistes : bientôt en ligne"),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    board.getByRole("region", { name: "Rendus réalistes" }),
  ).toHaveCount(0);
  await expect(
    board.getByRole("button", { name: "Rendre ▾", exact: true }),
  ).toHaveCount(0);
  const origin = { Origin: new URL(page.url()).origin };
  const ask = await page.request.post(
    `/api/projects/${id}/levels/${level}/realistic`,
    { data: { camera_id: crypto.randomUUID() }, headers: origin },
  );
  expect(ask.status()).toBe(403);
  expect(
    (await page.request.get(`/api/projects/${id}/realistic`)).status(),
  ).toBe(403);
  const status = (await (
    await page.request.get("/api/realistic/status")
  ).json()) as { allowed: boolean };
  expect(status.allowed).toBe(false);
});
