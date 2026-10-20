import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-65, avec les comptes : la couleur d'un post-it changée par un éditeur arrive en direct
 *  chez le propriétaire (sans recharger) ; le journal d'activité dit « a changé la couleur
 *  de … » ; le lecteur voit la couleur, sans palette ni écriture. Chaque personne a sa propre
 *  adresse (limite des connexions par adresse). */

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

/** Connexion par code, sur l'accueil ou sur une invitation. */
async function signIn(page: Page, email: string, path = "/"): Promise<void> {
  await gotoApp(page, path);
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
  const name = page.getByLabel("Votre nom (facultatif)");
  await expect(name.or(page.locator(".editor, .boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await name.isVisible()) {
    await name.fill(email.split(".")[0].replace(/^./, (c) => c.toUpperCase()));
    await page.getByRole("button", { name: "Continuer" }).click();
  }
}

test("KAN-65 : la couleur changée par un éditeur arrive en direct, au journal, lecteur sans palette", async ({
  browser,
}) => {
  const gilles = await person(browser, "198.51.100.165");
  await signIn(gilles, "gilles.kan65@example.org");
  const origin = { Origin: new URL(gilles.url()).origin };
  const made = await gilles.request.post("/api/projects", {
    data: { name: "Post-its" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invite = async (role: string) =>
    (
      (await (
        await gilles.request.post(`/api/projects/${id}/invitations`, {
          data: { role },
          headers: origin,
        })
      ).json()) as { token: string }
    ).token;
  const [asEditor, asViewer] = [await invite("editor"), await invite("viewer")];
  // Gilles pose un post-it jaune (par l'API), sous la mise en page de départ.
  const layout = (await (
    await gilles.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: { x: number; y: number; h: number }[] };
  const sofa = {
    id: crypto.randomUUID(),
    kind: "note",
    x: Math.min(...layout.items.map((i) => i.x)),
    y: Math.max(...layout.items.map((i) => i.y + i.h)) + 60,
    w: 200,
    h: 200,
    text: "Canapé",
    color: "yellow",
  };
  expect(
    (
      await gilles.request.patch(`/api/projects/${id}/board/items`, {
        data: { set: [sofa] },
        headers: origin,
      })
    ).status(),
  ).toBe(200);

  const fanny = await person(browser, "198.51.100.166");
  await signIn(fanny, "fanny.kan65@example.org", `/#/invitation/${asEditor}`);
  const kim = await person(browser, "198.51.100.167");
  await signIn(kim, "kim.kan65@example.org", `/#/invitation/${asViewer}`);
  await gotoApp(gilles, `/#/projects/${id}`);
  const note = (p: Page) =>
    p.locator(".board-item.kind-note", { hasText: "Canapé" });
  for (const p of [gilles, fanny, kim])
    await expect(note(p)).toHaveClass(/postit-yellow/, { timeout: 30_000 });

  // Fanny, éditrice : la palette avec les actions de la sélection, un clic.
  await note(fanny).click();
  const palette = (p: Page) =>
    p.getByRole("group", { name: "Couleur du post-it" });
  await palette(fanny)
    .getByRole("button", { name: "Vert", exact: true })
    .click();
  await expect(note(fanny)).toHaveClass(/postit-green/);
  // Gilles la voit en direct, sans recharger.
  await expect(note(gilles)).toHaveClass(/postit-green/, { timeout: 5_000 });
  await shot(gilles, "kan65-3-recu-en-direct");

  // Kim, lecteur : la couleur, mais ni palette ni écriture.
  await expect(note(kim)).toHaveClass(/postit-green/, { timeout: 5_000 });
  await note(kim).click();
  await expect(palette(kim)).toHaveCount(0);
  await kim.waitForTimeout(500);
  await note(kim).dblclick();
  await expect(
    kim.getByRole("textbox", { name: "Texte du post-it" }),
  ).toHaveCount(0);

  // Le journal d'activité (Must have 4) : « Fanny a changé la couleur de « Canapé » ».
  await gilles
    .locator(".topbar")
    .getByRole("button", { name: "Activité", exact: true })
    .click();
  const activity = gilles.getByRole("region", {
    name: "Activité",
    exact: true,
  });
  await expect(
    activity.getByRole("button", {
      name: /a changé la couleur de « Canapé »/,
    }),
  ).toBeVisible({ timeout: 10_000 });
  await shot(gilles, "kan65-4-activite");
});
