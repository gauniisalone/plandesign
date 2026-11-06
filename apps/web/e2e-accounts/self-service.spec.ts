import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-132 : « Supprimer mon compte » et « Mes données » dans Réglages › Compte, sur le vrai
 *  serveur à comptes (OPENPLAN_ACCOUNT_SELF_SERVICE=1 dans la config de ces e2e). La
 *  suppression ne se rattrape pas : le parcours entier, jusqu'au nouveau propriétaire et au
 *  commentaire signé « Ancien membre ». L'export : le lien de l'e-mail sert une fois.
 *  Adresses 198.51.100.132 à .135, propres à ce fichier ; des comptes neufs à chaque passage
 *  (la suppression est limitée à 5 tentatives par heure et par compte). */

const RUN = Date.now().toString(36);

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function lastCode(page: Page, email: string, after = ""): Promise<string> {
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code && code !== after ? code : "";
    })
    .toMatch(/^\d{6}$/);
  return code;
}

/** Renvoie le code de connexion (pour attendre ensuite un code DIFFÉRENT). */
async function signIn(page: Page, email: string): Promise<string> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  // KAN-133 (lot « comptes ») : la case des conditions, exigée avant l'envoi.
  await page.getByLabel("J'accepte les conditions d'utilisation").check();
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  const code = await lastCode(page, email);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
  return code;
}

const origin = (page: Page) => ({ Origin: new URL(page.url()).origin });

async function myId(page: Page): Promise<string> {
  return ((await (await page.request.get("/api/auth/me")).json()) as { id: string }).id;
}

async function join(owner: Page, member: Page, pid: string, role: string): Promise<void> {
  const made = await owner.request.post(`/api/projects/${pid}/invitations`, {
    data: { role },
    headers: origin(owner),
  });
  const { token } = (await made.json()) as { token: string };
  const accepted = await member.request.post("/api/auth/invitation/accept", {
    data: { token },
    headers: origin(member),
  });
  expect(accepted.status()).toBe(200);
}

async function openSettings(page: Page) {
  await page.getByRole("button", { name: "Réglages", exact: true }).click();
  const panel = page.getByRole("dialog", { name: "Réglages" });
  await expect(panel).toBeVisible();
  return panel;
}

test("KAN-132 : supprimer un compte qui partage un projet ; l'héritier choisi le reprend, le commentaire reste « Ancien membre »", async ({
  browser,
}) => {
  test.setTimeout(150_000);
  const leaEmail = `lea-kan132-${RUN}@example.org`;
  const lea = await person(browser, "198.51.100.132");
  const loginCode = await signIn(lea, leaEmail);
  const made = await lea.request.post("/api/projects", {
    data: { name: "Grenier (KAN-132)" },
    headers: origin(lea),
  });
  const pid = ((await made.json()) as { id: string }).id;
  // Marie arrive d'abord (héritière par défaut) ; Léa choisira Paul.
  const marie = await person(browser, "198.51.100.133");
  await signIn(marie, `marie-kan132-${RUN}@example.org`);
  await join(lea, marie, pid, "editor");
  const paul = await person(browser, "198.51.100.134");
  await signIn(paul, `paul-kan132-${RUN}@example.org`);
  await join(lea, paul, pid, "viewer");
  const paulId = await myId(paul);
  const comment = await lea.request.post(`/api/projects/${pid}/comments`, {
    data: { target: { at: { x: 1, y: 1 } }, text: "Mesurer la trémie (KAN-132)" },
    headers: origin(lea),
  });
  expect(comment.status()).toBe(201);

  await gotoApp(lea, "/");
  const panel = await openSettings(lea);
  await panel.getByText("Supprimer mon compte", { exact: true }).click();
  const heir = panel.getByLabel("Nouveau propriétaire de « Grenier (KAN-132) »");
  await expect(heir).toBeVisible();
  await expect(heir).not.toHaveValue(paulId); // le défaut : l'éditrice, Marie
  await heir.selectOption(paulId);
  await panel.getByRole("button", { name: "Recevoir un code de confirmation" }).click();
  await panel.getByLabel("Code à 6 chiffres").fill(await lastCode(lea, leaEmail, loginCode));
  await panel.getByLabel(/Retapez votre adresse/).fill(leaEmail);
  await panel.getByRole("button", { name: "Supprimer définitivement mon compte" }).click();
  await expect(lea.getByLabel("Adresse e-mail")).toBeVisible({ timeout: 30_000 });
  expect((await lea.request.get("/api/auth/me")).status()).toBe(401);

  const role = await paul.request.get(`/api/projects/${pid}/role`);
  expect(((await role.json()) as { role: string }).role).toBe("owner");
  const threads = await (await paul.request.get(`/api/projects/${pid}/comments`)).text();
  expect(threads).toContain("Mesurer la trémie (KAN-132)");
  expect(threads).toContain("Ancien membre");
  expect(threads).not.toContain(leaEmail);
});

test("KAN-132 : exporter mes données ; le lien de l'e-mail sert une fois", async ({
  browser,
}) => {
  const email = `export-kan132-${RUN}@example.org`;
  const page = await person(browser, "198.51.100.135");
  await signIn(page, email);
  const panel = await openSettings(page);
  await panel.getByText("Mes données", { exact: true }).click();
  await panel.getByRole("button", { name: "Exporter mes données" }).click();
  await expect(panel.getByRole("status")).toContainText("lien par e-mail");
  let path = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-link?email=${encodeURIComponent(email)}`,
      );
      path = r.ok() ? ((await r.json()) as { path: string }).path : "";
      return path;
    })
    .toMatch(/^\/api\/account\/export\/download\?t=/);
  const first = await page.request.get(path);
  expect(first.status()).toBe(200);
  expect(first.headers()["content-type"]).toBe("application/zip");
  expect(first.headers()["referrer-policy"]).toBe("no-referrer");
  expect((await first.body()).subarray(0, 2).toString()).toBe("PK");
  const again = await page.request.get(path);
  expect(again.status()).toBe(404);
  // La page sans script échappe l'apostrophe (&#x27;) : on lit son titre et la fin de la phrase.
  const page404 = await again.text();
  expect(page404).toContain("<h1>Lien expiré</h1>");
  expect(page404).toContain("est plus valable");
});
