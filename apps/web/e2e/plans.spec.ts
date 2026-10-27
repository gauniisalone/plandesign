import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";
import { gotoApp } from "./app-ready";

/** T7e : plusieurs plans par espace de travail. « + Nouveau plan » : une petite fenêtre à
 *  deux champs (nom libre, sorte : « Variante de ce plan », « Nouvel étage », « Plan vide ») ;
 *  le plan créé s'ouvre ; une rangée Plan + Rendus par plan ; Ctrl+Z le retire. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<string> {
  await gotoApp(page, "/");
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

type Lv = {
  id: string;
  name: string;
  variant_of: string | null;
  elevation: number;
};

async function levels(page: Page, id: string): Promise<Lv[]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, Lv>;
  };
  return Object.values(project.levels);
}

test("T7e : + Nouveau plan (variante, étage), une rangée par plan, annuler", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.emulateMedia({ colorScheme: "dark" });
  const id = await createRoom(page, "E2E plusieurs plans");
  await goSpace(page, "board");
  const nav = page.getByRole("navigation", { name: "Espaces du projet" });
  await expect(nav.locator(".space-name")).toHaveText("Espace de travail");

  // KAN-98 : « Plan » est dans la barre d'ajout, fixe en bas au centre (plus de bouton
  // flottant sous les rangées), et toujours pas dans la barre du haut.
  await expect(
    page.locator(".topbar").getByRole("button", { name: /Nouveau plan/ }),
  ).toHaveCount(0);
  const open = page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Plan", exact: true });
  await expect(open).toBeVisible();
  // La petite fenêtre : deux champs, les trois sortes avec les mots d'augus, rien d'autre.
  await open.click();
  const dialog = page.getByRole("dialog", { name: "Nouveau plan" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Nom")).toBeFocused();
  await expect(dialog.getByLabel("Sorte").locator("option")).toHaveText([
    "Variante de ce plan",
    "Nouvel étage",
    "Plan vide",
  ]);
  await expect(dialog.locator("p")).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "t7e_nouveau_plan.png") });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  // « Variante de ce plan » : une copie, qui s'ouvre (le nom dit à côté de « Plan »).
  await open.click();
  await dialog.getByLabel("Nom").fill("Variante bureau");
  await dialog.getByRole("button", { name: "Créer" }).click();
  await expect(dialog).toHaveCount(0);
  await expectSpace(page, "plan");
  await expect(nav.locator(".space-name")).toHaveText("Plan · Variante bureau");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  let all = await levels(page, id);
  expect(all).toHaveLength(2);
  const [base, variant] = all;
  expect(variant!.variant_of).toBe(base!.id);

  // L'espace de travail : une rangée Plan + Rendus par plan.
  await goSpace(page, "board");
  await expect(page.locator(".board-item.kind-plan")).toHaveCount(2);
  const frames = page.locator(".board-item.kind-frame.role-renders");
  await expect(frames).toHaveCount(2);
  await expect(
    frames.filter({ hasText: "Rendus · Variante bureau" }),
  ).toHaveCount(1);
  await expect(
    frames.filter({ hasText: `Rendus · ${base!.name}` }),
  ).toHaveCount(1);
  // KAN-98 : la barre d'ajout ne bouge pas avec les rangées.
  await expect(open).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "t7e_rangees.png") });
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: path.join(process.env.E2E_CAPTURE_DIR, "nouveau-plan-rangees.png"),
    });

  // Ouvrir le premier plan depuis son cadre.
  await page
    .locator(".board-item.kind-plan", { hasText: base!.name })
    .getByRole("button", { name: "Modifier le plan" })
    .click();
  await expectSpace(page, "plan");
  await expect(nav.locator(".space-name")).toHaveText(`Plan · ${base!.name}`);

  // « Nouvel étage » : vide, au-dessus ; puis Ctrl+Z le retire et rouvre le premier plan.
  await goSpace(page, "board");
  await open.click();
  await dialog.getByLabel("Nom").fill("Étage");
  await dialog.getByLabel("Sorte").selectOption({ label: "Nouvel étage" });
  await dialog.getByRole("button", { name: "Créer" }).click();
  await expectSpace(page, "plan");
  await expect(nav.locator(".space-name")).toHaveText("Plan · Étage");
  await expect(page.locator("polygon.wall")).toHaveCount(0);
  all = await levels(page, id);
  const storey = all.find((l) => l.name === "Étage")!;
  expect(storey.variant_of).toBeNull();
  expect(storey.elevation).toBeGreaterThan(2);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await levels(page, id)).length).toBe(2);
  await expect(nav.locator(".space-name")).toHaveText(`Plan · ${base!.name}`);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await goSpace(page, "board");
  await expect(page.locator(".board-item.kind-plan")).toHaveCount(2);
});
