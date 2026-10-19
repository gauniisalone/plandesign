import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-47 : avec les comptes, le rôle sur le projet arrive après l'atelier. Un lien direct vers
 *  « Ajouter une source » (`#/projects/{id}/import`) doit mener un éditeur à l'import et un
 *  lecteur à l'espace de travail en lecture seule ; entre les deux, l'atelier attend, sans
 *  rien d'éditable. La réponse du rôle est retardée exprès (1,5 s) : la course est rejouée à
 *  chaque passage. */

const ROLE_DELAY_MS = 1500;

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
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
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

/** Projet vide du compte connecté (il en est propriétaire). */
async function emptyProject(page: Page, name: string): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name },
    headers: { Origin: new URL(page.url()).origin },
  });
  return ((await made.json()) as { id: string }).id;
}

/** Le rôle répond tard ; `as` : le rôle renvoyé (sinon la vraie réponse du serveur). */
async function slowRole(page: Page, as?: "viewer"): Promise<void> {
  await page.route("**/api/projects/*/role", async (route) => {
    const response = as ? null : await route.fetch();
    await new Promise((resolve) => setTimeout(resolve, ROLE_DELAY_MS));
    if (response) await route.fulfill({ response });
    else await route.fulfill({ json: { role: as } });
  });
}

test("KAN-47 : lien direct vers l'import, l'éditeur arrive sur l'import après une attente sans rien d'éditable", async ({
  page,
}) => {
  // Une adresse propre (comme person() dans accounts.spec) : les connexions des autres
  // fichiers depuis 127.0.0.1 n'épuisent pas la limite des tentatives (10 par minute).
  await page
    .context()
    .setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.81" });
  await signIn(page, "editrice@example.org");
  const id = await emptyProject(page, "Lien direct (éditeur)");
  await slowRole(page);
  await page.goto(`/#/projects/${id}/import`);
  // Rôle pas encore lu : l'attente, ni import, ni espace de travail, ni outil.
  const waiting = page
    .getByRole("status")
    .filter({ hasText: "Ouverture du projet" });
  await expect(waiting).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continuer vers le plan" }),
  ).toHaveCount(0);
  await expect(page.locator(".editor")).toHaveAttribute(
    "data-space",
    "loading",
  );
  await expect(
    page.getByRole("region", { name: "Espace de travail" }),
  ).toHaveCount(0);
  await shot(page, "kan47-1-attente");
  // Rôle lu (propriétaire) : l'import, et l'adresse n'a pas bougé.
  await expect(
    page.getByRole("button", { name: "Continuer vers le plan" }),
  ).toBeVisible({
    timeout: 15_000,
  });
  await expect(waiting).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`#/projects/${id}/import$`));
  await expect(page.locator(".editor")).toHaveAttribute("data-space", "source");
  await expect(page.locator(".editor.read-only")).toHaveCount(0);
  await shot(page, "kan47-2-editeur-import");
});

test("KAN-47 : le même lien mène un lecteur à l'espace de travail, en lecture seule", async ({
  page,
}) => {
  await page
    .context()
    .setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.82" });
  await signIn(page, "lecteur-kan47@example.org");
  const id = await emptyProject(page, "Lien direct (lecteur)");
  // Le serveur dit « lecteur » (même procédé que le test du lecteur sans outils).
  await slowRole(page, "viewer");
  await page.goto(`/#/projects/${id}/import`);
  await expect(
    page.getByRole("status").filter({ hasText: "Ouverture du projet" }),
  ).toBeVisible();
  await expect(page.locator(".editor")).toHaveAttribute("data-space", "board", {
    timeout: 15_000,
  });
  await expect(page.locator(".editor.read-only")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continuer vers le plan" }),
  ).toHaveCount(0);
  await expect(page).toHaveURL(new RegExp(`#/projects/${id}$`));
  await shot(page, "kan47-3-lecteur-espace");
});
