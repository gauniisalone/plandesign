import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { toLegacy } from "../e2e/legacy-board";

/** KAN-100 (MH3) : « Ranger » est un geste unique, vu en direct par les autres membres : Fanny
 *  range, Gilles voit les nodes passer à la nouvelle pose sans recharger. Chaque personne a sa
 *  propre adresse (limite des connexions par adresse). */

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

test("KAN-100 : Ranger chez Fanny, vu en direct chez Gilles", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const gilles = await person(browser, "198.51.100.181");
  await signIn(gilles, "gilles.kan100@example.org");
  const origin = { Origin: new URL(gilles.url()).origin };
  const made = await gilles.request.post("/api/projects", {
    data: { name: "Ranger à deux" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  // L'espace est posé (première lecture), puis reposé à l'ancienne.
  await gilles.request.get(`/api/projects/${id}/board`);
  await toLegacy(gilles.request, id, origin);
  const token = (
    (await (
      await gilles.request.post(`/api/projects/${id}/invitations`, {
        data: { role: "editor" },
        headers: origin,
      })
    ).json()) as { token: string }
  ).token;

  const fanny = await person(browser, "198.51.100.182");
  await signIn(fanny, "fanny.kan100@example.org", `/#/invitation/${token}`);
  await gotoApp(gilles, `/#/projects/${id}`);
  const photos = (p: Page) =>
    p.locator(".board-item.kind-frame.role-photos").first();
  const width = (p: Page) =>
    photos(p).evaluate((el) => parseFloat((el as HTMLElement).style.width));
  for (const p of [gilles, fanny])
    await expect.poll(() => width(p), { timeout: 30_000 }).toBe(624);

  const ranger = fanny
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Ranger" });
  await expect(ranger).toBeEnabled();
  await ranger.click();
  await expect.poll(() => width(fanny)).toBe(780);
  // Gilles le voit en direct, sans recharger : les nodes ont la nouvelle taille et le
  // Moodboard est passé au-dessus de Rendus.
  await expect.poll(() => width(gilles), { timeout: 5_000 }).toBe(780);
  const box = (p: Page, role: string) =>
    p
      .locator(`.board-item.kind-frame.role-${role}`)
      .first()
      .evaluate((el) => {
        const s = (el as HTMLElement).style;
        return {
          x: parseFloat(s.left),
          y: parseFloat(s.top),
          h: parseFloat(s.height),
        };
      });
  const mood = await box(gilles, "inspirations");
  const renders = await box(gilles, "renders");
  expect(mood.x).toBe(renders.x);
  expect(mood.y + mood.h).toBeLessThan(renders.y);
  await expect(
    gilles
      .getByRole("group", { name: "Ajouter" })
      .getByRole("button", { name: "Ranger" }),
  ).toBeDisabled();
  await shot(gilles, "kan100-vu-en-direct");
});
