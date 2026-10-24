import fs from "node:fs";
import path from "node:path";
import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { E2E_WORKER_TOKEN, RENDER_ACCOUNT } from "./render-account";

/** KAN-91 (lot L6) : galerie de taille fixe du cadre Rendus, visionneuse plein écran (curseur
 *  avant/après contre la photo réelle ou la vue 3D), « Rendre depuis la photo » (D5),
 *  couverture du projet dans « Mes projets », réactions, variante, cloche, et D2 (pas de rendu
 *  sans plan). Le poste est SIMULÉ comme dans realistic.spec.ts (ses appels HTTP avec son
 *  jeton). Chaque personne et le poste ont leur propre X-Forwarded-For (198.51.100.231 à 233). */

const IMAGE = path.resolve("../../tests/fixtures/rect_room/image.png");
const PNG = fs.readFileSync(IMAGE);
const SIZE = [PNG.readUInt32BE(16), PNG.readUInt32BE(20)] as const;

async function signIn(page: Page, email: string, ip: string): Promise<void> {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": ip });
  await gotoApp(page, "/");
  // Le compte autorisé se connecte plusieurs fois dans la même base : le code d'une connexion
  // précédente ne doit pas être relu à la place du nouveau.
  const lastCode = async () => {
    const r = await page.request.get(
      `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
    );
    return r.ok() ? ((await r.json()) as { code: string }).code : "";
  };
  const before = await lastCode();
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      code = await lastCode();
      return code !== before && /^\d{6}$/.test(code);
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

/** Un espace (nom unique à chaque passage), avec ou sans murs, un point de vue libre et, sur
 *  demande, une photo et sa caméra estimée (comme KAN-66 et KAN-79 la gardent). */
async function project(
  page: Page,
  { walls = true, photo = false } = {},
): Promise<{ id: string; level: string; name: string }> {
  const origin = { Origin: new URL(page.url()).origin };
  const name = `Salon rendu ${Date.now().toString(36)}`;
  const created = await page.request.post("/api/projects", {
    data: { name },
    headers: origin,
  });
  expect(created.status()).toBe(201);
  const body = (await created.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(body.levels)[0]!;
  const ops = `/api/projects/${body.id}/levels/${level}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
  for (let i = 0; walls && i < 4; i++) {
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
  const view = await page.request.post(ops, {
    data: {
      op: "add_camera",
      position: { x: 1, y: 1 },
      yaw: 0.6,
      name: "Vue fenêtre",
    },
    headers: origin,
  });
  expect(view.ok()).toBe(true);
  if (photo) {
    const uploaded = await page.request.post(
      `/api/projects/${body.id}/images`,
      {
        headers: origin,
        multipart: {
          file: { name: "salon.png", mimeType: "image/png", buffer: PNG },
        },
      },
    );
    expect(uploaded.status()).toBe(201);
    const image = ((await uploaded.json()) as { image: { id: string } }).image;
    const added = await page.request.post(ops, {
      data: {
        op: "add_camera",
        position: { x: 3, y: 2 },
        yaw: 3.6,
        name: "Photo",
      },
      headers: origin,
    });
    expect(added.ok()).toBe(true);
    const after = (await added.json()) as {
      project: {
        levels: Record<
          string,
          { elements: Record<string, { type: string; name?: string }> }
        >;
      };
    };
    const camera = Object.entries(after.project.levels[level]!.elements).find(
      ([, e]) => e.type === "camera" && e.name === "Photo",
    )![0];
    const estimated = await page.request.post(ops, {
      data: {
        op: "update_element",
        element_id: camera,
        fields: { image_id: image.id, focal_px: 900 },
      },
      headers: origin,
    });
    expect(estimated.ok(), await estimated.text()).toBe(true);
  }
  return { id: body.id, level, name };
}

/** Le poste simulé prend la plus ancienne demande, lit ses fichiers et dépose l'image. */
async function deliver(worker: APIRequestContext): Promise<{
  request_id: string;
  camera: { width_px: number; height_px: number };
}> {
  const claim = await worker.post("/api/worker/claim", {
    data: { state: "idle" },
  });
  expect(claim.status()).toBe(200);
  const job = (await claim.json()) as { request_id: string; lease: string };
  const leased = { "X-Render-Lease": job.lease };
  let camera = { width_px: 0, height_px: 0 };
  for (const name of ["project", "bake", "camera"]) {
    const file = await worker.get(
      `/api/worker/jobs/${job.request_id}/files/${name}`,
      { headers: leased },
    );
    expect(file.status(), name).toBe(200);
    if (name === "camera") camera = (await file.json()) as typeof camera;
  }
  const done = await worker.post(`/api/worker/jobs/${job.request_id}/result`, {
    headers: leased,
    multipart: {
      meta: JSON.stringify({
        seconds: 95,
        vram_peak_mib: 0,
        width: SIZE[0],
        height: SIZE[1],
        engine: "fake",
      }),
      image: { name: "render.png", mimeType: "image/png", buffer: PNG },
    },
  });
  expect(done.status()).toBe(200);
  return { request_id: job.request_id, camera };
}

test("galerie, visionneuse avant/après, depuis la photo, couverture, réactions, variante", async ({
  page,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const failures: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  await signIn(page, RENDER_ACCOUNT.email, "198.51.100.231");
  const { id, name } = await project(page, { photo: true });
  const worker = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${E2E_WORKER_TOKEN}`,
      "X-Forwarded-For": "198.51.100.232",
    },
  });
  await gotoApp(page, `/#/projects/${id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const frame = board.getByRole("region", { name: "Rendus réalistes" });
  const menuButton = frame.getByRole("button", { name: "Rendre ▾" });
  await expect(menuButton).toBeEnabled({ timeout: 30_000 });

  // 1. Une vue du plan : la case « en attente » d'abord (le poste simulé de realistic.spec.ts
  // a pu appeler il y a moins d'une minute : en ligne ou hors ligne), puis l'image.
  await menuButton.click();
  await frame
    .getByRole("menuitem", { name: /Rendre la vue « Vue fenêtre »/ })
    .click();
  await expect(
    frame
      .locator(".rgal-running")
      .getByText(
        /^(En attente|Poste de rendu hors ligne : le rendu sera fait à son retour)$/,
      ),
  ).toBeVisible();
  await expect(frame.getByText("~2 min").first()).toBeVisible();
  await deliver(worker);
  const gallery = frame.getByRole("list", { name: "Galerie des rendus" });
  await expect(gallery.locator("img.rgal-img")).toHaveCount(1, {
    timeout: 15_000,
  });

  // 2. Depuis la photo (D5) : la caméra estimée de la photo, au format de la photo.
  await menuButton.click();
  await frame.getByRole("menuitem", { name: /Depuis la photo 1/ }).click();
  await expect(frame.getByRole("status").first()).toBeVisible();
  const fromPhoto = await deliver(worker);
  expect(fromPhoto.camera.width_px / fromPhoto.camera.height_px).toBeCloseTo(
    SIZE[0] / SIZE[1],
    2,
  );
  await expect(gallery.locator("img.rgal-img")).toHaveCount(2, {
    timeout: 15_000,
  });
  // La plus récente d'abord, même format 4:3.
  const tiles = gallery.getByRole("button", { name: /Ouvrir le rendu/ });
  await expect(tiles.first()).toHaveAccessibleName(/photo 1/);
  const boxes = await gallery
    .locator("li.rgal-cell")
    .evaluateAll((cells) =>
      cells.map(
        (c) =>
          c.getBoundingClientRect().width / c.getBoundingClientRect().height,
      ),
    );
  for (const ratio of boxes) expect(ratio).toBeCloseTo(4 / 3, 1);

  // 3. Visionneuse : avant/après fidèle contre la photo 1.
  await tiles.first().click();
  const viewer = page.getByRole("dialog", { name: "Visionneuse des rendus" });
  await expect(viewer).toBeVisible();
  await expect(viewer.getByText("1 sur 2")).toBeVisible();
  await expect(
    viewer.getByText("Photo réelle (photo 1)", { exact: true }),
  ).toBeVisible();
  await expect(
    viewer.getByText(
      "Même point de vue que la photo 1 : l'avant/après est fidèle.",
    ),
  ).toBeVisible();
  const slider = viewer.getByRole("slider", { name: "Avant / après" });
  await slider.fill("20");
  await expect
    .poll(() =>
      viewer
        .locator(".rview-stage")
        .evaluate((el) =>
          getComputedStyle(el).getPropertyValue("--rview-cut").trim(),
        ),
    )
    .toBe("20%");
  await expect(
    viewer.getByRole("link", { name: /Télécharger/ }),
  ).toHaveAttribute("download", /\.png$/);

  // Réagir (KAN-78) : ma réaction comptée.
  await viewer.getByRole("button", { name: "J'adore" }).click();
  await expect(
    viewer.getByRole("button", { name: "J'adore (1)" }),
  ).toHaveAttribute("aria-pressed", "true");

  // 4. Le rendu suivant (vue libre) : comparé à la photo, l'écart est dit, avec le bouton.
  await page.keyboard.press("ArrowRight");
  await expect(viewer.getByText("2 sur 2")).toBeVisible();
  await viewer
    .getByRole("button", { name: "Photo réelle", exact: true })
    .click();
  await expect(
    viewer.getByText(
      /n'a pas le point de vue de la photo 1 : la comparaison est approximative/,
    ),
  ).toBeVisible();
  await expect(
    viewer.getByRole("button", { name: /Rendre depuis la photo 1/ }).first(),
  ).toBeEnabled();
  await viewer.getByRole("button", { name: "Vue 3D", exact: true }).click();
  await expect(viewer.locator(".rview-tag-before")).toHaveText("Vue 3D");
  await expect(viewer.getByLabel("Vue 3D du plan")).toBeVisible();

  // 5. Couverture du projet : chip dans la galerie, vignette de « Mes projets ».
  await viewer
    .getByRole("button", { name: "Définir comme couverture du projet" })
    .click();
  await expect(
    viewer.getByRole("button", { name: "Couverture du projet ✓" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(gallery.getByText("couverture", { exact: true })).toBeVisible();
  const cover = await page.request.get(`/api/projects/${id}/cover`);
  expect(cover.headers()["x-cover-kind"]).toBe("render");
  expect(cover.headers()["content-type"]).toBe("image/webp");

  // 6. Variante : même vue, autre tirage ; le poste reçoit son tirage.
  await gallery
    .getByRole("button", { name: /Ouvrir le rendu/ })
    .first()
    .click();
  await viewer.getByRole("button", { name: /Nouvelle variante/ }).click();
  await expect(viewer).toHaveCount(0);
  const claim = await worker.post("/api/worker/claim", {
    data: { state: "idle" },
  });
  expect(claim.status()).toBe(200);
  expect(typeof ((await claim.json()) as { seed?: number }).seed).toBe(
    "number",
  );
  await worker.dispose();

  // 7. La cloche : « Votre rendu est prêt » (KAN-52).
  const bell = (await (
    await page.request.get("/api/notifications")
  ).json()) as {
    items: { type: string; mine: boolean }[];
  };
  expect(bell.items.some((i) => i.type === "render.done" && i.mine)).toBe(true);

  // 8. « Mes projets » : la carte du projet montre la couverture (le rendu, plus le plan).
  await gotoApp(page, "/");
  const card = page.getByRole("button", { name: `Ouvrir « ${name} »` });
  const img = card.locator("img");
  await expect
    .poll(
      () =>
        img.evaluate(async (el: HTMLImageElement) =>
          el.complete && el.naturalWidth > 0
            ? (await fetch(el.currentSrc)).headers.get("X-Cover-Kind")
            : null,
        ),
      { timeout: 15_000 },
    )
    .toBe("render");
  expect(failures).toEqual([]);
});

test("pas de rendu sans plan relié : bouton inactif et phrase", async ({
  page,
}) => {
  test.setTimeout(90_000);
  await signIn(page, RENDER_ACCOUNT.email, "198.51.100.233");
  const { id, level } = await project(page, { walls: false });
  await gotoApp(page, `/#/projects/${id}/board`);
  const frame = page
    .getByRole("region", { name: "Espace de travail" })
    .getByRole("region", { name: "Rendus réalistes" });
  await expect(frame.getByText("Reliez un plan pour rendre")).toBeVisible({
    timeout: 30_000,
  });
  await expect(frame.getByRole("button", { name: "Rendre ▾" })).toBeDisabled();
  // Le serveur refuse aussi (409), même appelé directement.
  const origin = { Origin: new URL(page.url()).origin };
  const listed = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, { type: string }> }>;
  };
  const camera = Object.entries(listed.levels[level]!.elements).find(
    ([, e]) => e.type === "camera",
  )![0];
  const refused = await page.request.post(
    `/api/projects/${id}/levels/${level}/realistic`,
    { data: { camera_id: camera }, headers: origin },
  );
  expect(refused.status()).toBe(409);

  // Avec un plan, le repli dit « relié » ; la réponse du serveur, comme le 409 du lot L1
  // (carte Rendus sans lien plan → rendus), prime : le bouton se grise avec la phrase. Même
  // session (le nombre de codes par adresse est limité).
  const linkedProject = await project(page);
  await page.route(
    /\/api\/projects\/[0-9a-f-]+\/levels\/[0-9a-f-]+\/realistic$/,
    (route) =>
      route.request().method() === "POST"
        ? route.fulfill({
            status: 409,
            contentType: "application/json",
            body: JSON.stringify({ detail: "Reliez un plan pour rendre" }),
          })
        : route.continue(),
  );
  await gotoApp(page, `/#/projects/${linkedProject.id}/board`);
  const menu = frame.getByRole("button", { name: "Rendre ▾" });
  await expect(menu).toBeEnabled({ timeout: 30_000 });
  await expect(frame.getByText("Reliez un plan pour rendre")).toHaveCount(0);
  await menu.click();
  await frame
    .getByRole("menuitem", { name: /Rendre la vue « Vue fenêtre »/ })
    .click();
  await expect(frame.getByText("Reliez un plan pour rendre")).toBeVisible();
  await expect(menu).toBeDisabled();
});
