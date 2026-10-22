import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-91, avec les comptes : un lien coupé par une éditrice disparaît en direct chez le
 *  propriétaire (sans recharger), et le propriétaire peut couper un lien qu'elle a retendu
 *  (D3 : tous les éditeurs du projet) ; le lecteur voit les liens et leur phrase, sans poignée
 *  ni « Couper ». Chaque personne a sa propre IP ; les adresses sont uniques à chaque exécution
 *  (le test se rejoue dans une même base). */

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function signIn(page: Page, email: string, path = "/"): Promise<void> {
  await gotoApp(page, path);
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const name = page.getByLabel("Votre nom (facultatif)");
  await expect(name.or(page.locator(".editor, .boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await name.isVisible()) {
    await name.fill(email.split("-")[0].replace(/^./, (c) => c.toUpperCase()));
    await page.getByRole("button", { name: "Continuer" }).click();
  }
}

test("KAN-91 : un lien coupé arrive en direct, tous les éditeurs le modifient, le lecteur le lit", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const run = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const rep = test.info().repeatEachIndex;
  const ip = (k: number) =>
    rep === 0 ? `198.51.100.${240 + k}` : `203.0.113.${100 + 10 * rep + k}`;
  const gilles = await person(browser, ip(1));
  await signIn(gilles, `gilles-${run}@example.org`);
  const origin = { Origin: new URL(gilles.url()).origin };
  const made = await gilles.request.post("/api/projects", {
    data: { name: "Cartes reliées" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invite = async (role: string) =>
    (
      (await (
        await gilles.request.post(`/api/projects/${id}/invitations`, {
          data: { role },
          headers: origin,
        })
      ).json()) as { token: string }
    ).token;
  const [asEditor, asViewer] = [await invite("editor"), await invite("viewer")];
  const fanny = await person(browser, ip(2));
  await signIn(fanny, `fanny-${run}@example.org`, `/#/invitation/${asEditor}`);
  const kim = await person(browser, ip(3));
  await signIn(kim, `kim-${run}@example.org`, `/#/invitation/${asViewer}`);
  await gotoApp(gilles, `/#/projects/${id}`);
  const wire = (p: Page, kind: string) =>
    p.locator(`.board-wire[data-kind=${kind}]`);
  for (const p of [gilles, fanny, kim])
    await expect(wire(p, "guides_style")).toHaveCount(1, { timeout: 30_000 });

  // Fanny, éditrice, coupe le lien posé automatiquement ; Gilles le voit partir en direct.
  await wire(fanny, "guides_style")
    .locator(".board-wire-badge")
    .dispatchEvent("pointerdown");
  await fanny
    .getByRole("toolbar", { name: "Lien choisi" })
    .getByRole("button", { name: "Couper" })
    .click();
  await expect(wire(fanny, "guides_style")).toHaveCount(0);
  await expect(wire(gilles, "guides_style")).toHaveCount(0, { timeout: 5_000 });
  // Elle annule : le lien revient, tiré à la main cette fois ; Gilles peut le couper (D3).
  await fanny
    .locator(".board-wire-toast")
    .getByRole("button", { name: "Annuler" })
    .click();
  await expect(wire(gilles, "guides_style")).toHaveCount(1, { timeout: 5_000 });
  await wire(gilles, "guides_style")
    .locator(".board-wire-badge")
    .dispatchEvent("pointerdown");
  await gilles
    .getByRole("toolbar", { name: "Lien choisi" })
    .getByRole("button", { name: "Couper" })
    .click();
  await expect(wire(fanny, "guides_style")).toHaveCount(0, { timeout: 5_000 });

  // Kim, lecteur : les liens et leur phrase, sans poignée ni « Couper ».
  await expect(wire(kim, "renders_view")).toHaveCount(1);
  await expect(kim.locator(".board-wire-handle")).toHaveCount(0);
  await wire(kim, "renders_view")
    .locator(".board-wire-badge")
    .dispatchEvent("pointerdown");
  const bar = kim.getByRole("toolbar", { name: "Lien choisi" });
  await expect(bar).toContainText("donne la géométrie de");
  await expect(bar.getByRole("button", { name: "Couper" })).toHaveCount(0);
});
