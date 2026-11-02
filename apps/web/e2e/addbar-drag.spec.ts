import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  addButton,
  addByKey,
  createEmptyProject,
  dragAdd,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-111 (demande d'augus) : la barre d'ajout passe au glisser-déposer. Un simple clic ne
 *  crée rien (bulle « Faites glisser… ») ; un glisser montre un aperçu à taille réelle, déjà à
 *  sa place finale, et pose le node au lâcher, jamais sur un autre ; lâché sur un node Rendus,
 *  un Moodboard s'y relie (un seul Ctrl+Z pour les deux) ; Échap ou un lâcher hors de l'espace
 *  de travail ne créent rien ; Entrée pose au centre ; fenêtre étroite : icônes seules, sans
 *  défilement, et le zoom ne recouvre jamais la barre. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const HINT = "Faites glisser sur l'espace de travail pour l'ajouter";

async function boardWithPlan(page: Page, name: string): Promise<string> {
  const id = await createEmptyProject(page, name);
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  await page.keyboard.press("Shift+Digit1");
  return id;
}

async function shot(page: Page, name: string) {
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, `${name}.png`),
    });
}

type Box = { x: number; y: number; width: number; height: number };
const apart = (a: Box, b: Box) =>
  a.x + a.width <= b.x ||
  b.x + b.width <= a.x ||
  a.y + a.height <= b.y ||
  b.y + b.height <= a.y;

test("un clic ne crée rien ; un glisser pose au lâcher ; Échap et hors de l'espace : rien", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await boardWithPlan(page, "Glisser-déposer");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await expect(photos).toHaveCount(1);

  // Un simple clic : rien n'est créé, la bulle dit le geste.
  await addButton(page, "Photos de la pièce").click();
  await expect(page.getByRole("status").filter({ hasText: HINT })).toBeVisible();
  await expect(photos).toHaveCount(1);

  // Un glisser : l'aperçu suit, à taille réelle, puis le node se pose au lâcher.
  const stage = (await page.locator(".board-stage").boundingBox())!;
  const spot = { x: stage.x + stage.width * 0.3, y: stage.y + stage.height * 0.3 };
  await dragAdd(page, "Photos de la pièce", spot, { release: false });
  const ghost = page.locator(".board-add-ghost");
  await expect(ghost).toBeVisible();
  const g = (await ghost.boundingBox())!;
  const node = (await photos.first().boundingBox())!;
  // Taille réelle : celle d'un node à ce zoom (780 × 468 dans le monde).
  expect(Math.abs(g.width / g.height - 780 / 468)).toBeLessThan(0.02);
  expect(Math.abs(g.width - node.width)).toBeLessThan(2);
  await shot(page, "kan111-1-apercu");
  await page.mouse.up();
  await expect(ghost).toHaveCount(0);
  await expect(photos).toHaveCount(2);

  // Échap pendant le glisser : rien.
  await dragAdd(page, "Photos de la pièce", spot, { release: false });
  await expect(ghost).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(ghost).toHaveCount(0);
  await page.mouse.up();
  await expect(photos).toHaveCount(2);

  // Lâché sur la barre elle-même (hors de l'espace de travail) : rien.
  await dragAdd(page, "Photos de la pièce", addButton(page, "Rendus"));
  await expect(photos).toHaveCount(2);

  // Quatre lâchers au même point : quatre nodes, aucun chevauchement.
  const moods = board.locator(".board-item.kind-frame.role-inspirations");
  const before = await moods.count();
  for (let k = 0; k < 4; k++) await dragAdd(page, "Moodboard", spot);
  await expect(moods).toHaveCount(before + 4);
  const all = await board
    .locator(".board-item.kind-frame, .board-item.kind-plan")
    .evaluateAll((fs) => fs.map((f) => f.getBoundingClientRect().toJSON() as DOMRect));
  for (let i = 0; i < all.length; i++)
    for (let j = i + 1; j < all.length; j++)
      expect(apart(all[i]!, all[j]!), `nodes ${i} et ${j} superposés`).toBe(true);

  // Entrée au clavier : un node de plus, au centre, sur une place libre.
  await addByKey(page, "Photos de la pièce");
  await expect(photos).toHaveCount(3);
});

test("lâché sur Rendus, un Moodboard s'y relie ; un seul Ctrl+Z, puis Ctrl+Y", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await boardWithPlan(page, "Glisser et relier");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const moods = board.locator(".board-item.kind-frame.role-inspirations");
  const guides = board.locator(".board-wire[data-kind=guides_style]");
  await expect(moods).toHaveCount(1);
  await expect(guides).toHaveCount(1);
  const renders = board.locator(".board-item.kind-frame.role-renders").first();

  // La cible s'éclaire et le dit, avant le lâcher.
  await dragAdd(page, "Moodboard", renders, { release: false });
  await expect(renders).toHaveClass(/board-add-target/);
  await expect(page.getByText("Relâchez pour relier")).toBeVisible();
  await shot(page, "kan111-2-cible");
  await page.mouse.up();
  await expect(moods).toHaveCount(2);
  await expect(guides).toHaveCount(2);
  // Au-dessus de Rendus, dans sa colonne, sans chevaucher le Moodboard de départ.
  const r = (await renders.boundingBox())!;
  const boxes = await Promise.all((await moods.all()).map(async (m) => (await m.boundingBox())!));
  for (const b of boxes) {
    expect(b.y + b.height).toBeLessThanOrEqual(r.y + 1);
    expect(Math.abs(b.x - r.x)).toBeLessThan(2);
  }
  expect(apart(boxes[0]!, boxes[1]!)).toBe(true);

  // Un seul Ctrl+Z retire le node ET son lien ; Ctrl+Y les rend tous deux.
  await page.keyboard.press("Control+z");
  await expect(moods).toHaveCount(1);
  await expect(guides).toHaveCount(1);
  await page.keyboard.press("Control+y");
  await expect(moods).toHaveCount(2);
  await expect(guides).toHaveCount(2);
});

test("fenêtre étroite : icônes seules, sans défilement, le zoom ne recouvre pas la barre", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 900, height: 700 });
  await boardWithPlan(page, "Fenêtre étroite");
  const bar = page.getByRole("group", { name: "Ajouter" });
  await expect(bar).toHaveClass(/compact/);
  // Les libellés restent dans les noms accessibles (lecteur d'écran, bulles au survol).
  await expect(addButton(page, "Moodboard")).toBeVisible();
  await expect(bar.locator(".board-addbar-text").first()).toBeHidden();
  const scroll = await bar.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(scroll).toBeLessThanOrEqual(1);
  const zoom = (await page
    .getByRole("group", { name: "Zoom de l'espace de travail" })
    .boundingBox())!;
  expect(apart((await bar.boundingBox())!, zoom)).toBe(true);
  await addButton(page, "Moodboard").hover();
  await shot(page, "kan111-3-barre-etroite");
  // « Ranger » reste un clic.
  const ranger = bar.getByRole("button", { name: "Ranger" });
  await expect(ranger).toBeVisible();

  // Le zoom tient en deux lignes à chaque palier : « − », le palier et « + » sur la première,
  // « Tout voir » dessous (un palier long renvoyait « + » sur une troisième ligne).
  const group = page.getByRole("group", { name: "Zoom de l'espace de travail" });
  const out = group.getByRole("button", { name: "Dézoomer" });
  for (let k = 0; k < 5; k++) {
    const minus = (await out.boundingBox())!;
    const plus = (await group
      .getByRole("button", { name: "Zoomer", exact: true })
      .boundingBox())!;
    const all = (await group.getByRole("button", { name: /Tout voir/ }).boundingBox())!;
    expect(Math.abs(plus.y - minus.y)).toBeLessThan(2);
    expect(all.y).toBeGreaterThanOrEqual(plus.y + plus.height - 1);
    expect(apart((await bar.boundingBox())!, (await group.boundingBox())!)).toBe(true);
    await out.click();
  }
});
