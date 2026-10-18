import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";

/** Retouches b2 des barres compactes : état des commandes 3D en plan seul, nom de pièce qui
 *  évite l'escalier, champs lisibles dans un panneau de 300 px, thème Clair / Sombre /
 *  Système retenu par le navigateur. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 600],
    [700, 600],
    [700, 350],
    [300, 350],
    [300, 600],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.keyboard.press("v");
  await page.keyboard.press("p");
  await expect(page.locator("text.room-label")).toHaveCount(1);
}

const button = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true });

const overlap = (
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
) =>
  a.x < b.x + b.width &&
  b.x < a.x + a.width &&
  a.y < b.y + b.height &&
  b.y < a.y + a.height;

test("b2 : commandes 3D en plan seul, nom de pièce hors de l'escalier, champs lisibles", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await createRoom(page, "E2E retouches b2");

  // 1) En plan seul, les commandes de la 3D sont en retrait ; « Masquer le toit » dit vrai.
  await expect(page.locator(".viewbar")).toHaveClass(/plan-only/);
  const roof = button(page, "Masquer le toit");
  await expect(roof).toHaveAttribute("aria-pressed", "true");
  await expect(roof).toHaveAttribute("title", /^Masquer le toit \(R\) : actif/);
  for (const name of ["Rayons X", "Maison de poupée", "Plafonds", "Matières"])
    await expect(button(page, name)).toHaveAttribute("aria-pressed", "false");
  await page.keyboard.press("r");
  await expect(roof).toHaveAttribute("title", /: inactif/);
  await expect(page.locator(".viewbar")).not.toHaveClass(/plan-only/);
  await button(page, "Plan").click();

  // 2) Un escalier posé au centre de la pièce : le nom de la pièce s'en écarte.
  await page.keyboard.press("e");
  await page.locator(".stairs-cards button").first().click();
  await page.locator("svg.plan").click({ position: { x: 500, y: 475 } });
  await expect(page.locator(".stairs-outline")).toHaveCount(1);
  await page.keyboard.press("Escape");
  const stairs = (await page.locator(".stairs-outline").boundingBox())!;
  for (const sel of ["text.room-label", "text.room-area"]) {
    const label = (await page.locator(sel).boundingBox())!;
    expect(overlap(label, stairs), `${sel} sur l'escalier`).toBe(false);
  }
  await page.screenshot({ path: path.join(SHOTS, "b2_piece_escalier.png") });

  // 3) Panneau de 300 px : libellés au-dessus, listes déroulantes sur toute la largeur.
  await page.keyboard.press(",");
  const select = page.locator(".side-card .field select").first();
  await expect(select).toBeVisible();
  const width = (await select.boundingBox())!.width;
  expect(width).toBeGreaterThan(200);
  await page.screenshot({ path: path.join(SHOTS, "b2_champs_300.png") });
});

test("b2 : thème Clair / Sombre / Système, retenu par le navigateur", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await createRoom(page, "E2E thème choisi");
  const html = page.locator("html");
  const canvasLight = () =>
    page
      .locator(".canvas-wrap")
      .evaluate((el) => getComputedStyle(el).backgroundColor);
  const light = await canvasLight();
  await page.keyboard.press(",");
  await expect(button(page, "Système")).toHaveAttribute("aria-pressed", "true");

  await button(page, "Sombre").click();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await expect.poll(canvasLight).not.toBe(light);
  await page.reload();
  await expect(html).toHaveAttribute("data-theme", "dark");
  await page.screenshot({ path: path.join(SHOTS, "b2_theme_sombre.png") });

  // Clair choisi : reste clair même si le système passe en sombre. La touche ne part qu'une
  // fois l'éditeur affiché (l'application lit d'abord ce que le serveur sait faire).
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator(".topbar")).toBeVisible({ timeout: 30_000 });
  await page.keyboard.press(",");
  await button(page, "Clair").click();
  await expect(html).toHaveAttribute("data-theme", "light");
  await expect.poll(canvasLight).toBe(light);

  // Système : l'attribut disparaît, le thème suit l'ordinateur (ici sombre).
  await button(page, "Système").click();
  await expect(html).not.toHaveAttribute("data-theme", /.+/);
  await expect.poll(canvasLight).not.toBe(light);
  await page.reload();
  await expect(html).not.toHaveAttribute("data-theme", /.+/);
});
