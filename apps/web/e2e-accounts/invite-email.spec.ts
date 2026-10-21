import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-55 : inviter par adresse e-mail. Deux personnes par test, chacune avec sa propre
 *  adresse (domaine réservé example.test) et sa propre X-Forwarded-For. Aucun e-mail réel :
 *  le mailer `fake` du serveur des e2e. */

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function signIn(page: Page, email: string, name?: string): Promise<void> {
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
  if (await field.isVisible()) {
    if (name) {
      await field.fill(name);
      await page.getByRole("button", { name: "Continuer" }).click();
    } else {
      await page.getByRole("button", { name: "Passer" }).click();
    }
  }
  await expect(home).toBeVisible();
}

async function project(page: Page, name: string): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name },
    headers: { Origin: new URL(page.url()).origin },
  });
  return ((await made.json()) as { id: string }).id;
}

async function openShare(page: Page, id: string) {
  await gotoApp(page, `/#/projects/${id}`);
  await page.getByRole("button", { name: "Partager" }).click();
  return page.getByRole("dialog", { name: "Partager le projet" });
}

test("KAN-55 (1) : Fanny invite Gilles par son adresse, la cloche de Gilles l'annonce, il accepte et le projet s'ouvre", async ({
  browser,
}) => {
  const gilles = await person(browser, "198.51.100.155");
  await signIn(gilles, "gilles.kan55@example.test");
  const fanny = await person(browser, "198.51.100.156");
  await signIn(fanny, "fanny.kan55@example.test", "Fanny");
  const id = await project(fanny, "Grenier");

  const share = await openShare(fanny, id);
  const form = share.getByRole("form", { name: "Inviter par e-mail" });
  await form.getByLabel("Adresse e-mail").fill("gilles.kan55@example.test");
  await form.getByLabel("Rôle de l'invité").selectOption("editor");
  await form.getByRole("button", { name: "Inviter", exact: true }).click();
  await expect(share.getByRole("status")).toHaveText("Invitation envoyée");
  const pending = share.getByRole("list", { name: "Invitations en attente" });
  await expect(pending.getByRole("listitem")).toHaveCount(1);
  await expect(pending).toContainText("gilles.kan55@example.test");
  await expect(pending).toContainText("Éditeur");
  await shot(fanny, "kan55-1-partager");

  // Gilles, sur l'accueil : la cloche sonne, l'invitation est en tête, nominative.
  const badge = gilles.getByTestId("bell-badge");
  await expect(badge).toHaveText("1", { timeout: 5_000 });
  await gilles.getByRole("button", { name: /^Notifications/ }).click();
  const invites = gilles
    .getByRole("dialog", { name: "Notifications" })
    .getByRole("region", { name: "Invitations" });
  await expect(invites).toContainText(
    "Fanny vous invite sur « Grenier » (éditeur)",
  );
  await expect(invites).not.toContainText("@");
  await shot(gilles, "kan55-2-cloche");
  await invites.getByRole("button", { name: "Accepter", exact: true }).click();

  // Le projet s'ouvre, en éditeur (pas en lecture seule) ; plus d'invitation.
  await expect(gilles).toHaveURL(new RegExp(`#/projects/${id}`));
  await expect(gilles.locator(".editor")).toBeVisible({ timeout: 30_000 });
  await expect(gilles.locator(".editor.read-only")).toHaveCount(0);
  await expect(gilles.getByRole("button", { name: "Partager" })).toHaveCount(0);
  await shot(gilles, "kan55-3-projet-ouvert");

  // Chez Fanny : l'invitation n'est plus en attente, Gilles est membre éditeur.
  await fanny.reload();
  await fanny.getByRole("button", { name: "Partager" }).click();
  const again = fanny.getByRole("dialog", { name: "Partager le projet" });
  await expect(
    again.getByRole("list", { name: "Invitations en attente" }),
  ).toHaveCount(0);
  await expect(
    again
      .getByRole("list", { name: "Membres" })
      .getByLabel("Rôle de gilles.kan55@example.test"),
  ).toHaveValue("editor");
});

test("KAN-55 (2) : adresse sans compte, invitation annulable, l'inscription fait membre avec le rôle prévu", async ({
  browser,
}) => {
  const hugo = await person(browser, "198.51.100.157");
  await signIn(hugo, "hugo.kan55@example.test", "Hugo");
  const id = await project(hugo, "Atelier d'Hugo");
  const share = await openShare(hugo, id);
  const form = share.getByRole("form", { name: "Inviter par e-mail" });
  for (const [email, role] of [
    ["ines.kan55@example.test", "viewer"],
    ["jules.kan55@example.test", "commenter"],
  ] as const) {
    await form.getByLabel("Adresse e-mail").fill(email);
    await form.getByLabel("Rôle de l'invité").selectOption(role);
    await form.getByRole("button", { name: "Inviter", exact: true }).click();
    await expect(share.getByRole("status")).toHaveText("Invitation envoyée");
    await expect(form.getByLabel("Adresse e-mail")).toHaveValue("");
  }
  const pending = share.getByRole("list", { name: "Invitations en attente" });
  await expect(pending.getByRole("listitem")).toHaveCount(2);
  await pending
    .getByRole("button", {
      name: "Annuler l'invitation de jules.kan55@example.test",
    })
    .click();
  await expect(pending.getByRole("listitem")).toHaveCount(1);
  await expect(pending).toContainText("ines.kan55@example.test");
  await shot(hugo, "kan55-4-en-attente");

  // Inès s'inscrit avec l'adresse invitée : le projet est dans ses espaces, en lecteur.
  const ines = await person(browser, "198.51.100.158");
  await signIn(ines, "ines.kan55@example.test");
  const card = ines.locator(".boards-card", { hasText: "Atelier d'Hugo" });
  await expect(card.locator(".boards-role")).toHaveText("lecteur");
  await shot(ines, "kan55-5-inscrite");
  // Jules, invitation annulée : rien ne l'attend, même s'il s'inscrit (inscription ouverte
  // dans les e2e).
  const jules = await person(browser, "198.51.100.159");
  await signIn(jules, "jules.kan55@example.test");
  await expect(
    jules.locator(".boards-card", { hasText: "Atelier d'Hugo" }),
  ).toHaveCount(0);
  await expect(jules.getByTestId("bell-badge")).toHaveCount(0);
});
