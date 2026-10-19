import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";

/** Retour d'augus (19 oct., en production) : le champ « Surface (m²) » des Questions guidées
 *  n'acceptait rien quand on tapait un nombre. On tape comme un humain, touche par touche :
 *  « 3 », « 35 », « 35,5 » restent affichés ; la validation se fait à la sortie du champ, avec
 *  un message clair hors bornes. Même règle pour « Chambres ». */

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

type Answers = { area_m2?: number | null; bedrooms?: number | null } | null;

async function answers(page: Page, id: string): Promise<Answers> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as { pending_answers: Answers };
  return project.pending_answers;
}

test("Questions guidées : surface et chambres se tapent touche par touche", async ({
  page,
}) => {
  await page.emulateMedia({ colorScheme: "dark" });
  const made = await page.request.post("/api/projects", {
    data: { name: "Saisie des nombres" },
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}/board`);
  const composer = page.getByRole("region", { name: "Compositeur" });
  await expect(composer).toBeVisible({ timeout: 30_000 });
  await composer.getByRole("tab", { name: "Questions guidées" }).click();

  const surface = composer.getByLabel("Surface (m²)");
  await surface.click();
  await surface.pressSequentially("3");
  await expect(surface).toHaveValue("3");
  await shot(page, "surface-1-apres-3");
  await surface.pressSequentially("5");
  await expect(surface).toHaveValue("35");
  await surface.pressSequentially(",5");
  await expect(surface).toHaveValue("35,5");
  await surface.blur();
  await expect(composer.getByRole("alert")).toHaveCount(0);
  await expect.poll(async () => (await answers(page, id))?.area_m2).toBe(35.5);
  await expect(composer).toContainText(/compris : .*35,5 m²/);

  // Le point marche aussi.
  await surface.fill("");
  await surface.pressSequentially("35.5");
  await expect(surface).toHaveValue("35.5");

  // Hors bornes : rien n'est effacé pendant la frappe, un message clair à la sortie.
  await surface.fill("");
  await surface.pressSequentially("500");
  await expect(surface).toHaveValue("500");
  await surface.blur();
  await expect(composer.getByRole("alert")).toHaveText("Entre 8 et 400 m².");
  await expect(surface).toHaveAttribute("aria-invalid", "true");
  await shot(page, "surface-2-hors-bornes");
  await expect
    .poll(async () => (await answers(page, id))?.area_m2 ?? null)
    .toBe(null);
  // Corrigé : le message s'en va.
  await surface.fill("");
  await surface.pressSequentially("60");
  await expect(composer.getByRole("alert")).toHaveCount(0);
  await expect.poll(async () => (await answers(page, id))?.area_m2).toBe(60);

  // Chambres : même règle (0 à 6).
  const bedrooms = composer.getByLabel("Chambres");
  await bedrooms.click();
  await bedrooms.pressSequentially("2");
  await expect(bedrooms).toHaveValue("2");
  await bedrooms.blur();
  await expect.poll(async () => (await answers(page, id))?.bedrooms).toBe(2);
  await bedrooms.fill("");
  await bedrooms.pressSequentially("7");
  await expect(bedrooms).toHaveValue("7");
  await bedrooms.blur();
  await expect(composer.getByRole("alert")).toHaveText(
    "Chambres : un nombre entier de 0 à 6.",
  );
  await expect(bedrooms).toHaveAttribute("aria-invalid", "true");
});
