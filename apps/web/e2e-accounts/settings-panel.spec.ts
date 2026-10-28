import { expect, test } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { expectHomeSettingsWhole, SETTINGS_SIZES } from "./settings-check";

/** KAN-110 : avec les comptes, les Réglages de l'accueil d'un compte ordinaire s'ouvrent
 *  entiers (sans « Back-office »). Le cas de l'administrateur est dans admin.spec, sur sa
 *  session déjà ouverte : le serveur n'envoie que 3 codes par adresse et par 15 min, et
 *  l'adresse de l'administrateur en a déjà demandé 3 dans la suite. */

const EMAIL = "marie.kan110@example.org";

test("KAN-110 : Réglages de l'accueil entiers (compte ordinaire)", async ({
  browser,
}) => {
  test.setTimeout(90_000);
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: SETTINGS_SIZES[0],
    extraHTTPHeaders: { "X-Forwarded-For": "198.51.100.193" },
  });
  const page = await context.newPage();
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(EMAIL);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(EMAIL)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const name = page.getByLabel("Votre nom (facultatif)");
  await expect(name.or(page.locator(".boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await name.isVisible()) {
    await name.fill("marie");
    await page.getByRole("button", { name: "Continuer" }).click();
  }
  await expect(page.locator(".boards")).toBeVisible();
  await expectHomeSettingsWhole(page, false);
  await context.close();
});
