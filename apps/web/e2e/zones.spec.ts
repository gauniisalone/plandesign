import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";

/** V1 (note 10, point 2) : « Brief par zones » dans Aménager, sans modèle : zones et kits de
 *  meubles d'après l'ambiance et la surface, puis des dispositions à comparer. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [250, 700],
    [850, 700],
    [850, 300],
    [250, 300],
    [250, 700],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
}

test("Aménager : brief par zones sans modèle, puis dispositions", async ({
  page,
}) => {
  await createRoom(page, "E2E brief par zones");
  await page.getByRole("button", { name: "Aménager", exact: true }).click();
  await page.getByRole("button", { name: "Brief par zones" }).click();
  const brief = page.locator("ul.list.brief li");
  await expect(brief.first()).toBeVisible();
  await expect(page.locator(".zone-notes")).toContainText("Brief par zones");
  await expect(page.locator("ul.list.brief")).toContainText("zone ");
  await page.screenshot({ path: path.join(SHOTS, "v1_brief_zones.png") });
  await page.getByRole("button", { name: "Proposer des dispositions" }).click();
  await expect(page.getByText(/meubles placés/).first()).toBeVisible({
    timeout: 30000,
  });
});
