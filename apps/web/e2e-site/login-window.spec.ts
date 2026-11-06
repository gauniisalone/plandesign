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
const OPEN_SENT =
  "Si cette adresse peut se connecter, un code vient de partir.";
const INVITE_SENT =
  "Si cette adresse a un compte ou une invitation, un code vient de partir.";

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
  const dialog = page.getByRole("dialog", {
    name: "Se connecter à PlanDesign",
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Adresse e-mail")).toBeFocused();
  await dialog.getByLabel("Adresse e-mail").fill(email);
  // KAN-133 : la case des conditions, exigée avant l'envoi.
  await dialog.getByLabel("J'accepte les conditions d'utilisation").check();
  await dialog.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(dialog.getByLabel("Code à 6 chiffres")).toBeVisible();
}

/** Code faux refusé (la fenêtre reste ouverte, message de l'API), puis le bon code. */
async function enterCodes(page: Page, email: string): Promise<void> {
  const dialog = page.getByRole("dialog", {
    name: "Se connecter à PlanDesign",
  });
  const code = await lastCode(page, email);
  const wrong = code === "000000" ? "111111" : "000000";
  await dialog.getByLabel("Code à 6 chiffres").fill(wrong);
  await dialog.getByRole("button", { name: "Se connecter" }).click();
  await expect(dialog.getByRole("alert").last()).toContainText(
    "Connexion impossible : ",
  );
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
  if (await field.isVisible())
    await page.getByRole("button", { name: "Passer" }).click();
}

test("« Se lancer » : adresse, code faux refusé, bon code, arrivée sur l'accueil de l'app", async ({
  page,
}) => {
  const email = "site-lancer@example.org";
  await page.goto("/");
  await page.getByRole("button", { name: "Se lancer" }).click();
  await sendAddress(page, email);
  const dialog = page.getByRole("dialog", {
    name: "Se connecter à PlanDesign",
  });
  // Le texte des deux modes est dans la page : on regarde ce qui est visible.
  await expect(dialog.getByText(OPEN_SENT)).toBeVisible();
  await expect(dialog.getByText(INVITE_SENT)).toBeHidden();
  await expect(dialog.getByText(BETA)).toBeHidden();
  await enterCodes(page, email);
  await expect(page).toHaveURL(`${APP}/`);
  await passNameStep(page);
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
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
  const nouveau = page.waitForRequest((r) =>
    r.url().startsWith(`${APP}/nouveau?`),
  );
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
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}(\/board)?$/, {
    timeout: 30_000,
  });
  const composer = page.getByRole("region", { name: "Compositeur" });
  await expect(composer).toBeVisible({ timeout: 30_000 });
  await expect(composer.getByLabel("Vos envies et contraintes")).toHaveValue(
    description,
  );
  await expect(composer.getByText(/compris :.*combles/)).toBeVisible();
});

/** Session déjà ouverte (cookie pd_session de l'app) : « Ouvrir mon espace » sur le site mène
 *  droit à l'app, sans fenêtre ni nouveau code. Le site ne lit que le statut de /api/auth/me. */
test("déjà connecté : « Ouvrir mon espace » mène à l'app sans redemander de code", async ({
  page,
}) => {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.183" });
  const email = "site-deja-connecte@example.org";
  await page.goto("/");
  await page.getByRole("button", { name: "Se lancer" }).click();
  await sendAddress(page, email);
  await enterCodes(page, email);
  await expect(page).toHaveURL(`${APP}/`);
  await passNameStep(page);
  // Retour sur le site, plus tard : la session de l'app est toujours là.
  const codes: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/auth/email/")) codes.push(r.url());
  });
  await page.goto("/");
  const me = page.waitForResponse((r) => r.url() === `${APP}/api/auth/me`);
  await page.getByRole("button", { name: "Ouvrir mon espace" }).first().click();
  expect((await me).status()).toBe(200);
  await expect(page).toHaveURL(`${APP}/`, { timeout: 15_000 });
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible({ timeout: 30_000 });
  expect(codes, "aucun nouveau code demandé").toEqual([]);
});

test("sans session : « Ouvrir mon espace » ouvre la fenêtre de connexion", async ({
  page,
}) => {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.184" });
  await page.goto("/");
  const me = page.waitForResponse((r) => r.url() === `${APP}/api/auth/me`);
  await page.getByRole("button", { name: "Ouvrir mon espace" }).first().click();
  expect((await me).status()).toBe(401);
  const dialog = page.getByRole("dialog", {
    name: "Se connecter à PlanDesign",
  });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByLabel("Adresse e-mail")).toBeFocused();
  expect(page.url().startsWith(APP)).toBe(false);
});

/** KAN-104 : la fenêtre n'offre le mot de passe que si /api/health de l'app le dit ouvert ;
 *  fermé, ou santé muette, rien n'en paraît (le site est statique : c'est le navigateur qui
 *  lit la santé). Adresses 198.51.100.109 à .111. */
const PASSWORD_LINK = "Se connecter avec un mot de passe";

async function openDialog(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Se lancer" }).click();
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  await expect(dialog).toBeVisible();
  return dialog;
}

for (const [what, health] of [
  ["mot de passe fermé", false],
  ["santé muette", null],
] as const) {
  test(`KAN-104 : ${what}, la fenêtre ne montre rien du mot de passe`, async ({ page }) => {
    await page.setExtraHTTPHeaders({
      "X-Forwarded-For": health === null ? "198.51.100.111" : "198.51.100.110",
    });
    await page.route(`${APP}/api/health`, async (route) => {
      if (health === null) return route.abort("failed");
      const response = await route.fetch();
      const json = (await response.json()) as Record<string, unknown>;
      await route.fulfill({ response, json: { ...json, password_login: health } });
    });
    const dialog = await openDialog(page);
    await expect(dialog.getByText("Sans mot de passe.")).toBeVisible();
    await expect(dialog.getByText("Un code vous est envoyé par e-mail.")).toBeVisible();
    await expect(dialog.getByRole("link", { name: PASSWORD_LINK })).toHaveCount(0);
    await expect(
      dialog.getByText("Avec un code reçu par e-mail, ou votre mot de passe."),
    ).toBeHidden();
    await expect(dialog.getByText("Un code ou votre mot de passe.")).toBeHidden();
  });
}

test("KAN-104 : mot de passe ouvert, le lien mène à la connexion de l'app, refus unique, arrivée sur l'accueil", async ({
  page,
}) => {
  const ip = "198.51.100.109";
  const headers = { "X-Forwarded-For": ip, Origin: APP };
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": ip });
  const email = "site-mot-de-passe@example.org";
  const password = "cerisier en fleur sous la pluie";
  // Le compte et son mot de passe, par l'API : code, preuve récente, mot de passe.
  const api = page.request;
  expect(
    (await api.post(`${APP}/api/auth/email/start`, { data: { email, cgu: true }, headers })).ok(),
  ).toBe(true);
  const login = await lastCode(page, email);
  expect(
    (await api.post(`${APP}/api/auth/email/verify`, { data: { email, code: login }, headers })).ok(),
  ).toBe(true);
  expect((await api.post(`${APP}/api/auth/reauth/start`, { headers })).status()).toBe(202);
  let reauth = "";
  await expect
    .poll(async () => (reauth = await lastCode(page, email)))
    .not.toBe(login);
  expect(
    (await api.post(`${APP}/api/auth/reauth/verify`, { data: { code: reauth }, headers })).status(),
  ).toBe(204);
  expect(
    (await api.put(`${APP}/api/auth/password`, { data: { password }, headers })).status(),
  ).toBe(204);
  await page.context().clearCookies(); // déconnecté : la fenêtre s'ouvrira

  const dialog = await openDialog(page);
  await expect(dialog.getByText("Avec un code reçu par e-mail, ou votre mot de passe.")).toBeVisible();
  await expect(dialog.getByText("Sans mot de passe.")).toBeHidden();
  await expect(dialog.getByText("Un code ou votre mot de passe.")).toBeVisible();
  // Le site ne tape ni n'envoie le mot de passe (conception L1 : aucune de ses routes n'est
  // ouverte au site) : le lien ouvre la page de connexion de l'app sur cette étape.
  const passwordCalls: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/auth/password")) passwordCalls.push(new URL(r.url()).origin);
  });
  const link = dialog.getByRole("link", { name: PASSWORD_LINK });
  await expect(link).toHaveAttribute("href", `${APP}/#/mot-de-passe`);
  await link.click();
  await expect(page).toHaveURL(`${APP}/#/mot-de-passe`);
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByLabel("Mot de passe", { exact: true }).fill("pas le bon mot de passe");
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Adresse ou mot de passe incorrect.");
  await page.getByLabel("Mot de passe", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  expect(new Set(passwordCalls)).toEqual(new Set([APP])); // seulement depuis l'app
  await expect(page).toHaveURL(`${APP}/`);
  await expect(page.getByRole("heading", { name: "Vos espaces de travail" })).toBeVisible({
    timeout: 30_000,
  });
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
  const dialog = page.getByRole("dialog", {
    name: "Se connecter à PlanDesign",
  });
  await expect(dialog.getByText(BETA)).toBeVisible();
  await sendAddress(page, "inconnu@example.org");
  await expect(dialog.getByText(INVITE_SENT)).toBeVisible();
  await expect(dialog.getByText(OPEN_SENT)).toBeHidden();
  await expect(
    dialog.getByText("Pas reçu ? Regardez dans vos courriers indésirables."),
  ).toBeVisible();
});

/** KAN-133 : la page des conditions (avec l'option de build seulement), son lien dans le pied
 *  de page, et la case de la fenêtre, exigée avant l'envoi de l'adresse. */
test("KAN-133 : page des conditions, lien du pied de page, case exigée", async ({ page }) => {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.139" });
  await page.goto("/");
  const footer = page.getByRole("navigation", { name: "Pied de page" });
  await footer.getByRole("link", { name: "Conditions d'utilisation" }).click();
  await expect(page).toHaveURL(/\/cgu\/$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Conditions générales d'utilisation" }),
  ).toBeVisible();
  await expect(page.getByText(/^Version 2026-11 · /)).toBeVisible();
  await page.goto("/");
  await page.getByRole("button", { name: "Se lancer" }).click();
  const dialog = page.getByRole("dialog", { name: "Se connecter à PlanDesign" });
  await dialog.getByLabel("Adresse e-mail").fill("site-cgu@example.org");
  const asked: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/auth/email/start")) asked.push(r.postData() ?? "");
  });
  await dialog.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(dialog.getByLabel("Code à 6 chiffres")).toBeHidden();
  expect(asked).toEqual([]);
  await dialog.getByLabel("J'accepte les conditions d'utilisation").check();
  await dialog.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(dialog.getByLabel("Code à 6 chiffres")).toBeVisible();
  expect(JSON.parse(asked[0] ?? "{}")).toMatchObject({ cgu: true });
});