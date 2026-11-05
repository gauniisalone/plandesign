import {
  expect,
  test,
  type Browser,
  type BrowserContext,
  type Page,
  type WebSocketRoute,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-104, lot interface : le mot de passe dans l'app (OPENPLAN_PASSWORD_LOGIN posé par la
 *  config des e2e-accounts). Réglages › Compte : définir après une preuve récente (code),
 *  changer, sessions ouvertes et « Fermer » ; la page de connexion : mot de passe, refus
 *  unique, « Mot de passe oublié » par un code (jamais un lien) ; une erreur de saisie d'une
 *  personne connectée ne la déconnecte jamais ; la connexion de présence survit au jeton
 *  renouvelé. Adresses 198.51.100.104 à .108 et .112, propres à ce fichier. HIBP éteint
 *  (OPENPLAN_HIBP=off dans la config) : aucun appel à un tiers depuis les tests. */

const RUN = Date.now().toString(36);
const GOOD = "cerisier en fleur sous la pluie";
const OTHER = "lampe de chevet bleue du grenier";
const HOME = "Vos espaces de travail";

const opened: BrowserContext[] = [];
test.afterEach(async () => {
  for (const context of opened.splice(0)) await context.close();
});

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  opened.push(context);
  return context.newPage();
}

/** Le dernier code envoyé à cette adresse, différent de `previous` (l'e-mail part après la
 *  réponse : sans cela, on relirait le code d'avant). */
async function nextCode(page: Page, email: string, previous = ""): Promise<string> {
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code !== previous ? code : "";
    })
    .toMatch(/^\d{6}$/);
  return code;
}

/** Connexion par code depuis la page de l'app ; le code reçu, pour la suite. */
async function signInByCode(page: Page, email: string): Promise<string> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  const code = await nextCode(page, email);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter", exact: true }).click();
  const field = page.getByLabel("Votre nom (facultatif)");
  const home = page.getByRole("heading", { name: HOME });
  await expect(field.or(home)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible())
    await page.getByRole("button", { name: "Passer" }).click();
  await expect(home).toBeVisible();
  return code;
}

async function openSettings(page: Page) {
  await page.getByRole("button", { name: "Réglages", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Réglages" });
  await expect(panel).toBeVisible();
  return panel;
}

test("KAN-104 : définir un mot de passe dans Réglages › Compte, le changer, se connecter avec, fermer une session", async ({
  browser,
}) => {
  test.setTimeout(150_000);
  const email = `kan104-reglages-${RUN}@example.org`;
  const page = await person(browser, "198.51.100.104");
  const loginCode = await signInByCode(page, email);
  const panel = await openSettings(page);

  // Définir : d'abord le code de confirmation (preuve récente), puis le mot de passe.
  await panel.getByText("Mot de passe", { exact: true }).click();
  await expect(panel.getByText("Un mot de passe est facultatif.")).toBeVisible();
  await panel
    .getByRole("button", { name: "Recevoir un code de confirmation" })
    .click();
  await expect(panel.getByText("Un code vient de partir vers votre adresse.")).toBeVisible();
  const reauth = await nextCode(page, email, loginCode);
  await panel.getByLabel("Code à 6 chiffres").fill(reauth === "000000" ? "111111" : "000000");
  await panel.getByRole("button", { name: "Confirmer" }).click();
  // Code faux : 400, le texte du serveur ; toujours connectée.
  await expect(panel.getByRole("alert")).toHaveText(
    "Ce code n'est pas bon, ou il a expiré.",
  );
  await panel.getByLabel("Code à 6 chiffres").fill(reauth);
  await panel.getByRole("button", { name: "Confirmer" }).click();
  await expect(panel.getByText("C'est bien vous.")).toBeVisible();
  await panel.getByLabel("Nouveau mot de passe").fill("trop court");
  await panel.getByRole("button", { name: "Définir le mot de passe" }).click();
  await expect(panel.getByRole("alert")).toHaveText(
    "Le mot de passe doit faire au moins 12 caractères.",
  );
  await panel.getByLabel("Nouveau mot de passe").fill(GOOD);
  await panel.getByRole("button", { name: "Afficher" }).click();
  await expect(panel.getByLabel("Nouveau mot de passe")).toHaveAttribute("type", "text");
  await panel.getByRole("button", { name: "Définir le mot de passe" }).click();
  await expect(panel.getByRole("status").filter({ hasText: "Mot de passe défini." })).toHaveText(
    "Mot de passe défini. Vos autres sessions ont été fermées.",
  );

  // Changer : l'ancien faux est refusé en 403, sans déconnecter.
  await panel.getByLabel("Mot de passe actuel").fill(OTHER);
  await panel.getByLabel("Nouveau mot de passe").fill(OTHER + " encore");
  await panel.getByRole("button", { name: "Changer le mot de passe" }).click();
  await expect(panel.getByRole("alert")).toHaveText(
    "Le mot de passe actuel n'est pas le bon.",
  );
  await expect(panel.getByText(`Connecté :`)).toBeVisible();
  await panel.getByLabel("Mot de passe actuel").fill(GOOD);
  await panel.getByLabel("Nouveau mot de passe").fill(OTHER);
  await panel.getByRole("button", { name: "Changer le mot de passe" }).click();
  await expect(panel.getByRole("status").filter({ hasText: "Mot de passe changé." })).toBeVisible();

  // Sessions : il ne reste que celle-ci.
  await panel.getByText("Sessions ouvertes", { exact: true }).click();
  const list = panel.getByRole("list", { name: "Sessions ouvertes" });
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(list.getByText("Cette session")).toBeVisible();

  // Un autre navigateur : refus unique, puis le bon mot de passe.
  const other = await person(browser, "198.51.100.105");
  await gotoApp(other, "/");
  await expect(other.getByText("Un code ou votre mot de passe.")).toBeVisible();
  await other.getByRole("button", { name: "Se connecter avec un mot de passe" }).click();
  await other.getByLabel("Adresse e-mail").fill(email);
  await other.getByLabel("Mot de passe", { exact: true }).fill(GOOD);
  await other.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(other.getByRole("alert")).toHaveText("Adresse ou mot de passe incorrect.");
  await expect(other.getByLabel("Mot de passe", { exact: true })).toBeVisible();
  await other.getByLabel("Mot de passe", { exact: true }).fill(OTHER);
  await other.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(other.getByRole("heading", { name: HOME })).toBeVisible({ timeout: 30_000 });

  // Retour sur la première : la nouvelle session apparaît (relue à l'ouverture), on la ferme.
  // Cette page a déjà fait une dizaine d'appels /api/auth en une minute (10 par minute et par
  // adresse, AUTH_RATE_LIMIT) : la suite part d'une autre adresse, propre à ce fichier.
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.112" });
  await panel.getByText("Sessions ouvertes", { exact: true }).click();
  await expect(list).toBeHidden();
  await panel.getByText("Sessions ouvertes", { exact: true }).click();
  await expect(list.getByRole("listitem")).toHaveCount(2);
  await expect(list.getByText(/par mot de passe/)).toBeVisible();
  await list.getByRole("button", { name: /^Fermer la session/ }).click();
  await expect(panel.getByRole("status").filter({ hasText: "Session fermée" })).toBeVisible();
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await other.reload();
  await expect(
    other.getByRole("button", { name: "Recevoir un code" }),
  ).toBeVisible({ timeout: 30_000 });
});

test("KAN-104 : mot de passe oublié, depuis le lien du site, par un code", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const email = `kan104-oubli-${RUN}@example.org`;
  const first = await person(browser, "198.51.100.106");
  const loginCode = await signInByCode(first, email); // le compte existe, sans mot de passe
  await first.context().clearCookies();
  // Une page neuve (sans cookie) : changer l'ancre seule ne recharge pas l'app, qui resterait
  // connectée en mémoire.
  const page = await first.context().newPage();

  await gotoApp(page, "/#/mot-de-passe-oublie");
  await expect(page.getByRole("heading", { name: "Mot de passe oublié" })).toBeVisible();
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(
    page.getByText("Si cette adresse a un compte, un code vient de partir. Il expire dans 30 minutes."),
  ).toBeVisible();
  const reset = await nextCode(page, email, loginCode);
  await page.getByLabel("Code à 6 chiffres").fill(reset);
  await page.getByLabel("Nouveau mot de passe").fill(`${email} et plus`);
  await page
    .getByRole("button", { name: "Changer le mot de passe et me connecter" })
    .click();
  await expect(page.getByRole("alert")).toHaveText(
    "Le mot de passe ne doit contenir ni votre adresse ni le nom de l'application.",
  );
  await page.getByLabel("Nouveau mot de passe").fill(GOOD);
  await page
    .getByRole("button", { name: "Changer le mot de passe et me connecter" })
    .click();
  await expect(page.getByRole("heading", { name: HOME })).toBeVisible({ timeout: 30_000 });
  expect(new URL(page.url()).hash).toBe("");

  // Et par la page de connexion : « Mot de passe oublié ? » sous le mot de passe.
  const fresh = await person(browser, "198.51.100.107");
  await gotoApp(fresh, "/");
  await fresh.getByRole("button", { name: "Se connecter avec un mot de passe" }).click();
  await fresh.getByRole("button", { name: "Mot de passe oublié ?" }).click();
  await expect(fresh.getByRole("heading", { name: "Mot de passe oublié" })).toBeVisible();
  await fresh.getByRole("button", { name: "Retour à la connexion" }).click();
  await fresh.getByLabel("Adresse e-mail").fill(email);
  await fresh.getByLabel("Mot de passe", { exact: true }).fill(GOOD);
  await fresh.getByRole("button", { name: "Se connecter", exact: true }).click();
  await expect(fresh.getByRole("heading", { name: HOME })).toBeVisible({ timeout: 30_000 });
});

test("KAN-104 : la connexion de présence survit au mot de passe défini (jeton renouvelé)", async ({
  browser,
}) => {
  // La connexion garde le cookie de son ouverture ; le serveur la revérifie toutes les 30 s
  // et la ferme en 4401 après un mot de passe défini (les autres sessions fermées). Avant le
  // correctif, l'atelier renvoyait alors à la page de connexion ; il se reconnecte désormais.
  test.setTimeout(150_000);
  const email = `kan104-presence-${RUN}@example.org`;
  const page = await person(browser, "198.51.100.108");
  let sockets = 0;
  await page.routeWebSocket(/\/ws\/projects\//, (ws: WebSocketRoute) => {
    sockets += 1;
    ws.connectToServer();
  });
  const loginCode = await signInByCode(page, email);
  const origin = { Origin: new URL(page.url()).origin };
  const made = await page.request.post("/api/projects", {
    data: { name: "Projet du mot de passe" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => sockets).toBeGreaterThan(0);
  const before = sockets;

  expect((await page.request.post("/api/auth/reauth/start", { headers: origin })).status()).toBe(202);
  const reauth = await nextCode(page, email, loginCode);
  expect(
    (await page.request.post("/api/auth/reauth/verify", { data: { code: reauth }, headers: origin })).status(),
  ).toBe(204);
  expect(
    (await page.request.put("/api/auth/password", { data: { password: GOOD }, headers: origin })).status(),
  ).toBe(204);

  // Le recontrôle (30 s au plus) ferme l'ancienne connexion ; une nouvelle s'ouvre.
  await expect.poll(() => sockets, { timeout: 60_000 }).toBeGreaterThan(before);
  await expect(page.locator(".board-stage")).toBeVisible();
  await expect(page.getByRole("button", { name: "Recevoir un code" })).toHaveCount(0);
});
