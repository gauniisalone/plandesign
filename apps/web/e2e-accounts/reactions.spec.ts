import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp, reloadApp } from "../e2e/app-ready";

/** KAN-78, à plusieurs : Gilles (propriétaire) réagit 😍 à la note de Fanny (éditrice) ; Fanny
 *  voit la pastille en direct (WebSocket, sans recharger) et sa cloche la prévient (« Gilles a
 *  réagi 😍 à « … » ») ; Gilles n'est pas notifié de sa propre réaction ; Kim, lectrice, voit la
 *  pastille sans pouvoir réagir (aucun bouton « Réagir »). Comptes, adresses et X-Forwarded-For
 *  propres à ce test. Aucune vérification de couleur. */

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function signInHere(page: Page, email: string, name: string) {
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
  const inside = page
    .getByRole("heading", { name: "Vos espaces de travail" })
    .or(page.locator(".editor"));
  await expect(field.or(inside)).toBeVisible({ timeout: 30_000 });
  if (await field.isVisible()) {
    await field.fill(name);
    await page.getByRole("button", { name: "Continuer" }).click();
  }
}

test("KAN-78 : Gilles réagit, Fanny le voit en direct et sa cloche la prévient ; Kim lit seulement", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const gilles = await person(browser, "198.51.100.178");
  await gotoApp(gilles, "/");
  await signInHere(gilles, "gilles.kan78@example.org", "Gilles");
  const origin = { Origin: new URL(gilles.url()).origin };
  const made = await gilles.request.post("/api/projects", {
    data: { name: "Atelier" },
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
  const fanny = await person(browser, "198.51.100.179");
  await gotoApp(fanny, `/#/invitation/${asEditor}`);
  await signInHere(fanny, "fanny.kan78@example.org", "Fanny");
  await expect(fanny.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  const kim = await person(browser, "198.51.100.180");
  await gotoApp(kim, `/#/invitation/${asViewer}`);
  await signInHere(kim, "kim.kan78@example.org", "Kim");
  await expect(kim.locator(".board-stage")).toBeVisible({ timeout: 30_000 });

  // Fanny pose une note (elle en est l'autrice), puis tout le monde ouvre l'espace de travail.
  const note = {
    id: crypto.randomUUID(),
    kind: "note",
    x: 60,
    y: 60,
    w: 240,
    h: 160,
    text: "Idée de Fanny",
  };
  const set = await fanny.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [note] },
    headers: { Origin: new URL(fanny.url()).origin },
  });
  expect(set.status()).toBe(200);
  await fanny.request.post("/api/notifications/read-all", {
    headers: { Origin: new URL(fanny.url()).origin },
  });
  for (const p of [gilles, fanny, kim]) {
    await gotoApp(p, `/#/projects/${id}/board`);
    await expect(p.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  }
  await reloadApp(fanny);
  await expect(fanny.getByTestId("bell-badge")).toHaveCount(0);

  // Gilles réagit 😍 par les actions de la sélection.
  const card = (p: Page) =>
    p.locator(".board-item.kind-note", { hasText: "Idée de Fanny" });
  await card(gilles).click();
  await gilles
    .getByRole("toolbar", { name: "Actions de la sélection" })
    .getByRole("button", { name: "Réagir", exact: true })
    .click();
  await gilles
    .getByRole("menu", { name: "Choisir une réaction" })
    .getByRole("menuitemcheckbox", { name: "😍 Coup de cœur" })
    .click();
  const row = (p: Page) =>
    p.getByRole("group", { name: "Réactions Idée de Fanny" });
  await expect(
    row(gilles).getByRole("button", { name: "😍 1 : Vous", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");

  // Fanny : la pastille arrive en direct, au nom de Gilles, sans être la sienne.
  const seen = row(fanny).getByRole("button", {
    name: "😍 1 : Gilles",
    exact: true,
  });
  await expect(seen).toBeVisible({ timeout: 5_000 });
  await expect(seen).toHaveAttribute("aria-pressed", "false");
  // Sa cloche : « Gilles a réagi 😍 à « Idée de Fanny » ».
  await expect(fanny.getByTestId("bell-badge")).toHaveText("1", {
    timeout: 5_000,
  });
  await fanny.getByRole("button", { name: /^Notifications/ }).click();
  await expect(
    fanny.getByRole("dialog", { name: "Notifications" }).getByRole("button", {
      name: /^Gilles a réagi 😍 à « Idée de Fanny » · Atelier · /,
    }),
  ).toBeVisible();

  // Gilles n'est pas notifié de sa propre réaction.
  const his = (await (
    await gilles.request.get("/api/notifications?tab=all")
  ).json()) as { items: { type: string }[] };
  expect(his.items.filter((l) => l.type === "reaction.added")).toEqual([]);

  // Kim, lectrice : la pastille est là, sans « Réagir » nulle part, et son clic n'écrit rien.
  const kimPill = row(kim).getByRole("button", {
    name: "😍 1 : Gilles",
    exact: true,
  });
  await expect(kimPill).toBeVisible({ timeout: 5_000 });
  await expect(kimPill).toHaveAttribute("aria-disabled", "true");
  const writes: string[] = [];
  kim.on("request", (r) => {
    if (r.method() !== "GET" && r.url().includes("/reactions"))
      writes.push(r.url());
  });
  await kimPill.click({ force: true }); // aria-disabled : le clic ne fait rien
  await card(kim).hover();
  await card(kim).click();
  await expect(
    kim.getByRole("button", { name: "Réagir", exact: true }),
  ).toHaveCount(0);
  expect(writes).toEqual([]);
});
