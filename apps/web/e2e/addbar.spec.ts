import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-98, lot L1 : la barre d'ajout, fixe en bas au centre, à la même place et à la même
 *  taille à tout zoom, sans jamais recouvrir la colonne d'outils ; elle ajoute chaque type de
 *  node (Photos de la pièce et Moodboard posés, Plan par la boîte « Nouveau plan », Rendus
 *  grisé avec sa raison tant que chaque plan a le sien). Les nodes ont un titre lisible et un
 *  compteur ; « Inspirations » s'appelle « Moodboard ». */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

async function boardWithPlan(page: Page): Promise<string> {
  const id = await createEmptyProject(page, "Barre d'ajout");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  return id;
}

test("barre d'ajout : fixe à tout zoom, hors de la colonne d'outils, ajoute chaque type", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await boardWithPlan(page);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const bar = page.getByRole("group", { name: "Ajouter" });
  await expect(bar).toBeVisible();
  await expect(bar.getByRole("button")).toHaveText([
    "Plan",
    "Photos de la pièce",
    "Moodboard",
    "Rendus",
  ]);
  // Plus de boutons flottants dans la toile.
  await expect(board.locator(".board-new-plan")).toHaveCount(0);
  await expect(
    board.getByRole("button", { name: /Nouvelle carte/ }),
  ).toHaveCount(0);

  // Même place, même taille, à 100 % comme au dézoom ; jamais sur la colonne d'outils.
  const zoom = page.getByRole("group", { name: "Zoom de l'espace de travail" });
  const before = (await bar.boundingBox())!;
  for (let k = 0; k < 6; k++)
    await zoom.getByRole("button", { name: "Dézoomer" }).click();
  const after = (await bar.boundingBox())!;
  expect(Math.abs(after.x - before.x)).toBeLessThan(1);
  expect(Math.abs(after.y - before.y)).toBeLessThan(1);
  expect(Math.abs(after.width - before.width)).toBeLessThan(1);
  const rail = await page.locator(".board-rail").last().boundingBox();
  if (rail) expect(after.x).toBeGreaterThan(rail.x + rail.width);

  // Moodboard : un node vide, titré « Moodboard », au-dessus du node Rendus de départ.
  const moods = board.locator(".board-item.kind-frame.role-inspirations");
  await expect(moods).toHaveCount(1);
  await expect(moods.first()).toContainText("Moodboard");
  const start = (await moods.first().boundingBox())!;
  await bar.getByRole("button", { name: "Moodboard", exact: true }).click();
  await expect(moods).toHaveCount(2);
  const renders = board.locator(".board-item.kind-frame.role-renders").first();
  const r = (await renders.boundingBox())!;
  // Le nouveau : celui qui n'est pas à la place du Moodboard de départ (l'ordre du DOM ne
  // le dit pas).
  const boxes = await Promise.all(
    (await moods.all()).map(async (m) => (await m.boundingBox())!),
  );
  const added = boxes.find(
    (b) => Math.abs(b.x - start.x) > 1 || Math.abs(b.y - start.y) > 1,
  )!;
  expect(added).toBeDefined();
  expect(added.y + added.height).toBeLessThanOrEqual(r.y + 1);
  expect(Math.abs(added.x - r.x)).toBeLessThan(2);

  // Photos de la pièce : un node vide de plus.
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await expect(photos).toHaveCount(1);
  await bar
    .getByRole("button", { name: "Photos de la pièce", exact: true })
    .click();
  await expect(photos).toHaveCount(2);

  // Rendus : chaque plan a déjà le sien : grisé, la raison dans l'infobulle.
  const rendus = bar.getByRole("button", { name: "Rendus", exact: true });
  await expect(rendus).toBeDisabled();
  await expect(rendus).toHaveAttribute(
    "title",
    /chaque plan a déjà son node Rendus/,
  );

  // Plan : la boîte « Nouveau plan » existante.
  await bar.getByRole("button", { name: "Plan", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "Nouveau plan" });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-barre-dezoom.png"),
    });
});

test("nodes : titre lisible à l'ouverture et compteur", async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1160 });
  await boardWithPlan(page);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const head = board
    .locator(".board-item.kind-frame.role-inspirations .board-frame-head")
    .first();
  await expect(head).toBeVisible();
  // Au zoom d'ouverture, le titre fait au moins 14 px à l'écran (règle de la maquette :
  // jamais sous 14 px ; 16 px semi-gras visé).
  const px = await head.evaluate((el) => {
    const size = parseFloat(getComputedStyle(el).fontSize);
    const world = el.closest(".board-world") as HTMLElement | null;
    const m = world ? new DOMMatrix(getComputedStyle(world).transform) : null;
    return size * (m ? m.a : 1);
  });
  expect(px).toBeGreaterThanOrEqual(14);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-ouverture.png"),
    });
});

test("L2 : ouverture en grand, Moodboard au-dessus de Rendus, étiquettes, ports, paliers, Maj+1", async ({
  page,
}) => {
  test.setTimeout(120_000);
  // La fenêtre d'augus (dernière capture : 1411 × 796).
  await page.setViewportSize({ width: 1411, height: 796 });
  await boardWithPlan(page);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const node = (role: string) =>
    board.locator(`.board-item.kind-frame.role-${role}`).first();
  const panelButton = (open: boolean) =>
    page.getByRole("button", {
      name: open ? "Masquer le panneau" : "Afficher le panneau",
    });
  // KAN-64 : le panneau de droite est ouvert d'office (PO, option a du 27 oct.).
  await expect(panelButton(true)).toBeVisible();
  // Aucun node sous la colonne d'outils ni sous la barre d'ajout à l'ouverture.
  const rail = (await page
    .getByRole("toolbar", { name: "Outils de l'espace de travail" })
    .boundingBox())!;
  const bar = (await page
    .getByRole("group", { name: "Ajouter" })
    .boundingBox())!;
  const stage = (await page.locator(".board-stage").boundingBox())!;
  const nodes = await board
    .locator(".board-item.kind-frame, .board-item.kind-plan")
    .evaluateAll((fs) =>
      fs.map((f) => f.getBoundingClientRect().toJSON() as DOMRect),
    );
  const apart = (
    c: DOMRect,
    r: { x: number; y: number; width: number; height: number },
  ) =>
    c.right <= r.x ||
    c.left >= r.x + r.width ||
    c.bottom <= r.y ||
    c.top >= r.y + r.height;
  for (const c of nodes) {
    expect(apart(c, rail), "node sous la colonne d'outils").toBe(true);
    expect(apart(c, bar), "node sous la barre d'ajout").toBe(true);
  }
  // L'ombre du bord droit dit exactement s'il reste de la suite à droite.
  const cut = nodes.some((c) => c.right > stage.x + stage.width + 1);
  await expect(page.locator(".board-more-right")).toHaveCount(cut ? 1 : 0);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-l2-1411x796.png"),
    });
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.reload();
  await expect(node("renders")).toBeVisible({ timeout: 30_000 });
  // En 1920 × 1080, tout tient à l'ouverture, panneau ouvert : plus d'ombre.
  await expect(page.locator(".board-more-right")).toHaveCount(0);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-l2-1920x1080.png"),
    });
  // Un espace neuf : le Moodboard au-dessus de Rendus, dans sa colonne.
  const mood = (await node("inspirations").boundingBox())!;
  const rendus = (await node("renders").boundingBox())!;
  const photos = (await node("photos").boundingBox())!;
  expect(Math.abs(mood.x - rendus.x)).toBeLessThan(2);
  expect(mood.y + mood.height).toBeLessThan(rendus.y);
  expect(Math.abs(photos.y - rendus.y)).toBeLessThan(2);
  // Ouverture : un node fait au moins 360 px à l'écran (la maquette A), au palier « détail ».
  expect(photos.width).toBeGreaterThanOrEqual(359);
  const zoom = page.getByRole("group", { name: "Zoom de l'espace de travail" });
  await expect(zoom).toContainText("· détail");
  // Étiquettes des fils et ports à la couleur du type.
  await expect(
    board.locator(".board-wire-label", { hasText: "construit le plan" }),
  ).toBeVisible();
  await expect(
    board.locator(".board-wire-label", { hasText: "guide le style" }),
  ).toBeVisible();
  await expect(
    board.locator(".board-wire[data-kind=guides_style] .port-inspirations"),
  ).toHaveCount(1);
  await expect(
    board.locator(".board-wire[data-kind=guides_style] .port-renders"),
  ).toHaveCount(1);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-l2-ouverture.png"),
    });

  // Maj+1 : « Tout voir » montre TOUT, d'un seul appui (de retour sur l'écran d'augus).
  await page.setViewportSize({ width: 1411, height: 796 });
  await page.keyboard.press("Shift+Digit1");
  await expect(page.locator(".board-more-right")).toHaveCount(0);
  const view = (await board.boundingBox())!;
  for (const role of ["photos", "inspirations", "renders"]) {
    const b = (await node(role).boundingBox())!;
    expect(b.x).toBeGreaterThanOrEqual(view.x - 1);
    expect(b.x + b.width).toBeLessThanOrEqual(view.x + view.width + 1);
    expect(b.y + b.height).toBeLessThanOrEqual(view.y + view.height + 1);
  }

  // Paliers : en dézoomant, « vignettes » puis « titres » ; les étiquettes partent aux titres.
  const out = zoom.getByRole("button", { name: "Dézoomer" });
  for (let k = 0; k < 4; k++) await out.click();
  await expect(zoom).toContainText("· vignettes");
  for (let k = 0; k < 8; k++) await out.click();
  await expect(zoom).toContainText("· titres");
  await expect(board.locator(".board-wire-label").first()).toBeHidden();
  await expect(node("photos").locator(".board-frame-head")).toBeVisible();
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "kan98-l2-titres.png"),
    });

  // Plus de place d'un clic (notes de MEP) : le panneau se ferme par son bouton, la toile
  // s'élargit ; Maj+1 montre alors tout, plus grand.
  await panelButton(true).click();
  await expect(panelButton(false)).toBeVisible();
  await page.keyboard.press("Shift+Digit1");
  await expect(page.locator(".board-more-right")).toHaveCount(0);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(
        process.env.E2E_CAPTURE_DIR,
        "kan98-l2-1411x796-sans-panneau.png",
      ),
    });
});
