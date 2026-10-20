import { expect, test, type Browser, type Page } from "@playwright/test";
import { gotoApp, reloadApp } from "../e2e/app-ready";
import { goSpace } from "../e2e/helpers";
import {
  cameraBasis,
  mockReference,
  newState,
  SEG_ID,
  smallPhoto,
} from "../e2e/photo-measure-mock";

/** KAN-66 MH7 (lecteur) : un lecteur voit la mesure de référence posée sur une photo (segment,
 *  cote, échelle) en lecture seule : ni « Mesurer sur la photo », ni « Modifier » ni
 *  « Supprimer », aucune écriture. Le rôle « lecteur » est simulé côté navigateur (même
 *  procédé que KAN-50) ; l'API de la mesure est simulée (contrat du 20 oct.). Adresse et
 *  X-Forwarded-For propres à ce test. */

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
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

test("KAN-66 MH7 : le lecteur voit la mesure, en lecture seule", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.66");
  const png = await smallPhoto(page);
  await signIn(page, "lecteur-kan66@example.org");
  const origin = { Origin: new URL(page.url()).origin };
  const made = await page.request.post("/api/projects", {
    data: { name: "Projet partagé (KAN-66)" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  await page.request.post(`/api/projects/${id}/images`, {
    multipart: {
      file: { name: "porte.png", mimeType: "image/png", buffer: png },
    },
    headers: origin,
  });
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as { images: Record<string, unknown> };
  const imageId = Object.keys(project.images)[0] ?? "";
  expect(imageId).toBeTruthy();

  const state = newState({
    ...cameraBasis(imageId),
    source: "reference",
    text: "Échelle calée sur votre mesure de 2,04 m (porte)",
    segment_id: SEG_ID,
  });
  state.segments = [
    {
      id: SEG_ID,
      p1: [250, 450],
      p2: [250, 180],
      length_m: 2.04,
      label: "porte",
      author: "local",
      created_at: "2026-10-20T15:42:00Z",
    },
  ];
  await mockReference(page, id, imageId, state);
  await page.route("**/api/projects/*/role", (route) =>
    route.fulfill({ json: { role: "viewer" } }),
  );
  const writes: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() !== "GET" && p.includes("/reference"))
      writes.push(`${r.method()} ${p}`);
  });
  await gotoApp(page, `/#/projects/${id}/board`);
  await reloadApp(page);
  await expect(page.locator(".editor.read-only")).toBeVisible({
    timeout: 30_000,
  });
  // La photo fait arriver sur le plan : retour à l'espace de travail.
  await goSpace(page, "board");

  await page.locator(".board-item.kind-photo").first().click();
  await expect(
    page.getByRole("button", { name: "Mesurer sur la photo", exact: true }),
  ).toHaveCount(0);
  await page
    .getByRole("button", { name: "Voir la mesure", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Mesures de la photo" });
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".pm-cote")).toHaveText("2,04 m · porte");
  await expect(dialog.getByLabel("Échelle de l'estimation")).toContainText(
    "Échelle calée sur votre mesure de 2,04 m (porte)",
  );
  await expect(dialog).toContainText("Lecture seule");
  for (const name of ["Modifier", "Supprimer", "Mesurer une longueur"])
    await expect(dialog.getByRole("button", { name, exact: true })).toHaveCount(
      0,
    );

  // Deux clics sur la photo : rien ne se trace, aucune loupe, aucune écriture.
  const box = await dialog.locator(".pm-photo-box img").boundingBox();
  if (!box) throw new Error("photo absente du calque");
  await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await expect(page.getByRole("img", { name: "Loupe ×4" })).toHaveCount(0);
  await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await page.mouse.click(box.x + box.width * 0.6, box.y + box.height * 0.2);
  await expect(
    page.getByRole("dialog", { name: "Longueur du segment" }),
  ).toHaveCount(0);
  await page.waitForTimeout(500);
  expect(writes).toEqual([]);
  expect(state.puts).toEqual([]);
});
