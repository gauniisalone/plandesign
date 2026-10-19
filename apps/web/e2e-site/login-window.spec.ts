import { expect, test, type Page } from "@playwright/test";

/** Fenêtre « Se connecter » de plandesign.fr (apps/site/login/login.js, option
 *  PUBLIC_LOGIN_WINDOW=1) : ouverture par « Se lancer », adresse, code faux refusé, bon code,
 *  arrivée sur l'app ; formulaire rempli → /nouveau, compositeur prérempli ; mode « invite »
 *  (lu sur /api/health) : ligne bêta, réponse neutre pour une adresse inconnue. */

const apiPort = process.env.OPENPLAN_PORT ?? "8015";
const webPort = process.env.OPENPLAN_WEB_PORT ?? "5188";
const APP = `http://localhost:${webPort}`;
const BETA =
  "PlanDesign est en bêta, sur invitation. Déjà inscrit ou invité ? Connectez-vous.";
const OPEN_SENT = "Si cette adresse peut se connecter, un code vient de partir.";
const INVITE_SENT = "Si cette adresse a un compte ou une invitation, un code vient de partir.";

test.beforeEach(async ({ page }) => {
  // En production, Caddy envoie /nouveau à l'API (redirection 303 vers /#/nouveau?…) ; en
  // local, Vite ne relaie que /api et /ws : on passe ce chemin à l'API nous-mêmes.
  await page.route(`${APP}/nouveau**`, async (route) => {
    const url = new URL(route.request().url());
    const response = await route.fetch({
      url: `http://127.0.0.1:${apiPort}${url.pathname}${url.search}`,
      maxRedirects: 0,
    });
    await route.fulfill({ response });
  });
});

async function lastCode(page: Page, email: string): Promise<string> {
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `${APP}/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  return code;
}

async function sendAddress(page: Page, email: string): Promise<void> {
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Adresse e-mail")).toBeFocused();
  await dialog.getByLabel("Adresse e-mail").fill(email);
  await dialog.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(dialog.getByLabel("Code à 6 chiffres")).toBeVisible();
}

/** Code faux refusé (la fenêtre reste ouverte, message de l'API), puis le bon code. */
async function enterCodes(page: Page, email: string): Promise<void> {
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  const code = await lastCode(page, email);
  const wrong = code === "000000" ? "111111" : "000000";
  await dialog.getByLabel("Code à 6 chiffres").fill(wrong);
  await dialog.getByRole("button", { name: "Se connecter" }).click();
  await expect(dialog.getByRole("alert").last()).toContainText("Connexion impossible : ");
  await expect(page).toHaveURL(/localhost:\d+\/$/);
  expect(page.url().startsWith(APP)).toBe(false);
  await dialog.getByLabel("Code à 6 chiffres").fill(code);
  await dialog.getByRole("button", { name: "Se connecter" }).click();
}

/** Un compte tout juste créé peut se voir proposer « Votre nom » : on passe. */
async function passNameStep(page: Page): Promise<void> {
  const field = page.getByLabel("Votre nom (facultatif)");
  const inside = page
    .getByRole("heading", { name: "Vos espaces de travail" })
    .or(page.getByRole("region", { name: "Compositeur" }));
  await expect(field.or(inside)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible()) await page.getByRole("button", { name: "Passer" }).click();
}

test("« Se lancer » : adresse, code faux refusé, bon code, arrivée sur l'accueil de l'app", async ({
  page,
}) => {
  const email = "site-lancer@example.org";
  await page.goto("/");
  await page.getByRole("button", { name: "Se lancer" }).click();
  await sendAddress(page, email);
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  // Le texte des deux modes est dans la page : on regarde ce qui est visible.
  await expect(dialog.getByText(OPEN_SENT)).toBeVisible();
  await expect(dialog.getByText(INVITE_SENT)).toBeHidden();
  await expect(dialog.getByText(BETA)).toBeHidden();
  await enterCodes(page, email);
  await expect(page).toHaveURL(`${APP}/`);
  await passNameStep(page);
  await expect(page.getByRole("heading", { name: "Vos espaces de travail" })).toBeVisible();
  // La session posée par la fenêtre du site est celle de l'app.
  const me = await page.request.get(`${APP}/api/auth/me`);
  expect(me.status()).toBe(200);
  expect(((await me.json()) as { email: string }).email).toBe(email);
});

test("formulaire rempli : « Commencer », connexion, arrivée sur /nouveau prérempli", async ({
  page,
}) => {
  const email = "site-formulaire@example.org";
  const description = "Chambre d'amis sous les combles, ambiance bois clair";
  await page.goto("/");
  await page.locator("#piece").selectOption("attic");
  await page.locator("#surface").fill("35.5");
  await page.locator("#description").fill(description);
  await page
    .getByRole("group", { name: "Votre projet" })
    .getByRole("button", { name: "Commencer" })
    .click();
  await sendAddress(page, email);
  const nouveau = page.waitForRequest((r) => r.url().startsWith(`${APP}/nouveau?`));
  await enterCodes(page, email);
  const sent = new URL((await nouveau).url());
  expect(Object.fromEntries(sent.searchParams)).toEqual({
    v: "1",
    source: "site",
    piece: "attic",
    surface: "35.5",
    description,
  });
  await passNameStep(page);
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}(\/board)?$/, { timeout: 30_000 });
  const composer = page.getByRole("region", { name: "Compositeur" });
  await expect(composer).toBeVisible({ timeout: 30_000 });
  await expect(composer.getByLabel("Vos envies et contraintes")).toHaveValue(description);
  await expect(composer.getByText(/compris :.*combles/)).toBeVisible();
});

test("mode « invite » : ligne bêta, réponse neutre pour une adresse inconnue", async ({
  page,
}) => {
  // Le serveur de test est en inscription ouverte : /api/health dit ici « invite ».
  await page.route(`${APP}/api/health`, async (route) => {
    const response = await route.fetch();
    const health = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...health, signup: "invite" } });
  });
  await page.goto("/");
  await expect(page.getByText("· Bêta sur invitation")).toBeVisible();
  await page.getByRole("button", { name: "Se lancer" }).click();
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  await expect(dialog.getByText(BETA)).toBeVisible();
  await sendAddress(page, "inconnu@example.org");
  await expect(dialog.getByText(INVITE_SENT)).toBeVisible();
  await expect(dialog.getByText(OPEN_SENT)).toBeHidden();
  await expect(dialog.getByText("Pas reçu ? Regardez dans vos courriers indésirables.")).toBeVisible();
});
