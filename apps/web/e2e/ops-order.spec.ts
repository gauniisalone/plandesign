import { expect, test, type Page } from "@playwright/test";
import { createEmptyProject } from "./helpers";
import { gotoApp } from "./app-ready";

/** KAN-54 : réseau lent et désordonné pendant le tracé. La réponse de la première opération
 *  du plan traîne (le serveur l'a déjà appliquée, seul le retour vers l'écran attend), celle
 *  de la seconde revient tout de suite. L'écran doit finir sur l'état de la dernière
 *  opération, et « Détecter les pièces » (bouton et touche P) rester indisponible tant
 *  qu'une opération est en vol. */

const FIRST_DELAY_MS = 2_000;

type Sent = { op: string };

/** Retarde la réponse de la première POST …/ops seulement ; journalise ce qui part. */
async function firstOpSlow(page: Page, sent: Sent[]): Promise<void> {
  let n = 0;
  await page.route("**/api/projects/*/levels/*/ops", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    sent.push(route.request().postDataJSON() as Sent);
    const first = n === 0;
    n += 1;
    const response = await route.fetch();
    if (first) await new Promise((r) => setTimeout(r, FIRST_DELAY_MS));
    await route.fulfill({ response });
  });
}

async function openPlan(page: Page, name: string) {
  await gotoApp(page, "/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  return svg;
}

/** Deux murs séparés, tracés vite : deux opérations l'une derrière l'autre. */
async function twoWalls(page: Page, svg: ReturnType<Page["locator"]>) {
  await page.keyboard.press("w");
  await svg.click({ position: { x: 300, y: 650 } });
  await svg.click({ position: { x: 800, y: 650 } });
  await page.keyboard.press("Escape");
  await svg.click({ position: { x: 300, y: 350 } });
  await svg.click({ position: { x: 800, y: 350 } });
  await page.keyboard.press("Escape");
}

test("KAN-54 (1) : réponses désordonnées, l'écran finit sur la dernière opération", async ({
  page,
}) => {
  const sent: Sent[] = [];
  const svg = await openPlan(page, "Ordre des opérations");
  await firstOpSlow(page, sent);
  await twoWalls(page, svg);
  await expect(page.locator("polygon.wall")).toHaveCount(2, {
    timeout: 10_000,
  });
  // La réponse en retard (un seul mur) est arrivée depuis : elle n'a rien écrasé.
  await page.waitForTimeout(FIRST_DELAY_MS + 1_000);
  await expect(page.locator("polygon.wall")).toHaveCount(2);
  expect(sent.map((s) => s.op)).toEqual(["add_wall", "add_wall"]);
});

test("KAN-54 (2) : « Détecter les pièces » et la touche P indisponibles tant qu'une opération est en vol", async ({
  page,
}) => {
  const sent: Sent[] = [];
  const svg = await openPlan(page, "Occupé pendant le vol");
  await firstOpSlow(page, sent);
  await twoWalls(page, svg);
  // La seconde opération serait revenue depuis longtemps ; la première est encore en vol.
  await page.waitForTimeout(600);
  const detect = page.getByRole("button", { name: "Détecter les pièces" });
  await expect(detect).toBeDisabled();
  await page.keyboard.press("p");
  await page.waitForTimeout(200);
  expect(sent.filter((s) => s.op === "detect_rooms")).toEqual([]);
  // Une fois tout revenu, le bouton se libère.
  await expect(detect).toBeEnabled({ timeout: 10_000 });
  await expect(page.locator("polygon.wall")).toHaveCount(2);
});
