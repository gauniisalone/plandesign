import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-98, lot L3 : coller une image ou un lien. Rien de choisi : le mini-menu au pointeur
 *  (Photos de la pièce ou Moodboard ; pour un lien, Moodboard ou Lien seul), au clavier ou à
 *  la souris, Échap annule. Un node choisi : le collage y entre directement, bandeau de 3 s
 *  avec sa barre et « Annuler ». Le serveur récupère l'image d'un lien : ici la route est
 *  simulée (aucun réseau réel dans les tests) ; sa garde est testée côté serveur. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
/** Un vrai PNG (64 × 64, dessiné par le navigateur), en base64. */
async function pngBase64(page: Page): Promise<string> {
  return page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d")!;
    g.fillStyle = "#c4743f";
    g.fillRect(0, 0, 64, 64);
    g.fillStyle = "#3e5a7e";
    g.fillRect(16, 16, 32, 32);
    return c.toDataURL("image/png").split(",")[1]!;
  });
}

type Item = {
  id: string;
  kind: string;
  frame?: string | null;
  role?: string | null;
  url?: string | null;
  ref?: string | null;
};

async function boardWithPlan(page: Page): Promise<string> {
  const id = await createEmptyProject(page, "Coller");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  return id;
}

async function items(page: Page, id: string): Promise<Item[]> {
  const r = await page.request.get(`/api/projects/${id}/board`);
  return ((await r.json()) as { items: Item[] }).items;
}

async function inNode(page: Page, id: string, role: string, kind: string) {
  const all = await items(page, id);
  const node = all.find((i) => i.kind === "frame" && i.role === role);
  return all.filter((i) => i.frame === node?.id && i.kind === kind);
}

/** Un collage comme Ctrl+V : un fichier image, ou du texte. */
async function paste(page: Page, what: { png?: boolean; text?: string }) {
  const b64 = what.png ? await pngBase64(page) : "";
  await page.evaluate(
    ({ b64, png, text }) => {
      const dt = new DataTransfer();
      if (png) {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        dt.items.add(new File([bytes], "collee.png", { type: "image/png" }));
      }
      if (text) dt.setData("text/plain", text);
      window.dispatchEvent(
        new ClipboardEvent("paste", { clipboardData: dt, bubbles: true }),
      );
    },
    { b64, png: what.png ?? false, text: what.text ?? "" },
  );
}

/** Le menu s'ouvre entier dans la fenêtre (leçon de KAN-110 : le mesurer avant d'y cliquer). */
async function expectMenuWhole(page: Page) {
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  const box = (await menu.boundingBox())!;
  const view = page.viewportSize()!;
  expect(box.height).toBeGreaterThan(120);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(view.width);
  expect(box.y + box.height).toBeLessThanOrEqual(view.height);
  const whole = await menu.evaluate(
    (el) => el.scrollHeight <= el.clientHeight + 1,
  );
  expect(whole).toBe(true);
  return menu;
}

test("coller une image sans rien choisir : le mini-menu, au clavier, puis Annuler", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1411, height: 796 });
  const id = await boardWithPlan(page);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const stage = (await page.locator(".board-stage").boundingBox())!;
  // Le pointeur sur l'espace de travail, vers le bas à droite : le menu s'ouvre là, entier.
  await page.mouse.move(
    stage.x + stage.width - 40,
    stage.y + stage.height - 40,
  );
  await board.click({ position: { x: 20, y: 20 } }); // rien de choisi
  await page.mouse.move(
    stage.x + stage.width - 40,
    stage.y + stage.height - 40,
  );

  await paste(page, { png: true });
  const menu = await expectMenuWhole(page);
  await expect(menu).toContainText("Rien n'est sélectionné.");
  await expect(menu).toContainText("Coller 1 image dans…");
  await expect(menu.getByRole("menuitem")).toHaveText([
    /Photos de la pièce/,
    /Moodboard/,
  ]);
  // Échap : rien n'est ajouté, et rien d'autre ne bouge.
  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  expect(await inNode(page, id, "inspirations", "file")).toHaveLength(0);

  // Flèche bas puis Entrée : le Moodboard.
  await paste(page, { png: true });
  await expectMenuWhole(page);
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("menu")).toHaveCount(0);
  const toast = page
    .getByRole("status")
    .filter({ hasText: "Ajouté au Moodboard" });
  await expect(toast).toBeVisible();
  await expect(toast.locator(".board-wire-toast-bar")).toHaveCount(1);
  await expect
    .poll(async () => (await inNode(page, id, "inspirations", "file")).length)
    .toBe(1);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(
        process.env.E2E_CAPTURE_DIR,
        "kan98-l3-colle-moodboard.png",
      ),
    });
  // « Annuler » retire l'image collée.
  await toast.getByRole("button", { name: "Annuler" }).click();
  await expect
    .poll(async () => (await inNode(page, id, "inspirations", "file")).length)
    .toBe(0);

  // À la souris : Photos de la pièce.
  await paste(page, { png: true });
  await (
    await expectMenuWhole(page)
  )
    .getByRole("menuitem", { name: /Photos de la pièce/ })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "Ajouté à Photos de la pièce" }),
  ).toBeVisible();
  await expect
    .poll(async () => (await inNode(page, id, "photos", "photo")).length)
    .toBe(1);
  // Le bandeau se retire seul après 3 s.
  await expect(
    page.getByRole("status").filter({ hasText: "Ajouté à" }),
  ).toHaveCount(0, {
    timeout: 6_000,
  });
});

test("coller un lien : dans le Moodboard choisi, image et source ; page sans image, carte lien", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1411, height: 796 });
  const id = await boardWithPlan(page);
  const board = page.getByRole("region", { name: "Espace de travail" });
  // La route du lien est simulée : la réponse est un vrai fichier joint du projet (envoyé
  // d'abord), avec la source qu'aurait gardée le serveur.
  const sent = await page.request.post(`/api/projects/${id}/media`, {
    params: { placed: "true" },
    multipart: {
      file: {
        name: "salon.png",
        mimeType: "image/png",
        buffer: Buffer.from(await pngBase64(page), "base64"),
      },
    },
  });
  expect(sent.status()).toBe(201);
  const ref = {
    ...(await sent.json()),
    source_url: "https://deco.exemple.fr/salon",
  };
  // La liste des fichiers joints, relue par l'espace de travail, porte la même source.
  await page.route(`**/api/projects/${id}/media`, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    const real = await route.fetch();
    const list = (await real.json()) as { id: string }[];
    await route.fulfill({
      response: real,
      json: list.map((m) => (m.id === ref.id ? ref : m)),
    });
  });
  const asked: string[] = [];
  await page.route(`**/api/projects/${id}/media/link*`, async (route) => {
    const url = (route.request().postDataJSON() as { url: string }).url;
    asked.push(url);
    if (url.includes("sans-image"))
      await route.fulfill({
        status: 422,
        contentType: "application/json",
        body: JSON.stringify({
          detail:
            "Cette page ne propose pas d'image : copiez l'image elle-même ou son adresse.",
        }),
      });
    else
      await route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify(ref),
      });
  });

  // Le Moodboard choisi : le lien y entre sans menu.
  const mood = board
    .locator(".board-item.kind-frame.role-inspirations")
    .first();
  await mood.locator(".board-frame-head").click();
  await paste(page, { text: "https://deco.exemple.fr/salon" });
  await expect(page.getByRole("menu")).toHaveCount(0);
  await expect(
    page.getByRole("status").filter({ hasText: "Ajouté au Moodboard" }),
  ).toBeVisible();
  expect(asked).toEqual(["https://deco.exemple.fr/salon"]);
  await expect
    .poll(async () => (await inNode(page, id, "inspirations", "file")).length)
    .toBe(1);
  const card = board
    .locator(".board-item.kind-file")
    .filter({ hasText: "salon" })
    .first();
  await card.hover();
  const source = card.locator(".board-source");
  await expect(source).toHaveText("deco.exemple.fr ↗");
  await expect(source).toHaveAttribute("href", "https://deco.exemple.fr/salon");
  await expect(source).toHaveCSS("opacity", "1");

  // Une page sans image : une carte lien dans le Moodboard, et le message dit pourquoi.
  await mood.locator(".board-frame-head").click();
  await paste(page, { text: "https://deco.exemple.fr/sans-image" });
  await expect(
    page.getByText("Cette page ne propose pas d'image"),
  ).toBeVisible();
  await expect
    .poll(async () =>
      (await inNode(page, id, "inspirations", "link")).map((i) => i.url),
    )
    .toEqual(["https://deco.exemple.fr/sans-image"]);

  // Photos de la pièce choisi : un lien n'y entre pas, le menu demande ; « Lien seul ».
  const photos = board.locator(".board-item.kind-frame.role-photos").first();
  await photos.locator(".board-frame-head").click();
  await paste(page, { text: "https://autre.exemple.fr/page" });
  const menu = await expectMenuWhole(page);
  await expect(menu).toContainText("Coller l'image de ce lien dans…");
  await expect(menu.getByRole("menuitem")).toHaveText([
    /Moodboard/,
    /Lien seul/,
  ]);
  await menu.getByRole("menuitem", { name: /Lien seul/ }).click();
  await expect
    .poll(async () =>
      (await items(page, id)).some(
        (i) => i.kind === "link" && i.url === "https://autre.exemple.fr/page",
      ),
    )
    .toBe(true);
  expect(asked, "« Lien seul » ne demande rien au serveur").toHaveLength(2);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-l3-liens.png"),
    });
});
