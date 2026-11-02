import { expect, type Browser, type Page } from "@playwright/test";
import { gotoApp } from "../../e2e/app-ready";
import type { Ctx, Person } from "./fixtures";

/** KAN-103 : connexion par code, comme dans e2e-accounts et e2e-online. Chaque personne a son
 *  propre navigateur et sa propre adresse IP (X-Forwarded-For), dès la première requête. */

async function lastCode(page: Page, email: string): Promise<string> {
  const r = await page.request.get(`/api/test/mail/last-code?email=${encodeURIComponent(email)}`);
  return r.ok() ? ((await r.json()) as { code: string }).code : "";
}

/** Demande un code sur la page affichée et le rend. */
export async function requestCode(page: Page, email: string): Promise<string> {
  const before = await lastCode(page, email);
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      code = await lastCode(page, email);
      return code !== before ? code : "";
    })
    .toMatch(/^\d{6}$/);
  return code;
}

/** Un compte tout juste créé se voit proposer « Votre nom » : on le donne. */
export async function passNameStep(page: Page, name: string): Promise<void> {
  const field = page.getByLabel("Votre nom (facultatif)");
  const inside = page
    .getByRole("heading", { name: "Vos espaces de travail" })
    .or(page.locator(".editor"));
  await expect(field.or(inside)).toBeVisible({ timeout: 30_000 });
  if (!(await field.isVisible())) return;
  await field.fill(name);
  await page.getByRole("button", { name: "Continuer" }).click();
}

/** Le navigateur d'une personne, avec son adresse IP. */
export async function person(
  browser: Browser,
  baseURL: string | undefined,
  ctx: Ctx,
  name: string,
  ip: string,
  email?: string,
): Promise<Person> {
  const context = await browser.newContext({
    baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  const slug = name.normalize("NFD").replace(/[^a-zA-Z]/g, "").toLowerCase();
  const who: Person = {
    page: await context.newPage(),
    email: email ?? `${slug}-${ctx.suffix}@example.org`,
    ip,
  };
  ctx.people.set(name, who);
  return who;
}

/** Connexion depuis l'accueil. */
export async function signIn(who: Person, name: string): Promise<void> {
  await gotoApp(who.page, "/");
  const code = await requestCode(who.page, who.email);
  await who.page.getByLabel("Code à 6 chiffres").fill(code);
  await who.page.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(who.page, name);
}

export const origin = (page: Page) => ({ Origin: new URL(page.url()).origin });
