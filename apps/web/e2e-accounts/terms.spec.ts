import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-133 : les conditions d'utilisation. Une personne invitée crée son compte avec un mot
 *  de passe (MH2, case des conditions exigée), et l'écran de nouvelle acceptation passe avant
 *  l'application (MH6). Adresses 198.51.100.136 à .138 ; les adresses e-mail sont uniques à
 *  chaque exécution (le test se rejoue dans une même base). */

const TERMS = "J'accepte les conditions d'utilisation";

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function lastCode(page: Page, email: string, not = ""): Promise<string> {
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code !== not && /^\d{6}$/.test(code);
    })
    .toBe(true);
  return code;
}

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByLabel(TERMS).check();
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await page.getByLabel("Code à 6 chiffres").fill(await lastCode(page, email));
  await page.getByRole("button", { name: "Se connecter" }).click();
  const name = page.getByLabel("Votre nom (facultatif)");
  await expect(name.or(page.locator(".boards"))).toBeVisible({ timeout: 30_000 });
  if (await name.isVisible()) await page.getByRole("button", { name: "Passer" }).click();
}

test("KAN-133 : une personne invitée crée son compte avec un mot de passe, case des conditions exigée", async ({
  browser,
}) => {
  test.setTimeout(90_000);
  const run = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const owner = await person(browser, "198.51.100.136");
  await signIn(owner, `proprietaire-${run}@example.org`);
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Grenier des conditions" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invited = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: origin,
  });
  const { token } = (await invited.json()) as { token: string };

  const guest = await person(browser, "198.51.100.137");
  const email = `invitee-${run}@example.org`;
  await gotoApp(guest, `/#/invitation/${token}`);
  await expect(guest.getByText("Vous avez été invité à un projet")).toBeVisible();
  // Depuis une invitation : pas de connexion par mot de passe, mais la création d'un compte.
  await expect(
    guest.getByRole("button", { name: "Se connecter avec un mot de passe" }),
  ).toHaveCount(0);
  const terms = guest.getByLabel(TERMS);
  await expect(terms).not.toBeChecked();
  await expect(
    guest.getByRole("link", { name: "conditions d'utilisation", exact: true }),
  ).toHaveAttribute("href", "https://plandesign.fr/cgu/");
  await guest.getByLabel("Adresse e-mail").fill(email);
  const create = guest.getByRole("button", {
    name: "Créer un compte avec un mot de passe",
  });
  // Case vide : le navigateur refuse, rien ne part.
  const sent: string[] = [];
  guest.on("request", (r) => {
    if (r.url().includes("/api/auth/signup/start")) sent.push(r.url());
  });
  await create.click();
  await expect(guest.getByRole("heading", { name: "Créer votre compte" })).toHaveCount(0);
  expect(sent).toEqual([]);
  await terms.check();
  await create.click();
  await expect(guest.getByRole("heading", { name: "Créer votre compte" })).toBeVisible();
  await guest.getByLabel("Code à 6 chiffres").fill(await lastCode(guest, email));
  await guest
    .getByLabel("Mot de passe", { exact: true })
    .fill("cerisier en fleur sous la pluie");
  await guest.getByLabel("Votre nom (facultatif)").fill("Inès");
  await guest.getByRole("button", { name: "Créer mon compte" }).click();
  // L'invitation est acceptée : le projet s'ouvre, comme par code.
  await expect(guest).toHaveURL(new RegExp(`#/projects/${id}`), { timeout: 30_000 });
  const me = await guest.request.get("/api/auth/me");
  const body = (await me.json()) as { name: string; has_password: boolean; terms_pending: boolean };
  expect(body).toMatchObject({ name: "Inès", has_password: true, terms_pending: false });
});

test("KAN-133 : l'écran de nouvelle acceptation passe avant l'application", async ({
  browser,
}) => {
  const run = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const page = await person(browser, "198.51.100.138");
  await signIn(page, `reaccepte-${run}@example.org`);
  // Une version plus ancienne en base n'existe pas pour un compte neuf : la réponse de
  // /api/auth/me la simule (une fois), l'acceptation, elle, part vraiment.
  let once = true;
  await page.route("**/api/auth/me", async (route) => {
    if (route.request().method() !== "GET" || !once) return route.fallback();
    once = false;
    const response = await route.fetch();
    const json = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...json, terms_pending: true } });
  });
  await gotoApp(page, "/");
  await expect(
    page.getByRole("heading", { name: "Conditions d'utilisation" }),
  ).toBeVisible();
  await expect(page.locator(".boards")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "Lire les conditions d'utilisation" }),
  ).toHaveAttribute("href", "https://plandesign.fr/cgu/");
  const accepted = page.waitForResponse((r) => r.url().endsWith("/api/auth/terms/accept"));
  await page.getByRole("button", { name: "J'accepte" }).click();
  expect((await accepted).status()).toBe(204);
  await expect(page.locator(".boards")).toBeVisible({ timeout: 30_000 });
});
