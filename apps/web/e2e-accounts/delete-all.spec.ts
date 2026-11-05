import { expect, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { person, test } from "./people";

/** KAN-119 : « Supprimer tous mes projets » sur le vrai serveur à comptes. Le propriétaire
 *  vide sa galerie ; une invitée ne supprime que ses projets, le projet partagé avec elle reste
 *  ; un mot faux est refusé par le serveur lui-même (le client seul ne suffit pas). Une adresse
 *  par personne (limite de 10 connexions par minute et par adresse) ; des comptes neufs à chaque
 *  passage (la route est limitée à 5 demandes par heure et par compte). */

const RUN = Date.now().toString(36);

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
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

const origin = (page: Page) => ({ Origin: new URL(page.url()).origin });

async function newProject(page: Page, name: string): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name },
    headers: origin(page),
  });
  expect(made.status()).toBe(201);
  return ((await made.json()) as { id: string }).id;
}

async function listed(page: Page): Promise<string[]> {
  const r = await page.request.get("/api/projects");
  return ((await r.json()) as { id: string }[]).map((p) => p.id);
}

/** Depuis l'accueil rechargé : menu « Plus », SUPPRIMER saisi, bouton rouge. */
async function deleteAllFromHome(page: Page, button: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByRole("button", { name: "⋯ Plus" }).click();
  await page
    .getByRole("menuitem", { name: "Supprimer tous mes projets…" })
    .click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByLabel(/Pour confirmer, saisissez SUPPRIMER/).fill("supprimer");
  await dialog.getByRole("button", { name: button }).click();
  await expect(dialog).toBeHidden({ timeout: 30_000 });
}

test("KAN-119 : le propriétaire supprime tous ses projets", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.119");
  await signIn(page, `proprio-kan119-${RUN}@example.org`);
  await newProject(page, "Séjour (KAN-119)");
  await newProject(page, "Combles (KAN-119)");
  await deleteAllFromHome(page, "Supprimer mes 2 projets");
  await expect(page.locator(".boards-notice")).toHaveText(
    "2 projets supprimés. La place est libérée.",
  );
  expect(await listed(page)).toEqual([]);
});

test("KAN-119 : une invitée ne supprime que les siens, le projet partagé reste", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.120");
  await signIn(owner, `hote-kan119-${RUN}@example.org`);
  const shared = await newProject(owner, "Grenier partagé (KAN-119)");
  const invited = await owner.request.post(
    `/api/projects/${shared}/invitations`,
    { data: { role: "editor" }, headers: origin(owner) },
  );
  const { token } = (await invited.json()) as { token: string };

  const guest = await person(browser, "198.51.100.121");
  await signIn(guest, `invitee-kan119-${RUN}@example.org`);
  const accepted = await guest.request.post("/api/auth/invitation/accept", {
    data: { token },
    headers: origin(guest),
  });
  expect(accepted.status()).toBe(200);
  const hers = await newProject(guest, "Bureau (KAN-119)");
  await gotoApp(guest, "/");
  await guest.getByRole("button", { name: "⋯ Plus" }).click();
  await guest
    .getByRole("menuitem", { name: "Supprimer tous mes projets…" })
    .click();
  await expect(guest.getByRole("alertdialog")).toContainText(
    "les projets partagés avec vous (1)",
  );
  await guest.keyboard.press("Escape");
  await deleteAllFromHome(guest, "Supprimer mon projet");
  await expect(guest.locator(".boards-notice")).toHaveText(
    "1 projet supprimé. La place est libérée.",
  );
  const left = await listed(guest);
  expect(left).toContain(shared);
  expect(left).not.toContain(hers);
  expect((await owner.request.get(`/api/projects/${shared}`)).status()).toBe(200);
});

test("KAN-119 : un mot faux est refusé par le serveur", async ({ browser }) => {
  const page = await person(browser, "198.51.100.122");
  await signIn(page, `mot-kan119-${RUN}@example.org`);
  const id = await newProject(page, "Atelier (KAN-119)");
  for (const confirm of ["supprimer", "SUPPRIME", ""]) {
    const r = await page.request.post("/api/projects/delete-all", {
      data: { confirm, project_ids: [id] },
      headers: origin(page),
    });
    expect(r.status()).toBe(400);
  }
  expect(await listed(page)).toEqual([id]);
});
