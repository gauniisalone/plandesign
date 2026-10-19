import { expect, test, type Locator } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** T7a (demande d'augus, 18 oct.) : importer droit dans les cadres Photos et Inspirations.
 *  Cadre vide : « Importer » sous sa ligne ; cadre rempli : « + » dans l'en-tête. Le
 *  sélecteur ne propose que ce que le cadre attend ; le fichier entre dans ce cadre, comme un
 *  dépôt (aucun geste de plus) ; un plan mis dans Photos se lit comme un plan. Les cadres
 *  sont vérifiés à l'écran (une lecture du tableau par l'API le resynchroniserait pendant
 *  l'enregistrement du geste). */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const SHOTS = path.resolve("e2e/screenshots");

/** Les éléments de cette sorte posés dans ce cadre (boîte dans la boîte du cadre). */
async function inside(frame: Locator, items: Locator): Promise<number> {
  const box = await frame.boundingBox();
  if (!box) return 0;
  let n = 0;
  for (const item of await items.all()) {
    const b = await item.boundingBox();
    if (
      b &&
      b.x >= box.x - 1 &&
      b.y >= box.y - 1 &&
      b.x + b.width <= box.x + box.width + 1 &&
      b.y + b.height <= box.y + box.height + 1
    )
      n += 1;
  }
  return n;
}

test("T7a : importer dans les cadres Photos et Inspirations", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO) || !existsSync(PLAN_DXF), "fixtures absentes");
  await page.setViewportSize({ width: 1440, height: 900 });
  const id = await createEmptyProject(page, "E2E import dans les cadres");
  // Bascule « générer au dépôt » et chemin automatique coupés : le test reste léger (ni
  // construction du plan depuis la photo, ni rendus après le plan lu).
  const patched = await page.request.patch(`/api/projects/${id}`, {
    data: { render: { auto_on_drop: false, auto_path: false } },
  });
  expect(patched.ok()).toBeTruthy();
  await reloadApp(page);
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const inspirations = board.locator(
    ".board-item.kind-frame.role-inspirations",
  );
  const photoItems = board.locator(".board-item.kind-photo");
  const references = board.locator(".board-item.reference");
  const input = page.getByTestId("frame-file-input");
  await page.screenshot({ path: path.join(SHOTS, "t7a_import_cadres.png") });

  // Photos, cadre vide : « Importer » ouvre le sélecteur des photos et plans.
  let chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await expect(input).toHaveAttribute(
    "accept",
    "image/jpeg,image/png,image/webp,.dxf,.pdf,.svg",
  );
  await (await chooser).setFiles(PHOTO);
  await expect(photoItems).toHaveCount(1, { timeout: 30_000 });
  await expect.poll(() => inside(photos, photoItems)).toBe(1);
  await expect(photos.getByRole("button", { name: "Importer" })).toHaveCount(0);

  // Inspirations, cadre vide : images seulement ; l'image arrive cochée Référence.
  chooser = page.waitForEvent("filechooser");
  await inspirations.getByRole("button", { name: "Importer" }).click();
  await expect(input).toHaveAttribute(
    "accept",
    "image/jpeg,image/png,image/webp",
  );
  await (await chooser).setFiles(PHOTO);
  await expect(references).toHaveCount(1, { timeout: 30_000 });
  await expect.poll(() => inside(inspirations, references)).toBe(1);
  // Sous l'en-tête du cadre : son titre reste lisible.
  const head = (await inspirations.locator(".board-frame-head").boundingBox())!;
  const ref = (await references.first().boundingBox())!;
  expect(ref.y).toBeGreaterThanOrEqual(head.y + head.height - 1);
  await expect(photoItems).toHaveCount(1);

  // Cadre rempli : le « + » de l'en-tête, avec son infobulle.
  const add = photos.getByRole("button", { name: "Ajouter des photos" });
  await expect(add).toHaveAttribute("title", "Ajouter des photos");
  await expect(
    inspirations.getByRole("button", { name: "Ajouter des images d'ambiance" }),
  ).toBeVisible();
  chooser = page.waitForEvent("filechooser");
  await add.click();
  await (await chooser).setFiles(PHOTO);
  await expect(photoItems).toHaveCount(2, { timeout: 30_000 });
  await expect.poll(() => inside(photos, photoItems)).toBe(2);
  // Aucun conflit d'enregistrement au passage.
  await expect(page.getByText(/enregistré ailleurs/)).toHaveCount(0);
  await expect(page.locator(".save-dot.saved")).toBeVisible();
  await page.screenshot({
    path: path.join(SHOTS, "t7a_import_cadres_remplis.png"),
  });

  // Mesure : importer par le bouton vaut un dépôt, aucun geste de plus.
  const metrics = (await (
    await page.request.get(`/api/projects/${id}/metrics`)
  ).json()) as { gestures_before_first_render: number };
  expect(metrics.gestures_before_first_render).toBe(0);

  // Un plan mis dans Photos se lit comme un plan (comme « Photo ou plan de la pièce »).
  chooser = page.waitForEvent("filechooser");
  await add.click();
  await (await chooser).setFiles(PLAN_DXF);
  await expect(page.locator("polygon.wall")).toHaveCount(4, {
    timeout: 30_000,
  });
  await expectSpace(page, "plan");
});
