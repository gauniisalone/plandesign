import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-80 : juste après la connexion, un seul clic sur « Nouveau projet » crée le projet et
 *  ouvre son espace de travail (il fallait parfois cliquer deux fois : la page se rechargeait
 *  d'elle-même et le premier clic se perdait). */

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
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
  const field = page.getByLabel("Votre nom (facultatif)");
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(field.or(home)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible())
    await page.getByRole("button", { name: "Passer" }).click();
  await expect(home).toBeVisible();
}

test("KAN-80 : connexion puis un seul clic sur « Nouveau projet » = projet créé et ouvert", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.200");
  await signIn(page, "kan80-nouveau@example.org");
  const reloads: string[] = [];
  page.on("load", () => reloads.push(page.url()));
  const created = page.waitForResponse(
    (r) => r.url().endsWith("/api/projects") && r.request().method() === "POST",
  );
  // Aussitôt la galerie affichée, comme quelqu'un qui vient de se connecter.
  await page.getByRole("button", { name: "Nouveau projet" }).click();
  const response = await created;
  expect(response.status()).toBe(201);
  const { id } = (await response.json()) as { id: string };
  await expect(page).toHaveURL(new RegExp(`#/projects/${id}`));
  expect(reloads, "aucun rechargement de la page après le clic").toEqual([]);
  // Un seul projet : le clic n'a pas été rejoué.
  const list = await page.request.get("/api/projects");
  expect(list.ok()).toBe(true);
  const projects = (await list.json()) as unknown[];
  expect(projects).toHaveLength(1);
  await page.context().close();
});
