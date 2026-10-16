import { expect, type Page } from "@playwright/test";

/** Projet vide au nom choisi, ouvert sur « Ajouter une source ». Depuis US1, l'accueil ne propose
 *  plus de champ de nom (« Commencer par un plan vide » donne un nom automatique, testé dans
 *  home.spec) : les tests qui ont besoin d'un nom précis passent par l'API. */
export async function createEmptyProject(
  page: Page,
  name: string,
): Promise<string> {
  const response = await page.request.post("/api/projects", { data: { name } });
  const { id } = (await response.json()) as { id: string };
  await page.goto(`/#/projects/${id}/import`);
  return id;
}

/** Espaces de l'atelier (B3, plus d'onglets) : résultats, éditeur (le tableau), plan en 2D
 *  ou en 3D, et « Ajouter une source ». */
export type Space = "renders" | "board" | "plan" | "3d" | "source";

const spaceOf = async (page: Page) =>
  (await page.locator(".editor").getAttribute("data-space")) ?? "";

export async function expectSpace(
  page: Page,
  space: Space,
  options?: { timeout?: number },
): Promise<void> {
  await expect(page.locator(".editor")).toHaveAttribute(
    "data-space",
    space,
    options,
  );
}

/** Va dans un espace par l'interface, comme on le ferait à la main : « Ouvrir l'éditeur »,
 *  « ← Résultats », « ← Tableau », « Modifier le plan » sur le cadre du plan, puis la vue
 *  3D de la barre des vues. */
export async function goSpace(page: Page, space: Space): Promise<void> {
  // L'atelier choisit d'abord son espace d'arrivée (rendus chargés).
  await expect(page.locator(".editor")).toHaveAttribute(
    "data-space",
    /^(renders|board|plan|3d|source)$/,
    { timeout: 30_000 },
  );
  for (let step = 0; step < 5; step++) {
    const now = await spaceOf(page);
    if (now === space) return;
    const nav = page.getByRole("navigation", { name: "Espaces du projet" });
    const views = page.getByRole("group", { name: "Vue" });
    if (now === "source")
      await page.getByRole("button", { name: /Ajouter une source/ }).click();
    else if ((space === "plan" || space === "3d") && now === "board")
      await page
        .getByRole("region", { name: "Tableau" })
        .locator(".board-item.kind-plan .board-plan-head button")
        .first()
        .click();
    else if (space === "3d" && now === "plan")
      await views.getByRole("button", { name: "3D", exact: true }).click();
    else if (space === "plan" && now === "3d")
      await views.getByRole("button", { name: "Plan", exact: true }).click();
    else await nav.getByRole("button").click();
    await expect.poll(() => spaceOf(page)).not.toBe(now);
  }
  await expectSpace(page, space);
}
