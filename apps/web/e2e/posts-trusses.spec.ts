import { expect, test, type Page } from "@playwright/test";
import {
  createEmptyProject,
  goSpace,
  showPanel,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-94, lot L3 : poteaux et fermes intermédiaires dans le plan 2D, sur des combles créés
 *  par l'API (6 × 5 m, faîtage 3,20 m). Un poteau posé d'un clic (outil « Poteau »), glissé,
 *  Ctrl+Z ; une ferme ajoutée par son bouton, glissée le long du faîtage seulement, son
 *  écartement réglé dans l'inspecteur. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

type Pt = { x: number; y: number };
type El = { type: string; position?: Pt; spacing?: number };
type ProjectJson = { levels: Record<string, { elements: Record<string, El> }> };

const estimate = (value: number) => ({
  value,
  sigma: 0,
  source: "user_defined",
});

async function attic(page: Page): Promise<{ id: string; level: string }> {
  const id = await createEmptyProject(page, "Combles à fermes");
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  const level = Object.keys(project.levels)[0]!;
  const made = await page.request.post(
    `/api/projects/${id}/levels/${level}/attic`,
    {
      headers: { Origin: new URL(page.url()).origin },
      data: {
        length: estimate(6.0),
        width: estimate(5.0),
        ridge_height: estimate(3.2),
        eave_height: estimate(1.0),
      },
    },
  );
  expect(made.status(), await made.text()).toBe(200);
  await page.reload();
  await goSpace(page, "plan");
  return { id, level };
}

async function elements(page: Page, id: string, level: string, type: string) {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  return Object.entries(project.levels[level]!.elements).filter(
    ([, e]) => e.type === type,
  );
}

async function drag(page: Page, selector: string, dx: number, dy: number) {
  const box = (await page.locator(selector).first().boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + dx, y + dy, { steps: 8 });
  await page.mouse.up();
}

test("poteau en 2D : un clic le pose, glisser le déplace, Ctrl+Z le remet", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { id, level } = await attic(page);
  await page.getByRole("button", { name: "Poteau", exact: true }).click();
  const plan = (await page.locator("svg.plan").boundingBox())!;
  // Le milieu du plan : dans les combles (la vue est cadrée sur le niveau).
  await page.mouse.click(plan.x + plan.width / 2, plan.y + plan.height / 2);
  await expect
    .poll(async () => (await elements(page, id, level, "post")).length)
    .toBe(1);
  const post = page.locator("polygon.plan-post");
  await expect(post).toHaveCount(1, { timeout: 30_000 });
  const before = (await elements(page, id, level, "post"))[0]![1].position!;

  await page.getByRole("button", { name: "Sélection", exact: true }).click();
  await drag(page, "polygon.plan-post", 50, 0);
  await expect
    .poll(
      async () => (await elements(page, id, level, "post"))[0]![1].position!.x,
    )
    .not.toBe(before.x);
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await elements(page, id, level, "post"))[0]![1].position)
    .toEqual(before);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: `${process.env.E2E_CAPTURE_DIR}/kan94-1-poteau-2d.png`,
    });
});

test("ferme intermédiaire en 2D : ajoutée, glissée le long du faîtage, écartement réglé", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { id, level } = await attic(page);
  await page
    .getByRole("button", { name: "Ajouter une ferme intermédiaire" })
    .click();
  await expect
    .poll(async () => (await elements(page, id, level, "truss")).length)
    .toBe(1);
  await expect(page.locator(".plan-truss .plan-beam")).toHaveCount(1, {
    timeout: 30_000,
  });
  await expect(page.locator(".plan-truss .plan-post")).toHaveCount(2);
  const derived = (await (
    await page.request.get(`/api/projects/${id}/levels/${level}/derived`)
  ).json()) as { ridge: { start: Pt; end: Pt } };
  const { start, end } = derived.ridge;
  const before = (await elements(page, id, level, "truss"))[0]![1].position!;

  // Un glissé en biais : seule la part le long du faîtage compte.
  await drag(page, ".plan-truss .plan-beam", 60, 40);
  await expect
    .poll(async () => {
      const p = (await elements(page, id, level, "truss"))[0]![1].position!;
      return Math.hypot(p.x - before.x, p.y - before.y) > 0.05;
    })
    .toBe(true);
  const after = (await elements(page, id, level, "truss"))[0]![1].position!;
  const ux = end.x - start.x;
  const uy = end.y - start.y;
  const across =
    Math.abs((after.x - start.x) * uy - (after.y - start.y) * ux) /
    Math.hypot(ux, uy);
  expect(across).toBeLessThan(0.01);

  // Inspecteur (panneau fermé par défaut) : l'écartement des poteaux.
  await page.locator(".plan-truss .plan-beam").click();
  await showPanel(page);
  await expect(page.locator(".inspector h2").first()).toContainText(
    "Ferme intermédiaire",
  );
  const spacing = page.getByLabel("Écartement des poteaux");
  await spacing.fill("1.4");
  await spacing.press("Enter");
  await expect
    .poll(async () => (await elements(page, id, level, "truss"))[0]![1].spacing)
    .toBe(1.4);
  await expect(page.locator(".plan-truss-label")).toContainText("poutre à");
  // Les pignons sans nom se distinguent par leur côté (deux « Pignon » pareils, avant).
  const options = await page
    .getByLabel("Pignon de référence")
    .locator("option")
    .allTextContents();
  expect(new Set(options).size).toBe(2);
  if (process.env.E2E_CAPTURE_DIR) {
    await page.screenshot({
      path: `${process.env.E2E_CAPTURE_DIR}/kan94-2-ferme-2d.png`,
    });
    // Capture 3D (rapport L3) : la ferme et un poteau en bois sous la charpente.
    await page.getByRole("button", { name: "3D", exact: true }).click();
    await page.locator("canvas").first().waitFor({ state: "visible" });
    await page.waitForTimeout(4000);
    await page.screenshot({
      path: `${process.env.E2E_CAPTURE_DIR}/kan94-3-ferme-3d.png`,
    });
  }
});
