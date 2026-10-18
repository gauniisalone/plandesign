import { expect, type Locator, type Page } from "@playwright/test";

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

/** T7d1 (isolation des e2e lourds, PO du 18 oct.) : à la fin d'un test qui lance le chemin
 *  automatique, attendre que le serveur n'ait plus de tâche lourde ; un échec ne peut plus
 *  faire refuser (« une tâche lourde à la fois ») le lancement du test suivant. */
export async function waitNoHeavyJob(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const r = await page.request.get("/api/jobs/running");
        if (!r.ok()) return false;
        return ((await r.json()) as { running: boolean }).running;
      },
      { timeout: 300_000, intervals: [1000, 2000, 5000] },
    )
    .toBe(false);
}

/** T7d1 (décision PO du 16 oct.) : le panneau de droite est fermé par défaut ; les tests
 *  qui lisent l'inspecteur l'ouvrent comme on le ferait, par l'icône du haut. */
export async function showPanel(page: Page): Promise<void> {
  const open = page.getByRole("button", { name: "Afficher le panneau" });
  const close = page.getByRole("button", { name: "Masquer le panneau" });
  // L'atelier finit de charger : l'un des deux boutons est là.
  await expect(open.or(close)).toBeVisible({ timeout: 30_000 });
  if (await open.isVisible()) await open.click();
  await expect(close).toBeVisible();
}

/** T7d1 : le volet Rendus (ex-écran Résultats : « Vos rendus », vues automatiques, points de
 *  vue, galerie) s'ouvre par « Réglages et galerie » dans l'en-tête du cadre Rendus. */
export async function openRendersPane(page: Page): Promise<void> {
  await goSpace(page, "board");
  await page
    .locator(".board-item.kind-frame.role-renders")
    .getByRole("button", { name: "Réglages et galerie" })
    .click();
  await expect(
    page.locator(".side-card .side-head h2", { hasText: "Rendus" }),
  ).toBeVisible();
}

/** T7d2 : la visionneuse plein écran (ex-écran Résultats), ouverte par « Voir en grand » dans
 *  l'en-tête du cadre Rendus ; elle montre la première vue. */
export async function openViewer(page: Page): Promise<Locator> {
  await goSpace(page, "board");
  await page
    .locator(".board-item.kind-frame.role-renders")
    .getByRole("button", { name: "Voir en grand" })
    .click();
  const viewer = page.getByRole("dialog", { name: "Visionneuse des rendus" });
  await expect(viewer).toBeVisible();
  return viewer;
}

/** Espaces de l'atelier (B3, plus d'onglets ; T7d2, plus d'écran de résultats) : l'espace de
 *  travail, le plan en 2D ou en 3D, et « Ajouter une source ». */
export type Space = "board" | "plan" | "3d" | "source";

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

/** Va dans un espace par l'interface, comme on le ferait à la main : « ← Espace de travail »,
 *  « Modifier le plan » sur le cadre du plan, puis la vue 3D de la barre des vues. */
export async function goSpace(page: Page, space: Space): Promise<void> {
  // L'atelier choisit d'abord son espace d'arrivée (rendus chargés).
  await expect(page.locator(".editor")).toHaveAttribute(
    "data-space",
    /^(board|plan|3d|source)$/,
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
        .getByRole("region", { name: "Espace de travail" })
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
