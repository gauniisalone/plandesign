import { expect, request as apiRequest, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { expectHomeSettingsWhole } from "./settings-check";
import { person, test } from "./people";

/** KAN-102 : les jetons du module navigateur « Ajouter à PlanDesign », dans Réglages › Compte :
 *  créer (montré une fois), lister, révoquer. Le jeton ouvre les routes du module et rien
 *  d'autre ; la session de la page n'ouvre pas les routes du module. Une IP par test. */

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
  await page.getByRole("button", { name: "Se connecter" }).click();
  const field = page.getByLabel("Votre nom (facultatif)");
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(field.or(home)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible())
    await page.getByRole("button", { name: "Passer" }).click();
  await expect(home).toBeVisible();
}

/** Le module : aucun cookie, son jeton en Bearer, son origine. */
async function moduleClient(token: string, ip: string) {
  return apiRequest.newContext({
    baseURL: test.info().project.use.baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${token}`,
      Origin: "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
      "X-Forwarded-For": ip,
    },
  });
}

test("KAN-102 : créer, lister et révoquer un jeton de l'extension (Réglages › Compte)", async ({
  browser,
}) => {
  test.setTimeout(90_000);
  const ip = "198.51.100.230";
  const page = await person(browser, ip);
  await signIn(page, "kan102-jetons@example.org");
  await page.getByRole("button", { name: "Nouveau projet" }).click();
  await expect(page).toHaveURL(/#\/projects\//);
  await gotoApp(page, "/");
  // Repliée, la section ne change pas les Réglages de l'accueil (KAN-110).
  await expectHomeSettingsWhole(page, false);
  await page.getByRole("button", { name: "Réglages", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Réglages" });
  await panel.getByText("Extension navigateur").click();
  await expect(panel.getByText("Aucun jeton pour l'instant.")).toBeVisible();
  await panel.getByLabel("Nom du jeton").fill("Chrome du bureau");
  await panel
    .getByRole("button", { name: "Créer un jeton pour l'extension" })
    .click();
  const fresh = panel.getByLabel("Nouveau jeton de l'extension");
  await expect(fresh).toHaveValue(/^pde_[0-9a-f]{32}_/);
  const token = await fresh.inputValue();
  await expect(panel.getByText("il ne sera plus affiché")).toBeVisible();
  const list = panel.getByRole("list", { name: "Jetons de l'extension" });
  await expect(list.getByText("Chrome du bureau")).toBeVisible();
  await expect(list.getByText(/jamais utilisé/)).toBeVisible();
  // La liste ne montre jamais le jeton lui-même.
  await expect(list).not.toContainText("pde_");

  const module = await moduleClient(token, ip);
  const targets = await module.get("/api/extension/targets");
  expect(targets.status()).toBe(200);
  const body = (await targets.json()) as {
    projects: { moodboards: { title: string }[] }[];
  };
  expect(body.projects).toHaveLength(1);
  expect(body.projects[0]?.moodboards.map((m) => m.title)).toEqual([
    "Moodboard",
  ]);
  // Rien d'autre : ni la liste des projets, ni le compte, ni la gestion des jetons.
  for (const path of [
    "/api/projects",
    "/api/auth/me",
    "/api/account/extension-tokens",
  ])
    expect([401, 403], path).toContain((await module.get(path)).status());

  page.once("dialog", (d) => void d.accept());
  await list.getByRole("button", { name: "Révoquer Chrome du bureau" }).click();
  await expect(panel.getByText("Aucun jeton pour l'instant.")).toBeVisible();
  expect((await module.get("/api/extension/targets")).status()).toBe(401);
  await module.dispose();
  await page.context().close();
});

test("KAN-102 : la session de l'application n'ouvre pas les routes du module", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.231");
  await signIn(page, "kan102-session@example.org");
  // La page a son cookie de session : il ne suffit jamais.
  expect((await page.request.get("/api/extension/targets")).status()).toBe(401);
  expect((await page.request.get("/api/account/extension-tokens")).status()).toBe(
    200,
  );
  await page.context().close();
});
