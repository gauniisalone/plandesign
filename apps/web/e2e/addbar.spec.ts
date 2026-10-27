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
