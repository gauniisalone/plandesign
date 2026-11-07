import { expect, test, type Page } from "@playwright/test";
import {
  createEmptyProject,
  goSpace,
  showPanel,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-161 : bois incliné dans le plan 2D, sur des combles créés par l'API (6 × 5 m, faîtage
 *  3,20 m). Posé en deux clics (outil « Bois incliné » : pied, puis tête aimantée), glissé
 *  d'un bloc, Ctrl+Z ; sa section passée en ronde dans l'inspecteur, puis supprimé. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

type Pt = { x: number; y: number; z?: number };
type El = {
  type: string;
  start?: Pt;
  end?: Pt;
  metadata?: { brace?: { profile?: string; head_on?: string } };
};
type ProjectJson = { levels: Record<string, { elements: Record<string, El> }> };

const estimate = (value: number) => ({
  value,
  sigma: 0,
  source: "user_defined",
});

async function attic(page: Page): Promise<{ id: string; level: string }> {
  const id = await createEmptyProject(page, "Combles à jambes de force");
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

async function braces(page: Page, id: string, level: string) {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  return Object.values(project.levels[level]!.elements).filter(
    (e) => e.type === "beam",
  );
}

test("bois incliné en 2D : deux clics le posent, glisser le déplace, Ctrl+Z, section ronde, supprimé", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { id, level } = await attic(page);
  await page.getByRole("button", { name: "Bois incliné", exact: true }).click();
  const plan = (await page.locator("svg.plan").boundingBox())!;
  const cx = plan.x + plan.width / 2;
  const cy = plan.y + plan.height / 2;
  // Pied à 50 px du milieu des combles, tête au milieu : sous la faîtière ou le rampant.
  await page.mouse.click(cx - 50, cy - 40);
  await page.mouse.move(cx, cy, { steps: 4 });
  await expect(page.locator(".plan-brace.preview")).toHaveCount(1);
  await page.mouse.click(cx, cy);
  await expect.poll(async () => (await braces(page, id, level)).length).toBe(1);
  const placed = (await braces(page, id, level))[0]!;
  expect(placed.start!.z).toBe(0);
  expect(placed.end!.z!).toBeGreaterThan(1.0);
  expect(["ridge", "slope"]).toContain(placed.metadata?.brace?.head_on);
  const drawn = page.locator("[data-testid=plan-brace]");
  await expect(drawn).toHaveCount(1, { timeout: 30_000 });
  await expect(page.locator(".plan-brace-foot")).toHaveCount(1);

  // Glissé d'un bloc : pied et tête bougent ensemble, en un pas que Ctrl+Z défait.
  await page.getByRole("button", { name: "Sélection", exact: true }).click();
  const box = (await page.locator(".plan-brace-outline").boundingBox())!;
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 40, y, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () => (await braces(page, id, level))[0]!.start!.x)
    .not.toBe(placed.start!.x);
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await braces(page, id, level))[0]!.start)
    .toEqual(placed.start);

  // Inspecteur : section ronde, passage affiché, puis suppression.
  await drawn.click();
  await showPanel(page);
  await expect(page.locator(".inspector h2").first()).toContainText(
    "Bois incliné",
  );
  await expect(page.locator(".inspector")).toContainText("Passage sous le bois");
  await page
    .getByLabel("Section du bois", { exact: true })
    .selectOption("round");
  await expect
    .poll(
      async () =>
        (await braces(page, id, level))[0]!.metadata?.brace?.profile ?? "",
    )
    .toBe("round");
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: `${process.env.E2E_CAPTURE_DIR}/kan161-1-bois-incline-2d.png`,
    });
  await page.getByRole("button", { name: "Supprimer le bois incliné" }).click();
  await expect.poll(async () => (await braces(page, id, level)).length).toBe(0);
});
