import { expect, test, type Page } from "@playwright/test";
import { createEmptyProject } from "./helpers";
import { gotoApp } from "./app-ready";

/** KAN-53 : « Détecter les pièces » juste après la fermeture du contour, serveur lent. Les
 *  réponses de POST …/ops sont retardées de 300 ms (le serveur a déjà appliqué l'opération :
 *  seul le retour vers l'écran traîne, comme sous charge). Quatre murs tracés au clic, puis
 *  « Détecter les pièces » : une pièce attendue, dix fois de suite. Le test journalise ce qui
 *  part (fin du mur qui ferme : sommet ou point, et ses coordonnées) pour l'analyse. */

const OPS_DELAY_MS = 300;

type Sent = { op: string; start?: unknown; end?: unknown };

async function slowOps(page: Page, sent: Sent[]): Promise<void> {
  await page.route("**/api/projects/*/levels/*/ops", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    sent.push(route.request().postDataJSON() as Sent);
    const response = await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, OPS_DELAY_MS));
    await route.fulfill({ response });
  });
}

for (let n = 1; n <= 10; n++) {
  test(`KAN-53 : contour fermé puis « Détecter les pièces », /ops lent (${n}/10)`, async ({
    page,
  }) => {
    const sent: Sent[] = [];
    await gotoApp(page, "/");
    await createEmptyProject(page, `Détection sous charge ${n}`);
    await page.getByRole("button", { name: "Continuer vers le plan" }).click();
    const svg = page.locator("svg.plan");
    await expect(svg).toBeVisible();
    await slowOps(page, sent);
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
    try {
      await expect(page.locator("text.room-label")).toHaveCount(1);
    } finally {
      // Ce qui est parti, dans l'ordre (visible dans le rapport en cas d'échec).
      test.info().annotations.push({
        type: "ops envoyées",
        description: JSON.stringify(sent),
      });
      console.log(`KAN-53 ${n} ops envoyées : ${JSON.stringify(sent)}`);
    }
  });
}

test("KAN-53 : sans retard, le contour fermé donne toujours la pièce (rien ne change)", async ({
  page,
}) => {
  await gotoApp(page, "/");
  await createEmptyProject(page, "Détection sans retard");
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
});
