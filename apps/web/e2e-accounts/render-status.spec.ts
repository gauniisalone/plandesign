import { expect, test, type BrowserContext, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-134 : le node Rendus ne reste plus vide. Le statut des rendus (GET
 *  /api/realistic/status) en échec se dit « Statut des rendus indisponible, nouvel essai… »
 *  (et non « bientôt en ligne »), la ligne reste visible au dézoom, et le statut se relit au
 *  retour sur l'onglet : « Rendre ▾ » apparaît sans recharger la page. Le statut et
 *  /api/health sont simulés (comme en production : ni Blender ni poste déclaré dans health).
 *  Adresse 198.51.100.113, propre à ce fichier. */

const STATUS_DOWN = "Statut des rendus indisponible, nouvel essai…";
const opened: BrowserContext[] = [];
test.afterEach(async () => {
  for (const context of opened.splice(0)) await context.close();
});

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByLabel("J'accepte les conditions d'utilisation").check();
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
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  const field = page.getByLabel("Votre nom (facultatif)");
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(field.or(home)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible())
    await page.getByRole("button", { name: "Passer" }).click();
  await expect(home).toBeVisible();
}

test("KAN-134 : statut en échec puis rétabli au retour sur l'onglet : « Rendre ▾ » apparaît, la ligne reste au dézoom", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": "198.51.100.113" },
  });
  opened.push(context);
  const page = await context.newPage();
  // Comme la production : ni Blender ni poste déclaré dans health.
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, json: { ...(await response.json()), blender: false } });
  });
  let up = false;
  let asked = 0;
  await page.route("**/api/realistic/status", async (route) => {
    asked += 1;
    if (!up) return route.fulfill({ status: 500, json: { detail: "redémarrage" } });
    return route.fulfill({
      json: { allowed: true, worker: "online", quota: 20, used_today: 0 },
    });
  });

  await signIn(page, `kan134-${Date.now().toString(36)}@example.org`);
  await page.getByRole("button", { name: "Nouveau projet" }).click();
  await expect(page).toHaveURL(/#\/projects\//);
  const renders = page.getByRole("region", { name: "Rendus réalistes" }).first();
  await expect(renders.getByText(STATUS_DOWN)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Rendus réalistes : bientôt en ligne")).toHaveCount(0);

  // Dézoom jusqu'aux vignettes : la ligne du node Rendus reste (avant : masquée).
  const stage = page.locator(".board-stage");
  const zoomedOut = async () =>
    /tier-(thumbs|titles)/.test((await stage.getAttribute("class")) ?? "");
  for (let i = 0; i < 12 && !(await zoomedOut()); i++) await page.keyboard.press("-");
  await expect(stage).toHaveClass(/tier-(thumbs|titles)/);
  await expect(renders.getByText(STATUS_DOWN)).toBeVisible();

  // Le serveur répond de nouveau ; retour sur l'onglet : le statut est relu.
  up = true;
  const before = asked;
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect.poll(() => asked).toBeGreaterThan(before);
  await expect(renders.getByRole("button", { name: /^Rendre/ })).toBeVisible();
  await expect(renders.getByText(STATUS_DOWN)).toHaveCount(0);
});
