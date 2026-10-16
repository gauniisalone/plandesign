import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, goSpace } from "./helpers";

/** V1 (note 10, point 4) : vues automatiques posées en un clic dans Rendre, format 4:5,
 *  vues manquées expliquées, annulables ; « Rendre toutes les vues » proposé. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<string> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 650],
    [800, 650],
    [800, 350],
    [300, 350],
    [300, 650],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  return /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
}

type Cam = { type: string; name?: string; metadata?: Record<string, unknown> };

async function autoViews(page: Page, id: string): Promise<Cam[]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as { levels: Record<string, { elements: Record<string, Cam> }> };
  return Object.values(Object.values(project.levels)[0]!.elements).filter(
    (e) => e.type === "camera" && e.metadata?.auto_view,
  );
}

test("vues automatiques : un clic, format 4:5, vues de secours distinctes, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E vues automatiques");
  await goSpace(page, "renders");
  const section = page.locator("section.auto-views");
  await expect(section).toBeVisible();
  await expect(
    section.getByRole("button", { name: "Rendre toutes les vues" }),
  ).toBeVisible();
  await section.getByLabel("Format des vues").selectOption("4:5");
  await section.getByRole("button", { name: "Poser les 3 vues" }).click();

  // Pièce vide, sans porte : la vue d'ensemble est posée (dans l'axe) ; rien à cadrer pour la
  // zone ni le détail, deux vues de secours distinctes prennent leur place (toujours 3 vues).
  await expect(page.locator(".viewpoints")).toContainText("Vue d'ensemble");
  await expect(page.locator(".viewpoints")).toContainText(
    /Contrechamp|Vers la lumière|Autre angle/,
  );
  await expect.poll(async () => (await autoViews(page, id)).length).toBe(3);
  for (const cam of await autoViews(page, id))
    expect(cam.metadata!.aspect).toBe("4:5");
  await expect(page.locator(".viewpoints")).toContainText("vue automatique");
  await expect(
    section.getByRole("button", { name: "Reposer les vues" }),
  ).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "v1_vues_automatiques.png") });

  // Reposer ne double pas ; Annuler retire les vues posées.
  await section.getByRole("button", { name: "Reposer les vues" }).click();
  await expect.poll(async () => (await autoViews(page, id)).length).toBe(3);
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await autoViews(page, id)).length).toBe(0);
});
