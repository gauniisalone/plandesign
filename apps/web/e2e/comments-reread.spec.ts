import { expect, test, type Page } from "@playwright/test";
import { createEmptyProject, goSpace } from "./helpers";

/** KAN-68, mode local (sans comptes, pas de WebSocket) : les commentaires se relisent comme
 *  la cloche. Un commentaire posé ailleurs (ici par l'API) apparaît au focus de la fenêtre,
 *  au retour sur l'onglet et au plus tard en 15 s, sans rechargement ; après un commentaire
 *  écrit depuis l'onglet, l'Activité se relit. */

async function postByApi(page: Page, id: string, text: string, x: number) {
  const r = await page.request.post(`/api/projects/${id}/comments`, {
    data: { target: { at: { x, y: -400 } }, text },
  });
  expect(r.status()).toBe(201);
}

test("KAN-68 local : focus, retour sur l'onglet, 15 s, puis l'Activité après mon commentaire", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const id = await createEmptyProject(page, "Relecture des commentaires");
  await goSpace(page, "board");
  const counter = (n: number) =>
    page.getByRole("button", {
      name: `Commentaires, ${n} ouvert${n > 1 ? "s" : ""}`,
      exact: true,
    });
  await expect(counter(0)).toBeVisible();

  // Must have 1 et 3 : posé ailleurs, puis la fenêtre reprend le focus : à jour, sans
  // rechargement (bien avant les 15 s).
  await postByApi(page, id, "Posé ailleurs", 0);
  await page.waitForTimeout(500);
  await expect(counter(0)).toBeVisible(); // rien ne se relit tout seul en 0,5 s
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(counter(1)).toBeVisible({ timeout: 3_000 });

  // Retour sur l'onglet.
  await postByApi(page, id, "Posé ailleurs, encore", 200);
  await page.evaluate(() =>
    document.dispatchEvent(new Event("visibilitychange")),
  );
  await expect(counter(2)).toBeVisible({ timeout: 3_000 });

  // Toutes les 15 s, sans aucun geste.
  await postByApi(page, id, "Posé ailleurs, sans geste", 400);
  await expect(counter(3)).toBeVisible({ timeout: 17_000 });

  // Must have 2 : un commentaire écrit depuis l'onglet ; l'Activité ouverte se relit.
  await page
    .locator(".topbar")
    .getByRole("button", { name: "Activité", exact: true })
    .click();
  const activity = page.getByRole("region", { name: "Activité", exact: true });
  const commented = activity.getByRole("button", { name: /a commenté/ });
  await expect(commented).toHaveCount(3, { timeout: 10_000 });
  for (let i = 0; i < 3; i++) await page.keyboard.press("-");
  const box = (await page.locator(".board-stage").boundingBox())!;
  await page.keyboard.press("c");
  await page.mouse.click(box.x + box.width - 140, box.y + box.height * 0.3);
  const draft = page.getByRole("dialog", { name: "Nouveau commentaire" });
  await draft.getByLabel("Votre commentaire").fill("Écrit depuis l'onglet");
  await draft.getByRole("button", { name: "Publier" }).click();
  await expect(counter(4)).toBeVisible();
  await expect(commented).toHaveCount(4, { timeout: 5_000 });
});
