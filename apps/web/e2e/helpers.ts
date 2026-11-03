import { expect, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";

/** Projet vide au nom choisi, ouvert sur « Ajouter une source ». Depuis US1, l'accueil ne propose
 *  plus de champ de nom (« Commencer par un plan vide » donne un nom automatique, testé dans
 *  home.spec) : les tests qui ont besoin d'un nom précis passent par l'API. */
export async function createEmptyProject(
  page: Page,
  name: string,
): Promise<string> {
  const response = await page.request.post("/api/projects", { data: { name } });
  const { id } = (await response.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}/import`);
  return id;
}

/** Poids des modèles d'images (rendu contextualisé, finition) et de l'analyse fine d'une photo
 *  (profondeur, portes et fenêtres) : mêmes identifiants que /api/health. */
export const IMAGE_WEIGHTS = [
  "stable_diffusion_1_5@1.5",
  "controlnet_depth_sd15@1.1",
];
export const PHOTO_WEIGHTS = [
  "depth_anything_v2_metric_indoor_small@1.0",
  "owlv2_base@1.0",
];

/** Les poids sont-ils installés sur ce serveur ? Un test qui en dépend se saute proprement
 *  sans eux (copie de travail sans poids ni torch), au lieu d'attendre un bouton caché. */
export async function hasWeights(page: Page, ids: string[]): Promise<boolean> {
  const r = await page.request.get("/api/health");
  if (!r.ok()) return false;
  const body = (await r.json()) as { weights?: string[] };
  const weights = new Set(body.weights ?? []);
  return ids.every((id) => weights.has(id));
}

/** KAN-112 : ce serveur fait-il des rendus réalistes ? Même règle que l'application
 *  (`capabilities.ts` : Blender présent). La CI GitHub n'a ni Blender ni poids : les tests qui
 *  attendent un rendu y sautent, avec leur raison, au lieu d'attendre 280 s ; ici, ils tournent. */
export async function canRender(page: Page): Promise<boolean> {
  const r = await page.request.get("/api/health");
  if (!r.ok()) return false;
  const body = (await r.json()) as { blender?: boolean };
  return Boolean(body.blender);
}
export const NO_RENDER = "rendus réalistes : Blender absent sur ce serveur (CI)";

/** KAN-111 : un bouton de la barre d'ajout (« Plan », « Photos de la pièce », « Moodboard »,
 *  « Rendus »). */
export function addButton(page: Page, name: string): Locator {
  return page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name, exact: true });
}

/** KAN-111 : ajouter au clavier (Entrée : au centre de la vue, sur une place libre). Les
 *  tests qui veulent seulement un node de plus passent par là ; le glisser a ses tests. */
export async function addByKey(page: Page, name: string): Promise<void> {
  const button = addButton(page, name);
  await button.focus();
  await page.keyboard.press("Enter");
}

/** KAN-111 : glisser un bouton de la barre d'ajout jusqu'à un point de l'écran (ou au milieu
 *  d'un élément), en plusieurs pas, comme une souris ; `release: false` garde le bouton
 *  appuyé (pour regarder l'aperçu, ou appuyer sur Échap). */
export async function dragAdd(
  page: Page,
  name: string,
  to: { x: number; y: number } | Locator,
  { release = true }: { release?: boolean } = {},
): Promise<void> {
  const from = (await addButton(page, name).boundingBox())!;
  let x: number;
  let y: number;
  if ("boundingBox" in to) {
    const b = (await to.boundingBox())!;
    x = b.x + b.width / 2;
    y = b.y + b.height / 2;
  } else ({ x, y } = to);
  await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
  await page.mouse.down();
  await page.mouse.move(x, y, { steps: 12 });
  if (release) await page.mouse.up();
}

/** T7d1 (isolation des e2e lourds, PO du 18 oct.) : à la fin d'un test qui lance le chemin
 *  automatique, attendre que le serveur n'ait plus de tâche lourde, ni en cours ni en
 *  attente : le test suivant ne démarre pas derrière le calcul du précédent. */
export async function waitNoHeavyJob(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const r = await page.request.get("/api/jobs/running");
        if (!r.ok()) return false;
        // File d'attente : rien ne calcule et rien n'attend son tour.
        const body = (await r.json()) as { running: boolean; queued?: number };
        return body.running || (body.queued ?? 0) > 0;
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
  // L'atelier finit de charger : l'un des deux boutons est là. KAN-64 : dans l'espace de
  // travail, le panneau s'ouvre de lui-même juste après le chargement ; on lui laisse ce
  // temps avant de cliquer (sinon le bouton change sous le clic).
  await expect(open.or(close)).toBeVisible({ timeout: 30_000 });
  const opened = await close
    .waitFor({ state: "visible", timeout: 3_000 })
    .then(() => true)
    .catch(() => false);
  if (!opened) await open.click();
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
    else if ((space === "plan" || space === "3d") && now === "board") {
      // KAN-98 L2 : la vue peut ne pas tout montrer (ouverture au plancher, cadrage sur les
      // Rendus) ; « Tout voir » (Maj+1) d'abord, comme on le ferait.
      await page.keyboard.press("Shift+Digit1");
      await page
        .getByRole("region", { name: "Espace de travail" })
        .locator(".board-item.kind-plan .board-plan-head button")
        .first()
        .click();
    } else if (space === "3d" && now === "plan")
      await views.getByRole("button", { name: "3D", exact: true }).click();
    else if (space === "plan" && now === "3d")
      await views.getByRole("button", { name: "Plan", exact: true }).click();
    else await nav.getByRole("button").click();
    await expect.poll(() => spaceOf(page)).not.toBe(now);
  }
  await expectSpace(page, space);
}

/** KAN-120 : une photo importée ne construit plus le plan ; on clique, comme on le ferait,
 *  « Générer le plan à partir de N photos » dans le node Photos de la pièce (l'import d'une
 *  photo ouvre l'espace de travail), puis on attend les murs. */
export async function generateFromPhotos(
  page: Page,
  options?: { photos?: number; timeout?: number; walls?: number },
): Promise<void> {
  await expectSpace(page, "board", { timeout: 30_000 });
  const n = options?.photos ?? 1;
  const node = page
    .getByRole("region", { name: "Espace de travail" })
    .locator(".board-photos-generate");
  await node
    .getByRole("button", {
      name: `Générer le plan à partir de ${n} photo${n > 1 ? "s" : ""}`,
    })
    .click({ timeout: 30_000 });
  if (options?.walls === 0) return;
  await expect(
    page
      .getByRole("region", { name: "Espace de travail" })
      .locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({ timeout: options?.timeout ?? 180_000 });
}
