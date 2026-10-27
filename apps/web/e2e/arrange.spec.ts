import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import path from "node:path";
import { createEmptyProject, goSpace, waitNoHeavyJob } from "./helpers";
import { readItems as read, toLegacy } from "./legacy-board";

/** KAN-100 L1 : « Ranger » remet un espace posé à l'ancienne (nodes de 624, Moodboard dans
 *  la colonne de gauche) à la pose de KAN-98 L2, en un seul geste : un Ctrl+Z rend l'état
 *  d'origine à l'identique (places ET tailles) ; une fois rangé, le bouton se grise. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** Les places et tailles de tout l'espace, au centième. */
const snapshot = async (request: APIRequestContext, id: string) =>
  Object.fromEntries(
    (await read(request, id))
      .map((i) => [
        i.id,
        [i.x, i.y, i.w, i.h].map((v) => Math.round(v * 100) / 100),
      ])
      .sort(([a], [b]) => String(a).localeCompare(String(b))),
  );

async function shot(page: Page, name: string) {
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, `${name}.png`),
    });
}

test("KAN-100 : Ranger, Ctrl+Z rend l'état d'origine à l'identique, grisé une fois rangé", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const id = await createEmptyProject(page, "Ranger");
  await goSpace(page, "board");
  await toLegacy(page.request, id, {
    Origin: new URL(page.url()).origin,
  });
  await page.reload();
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(board.locator(".board-item.kind-plan")).toBeVisible({
    timeout: 30_000,
  });
  const ranger = page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Ranger" });
  await expect(ranger).toBeEnabled({ timeout: 30_000 });
  const before = await snapshot(page.request, id);
  await page.keyboard.press("Shift+Digit1");
  await shot(page, "kan100-avant");

  // Ranger : nodes à 780, Moodboard au-dessus de Rendus dans sa colonne, rangée Photos →
  // Plan → Rendus ; un seul geste.
  await ranger.click();
  await expect(ranger).toBeDisabled();
  const node = async (role: string) =>
    (await read(page.request, id)).find(
      (i) => i.kind === "frame" && i.role === role,
    )!;
  await expect.poll(async () => (await node("photos")).w).toBe(780);
  const [photos, mood, renders] = [
    await node("photos"),
    await node("inspirations"),
    await node("renders"),
  ];
  const plan = (await read(page.request, id)).find((i) => i.kind === "plan")!;
  expect(mood.w).toBe(780);
  expect(renders.w).toBe(780);
  expect(mood.x).toBe(renders.x);
  expect(renders.y - (mood.y + mood.h)).toBe(160);
  expect(photos.y).toBe(plan.y);
  expect(renders.y).toBe(plan.y);
  expect(plan.x - (photos.x + photos.w)).toBe(240);
  expect(renders.x - (plan.x + plan.w)).toBe(240);
  await page.keyboard.press("Shift+Digit1");
  await shot(page, "kan100-apres");

  // Un seul Ctrl+Z : l'état d'origine à l'identique, places et tailles.
  await page.keyboard.press("Control+z");
  await expect
    .poll(() => snapshot(page.request, id), { timeout: 10_000 })
    .toEqual(before);
  await expect(ranger).toBeEnabled();

  // Rangé de nouveau, puis grisé : ranger un espace rangé ne change rien.
  await ranger.click();
  await expect(ranger).toBeDisabled();
  await expect.poll(async () => (await node("photos")).w).toBe(780);
  await page.reload();
  await expect(ranger).toBeDisabled({ timeout: 30_000 });
});
