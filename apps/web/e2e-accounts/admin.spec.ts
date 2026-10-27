import { expect, test, type Browser, type Page } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { gotoApp } from "../e2e/app-ready";

/** KAN-109 L1 : le back-office d'augus. L'administrateur voit l'audience du site, tirée d'un
 *  journal de test au format de Caddy (IP déjà tronquée, adresses de documentation), et la
 *  santé technique ; un autre membre n'a ni la page ni l'API. */

const LOG_DIR = path.resolve(".e2e-accounts/access-log");

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1280, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
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
  await expect(name.or(page.locator(".boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await name.isVisible()) {
    await name.fill(email.split("@")[0]!);
    await page.getByRole("button", { name: "Continuer" }).click();
  }
}

/** Une ligne comme celles de Caddy, l'IP déjà tronquée. */
function line(uri: string, agent: string, ip: string): string {
  return JSON.stringify({
    ts: new Date().toISOString(),
    request: {
      client_ip: ip,
      remote_ip: ip,
      method: "GET",
      host: "plandesign.fr",
      uri,
      headers: { "User-Agent": [agent], Referer: ["https://example.org/x"] },
    },
    status: 200,
  });
}

test("KAN-109 : l'administrateur voit l'audience et la santé ; un membre, non", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  mkdirSync(LOG_DIR, { recursive: true });
  writeFileSync(
    path.join(LOG_DIR, "site.log"),
    [
      line("/", "Mozilla/5.0 (X11; Linux x86_64) Firefox/131.0", "203.0.113.0"),
      line("/tarifs", "Mozilla/5.0 (iPhone) Mobile Safari", "198.51.100.0"),
      line("/", "Mozilla/5.0 (compatible; Googlebot/2.1)", "192.0.2.0"),
    ].join("\n") + "\n",
  );

  const admin = await person(browser, "198.51.100.191");
  await signIn(admin, "admin@example.org");
  await gotoApp(admin, "/#/admin");
  const audience = admin.getByRole("region", { name: "Audience du site" });
  // Le journal est lu chaque seconde par ce serveur de test (5 min en production).
  await expect
    .poll(
      async () => {
        await admin.reload();
        return (await audience.textContent()) ?? "";
      },
      { timeout: 30_000 },
    )
    .toMatch(/Vues\s*2/);
  await expect(audience).toContainText("Depuis le");
  await expect(audience).toContainText("Robots écartés1");
  await expect(audience).toContainText("example.org");
  await expect(audience).toContainText("/tarifs");
  const health = admin.getByRole("region", { name: "Santé technique" });
  await expect(health).toContainText("Version déployée");
  await expect(health).toContainText("Poste de rendu");
  await shot(admin, "kan109-back-office");

  // Un membre qui n'est pas administrateur : ni la page, ni l'API.
  const marie = await person(browser, "198.51.100.192");
  await signIn(marie, "marie.kan109@example.org");
  await gotoApp(marie, "/#/admin");
  await expect(marie.getByRole("alert")).toHaveText(
    "Réservé à l'administrateur.",
  );
  expect((await marie.request.get("/api/admin/health")).status()).toBe(403);
  expect((await marie.request.get("/api/admin/audience")).status()).toBe(403);
});
