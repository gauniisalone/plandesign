import { expect, test } from "@playwright/test";
import { createEmptyProject, expectSpace } from "./helpers";

/** P0 (B3) : les passages d'un espace à l'autre faits à la main (plan → tableau → résultats)
 *  arrivent au journal local et dans les indicateurs du parcours ; l'arrivée automatique
 *  ne compte pas. */
test("P0 : passages d'espace mesurés au journal local, sans réseau extérieur", async ({
  page,
}) => {
  const outside: string[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname))
      outside.push(url.host);
  });
  const id = await createEmptyProject(page, "E2E mesure");
  const nav = page.getByRole("navigation", { name: "Espaces du projet" });
  const source = page.getByRole("button", { name: "Ajouter une source" });
  await expect(source).toHaveAttribute("aria-pressed", "true");
  const metrics = async () =>
    (await (await page.request.get(`/api/projects/${id}/metrics`)).json()) as {
      tab_changes: number;
      ttfr_s: number | null;
    };
  expect((await metrics()).tab_changes).toBe(0);

  // Fermer « Ajouter une source » n'est pas un passage d'espace.
  await source.click();
  await expectSpace(page, "plan");
  await nav.getByRole("button", { name: "← Espace de travail" }).click();
  await expectSpace(page, "board");
  await nav.getByRole("button", { name: "← Résultats" }).click();
  await expectSpace(page, "renders");
  await expect.poll(async () => (await metrics()).tab_changes).toBe(2);
  const m = await metrics();
  expect(m.ttfr_s).toBeNull();
  expect(outside).toEqual([]);
});
