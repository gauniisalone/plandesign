import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { createEmptyProject } from "./helpers";

/** Téléphone (4.9) : émulation tactile 412 × 915. Créer un projet, tracer une pièce au doigt
 *  avec l'outil Mur de la palette, aucun débordement horizontal, capture d'écran. */
test.use({
  viewport: { width: 412, height: 915 },
  hasTouch: true,
  isMobile: true,
});

const SHOTS = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  "screenshots",
);

test("téléphone : tracer une pièce au doigt, sans débordement", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "Vos tableaux",
    }),
  ).toBeVisible();
  await createEmptyProject(page, "Au doigt");
  await expect(page).toHaveURL(/\/import$/);
  await expect(
    page.getByRole("button", { name: "Prendre une photo" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  // Outil Mur depuis la palette (pas de clavier sur un téléphone), puis quatre touchers dans
  // la zone libre du plan ; le cinquième referme sur le premier sommet.
  await page.getByRole("button", { name: "Mur", exact: true }).click();
  const box = await svg.boundingBox();
  expect(box).not.toBeNull();
  if (!box) return;
  const corners: [number, number][] = [
    [60, 190],
    [300, 190],
    [300, 330],
    [60, 330],
  ];
  for (const [x, y] of corners)
    await page.touchscreen.tap(box.x + x, box.y + y);
  await page.touchscreen.tap(box.x + 60, box.y + 190);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(page.locator("circle.vertex")).toHaveCount(4);
  // La sélection au doigt d'un sommet le déplace (pointer events, pas de souris).
  await page.getByRole("button", { name: "Sélection", exact: true }).click();
  await page.screenshot({ path: path.join(SHOTS, "09-telephone.png") });
});
