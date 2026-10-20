import { expect, test } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  openRendersPane,
  openViewer,
  waitNoHeavyJob,
} from "./helpers";
import { gotoApp, reloadApp } from "./app-ready";

// T7d1 : isolation des e2e lourds : aucun test ne commence tant que le serveur calcule
// encore pour le précédent (même s'il a échoué).
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** V1 (note 10, point 6) : chemin « zéro geste ». Un plan déposé, et sans autre geste :
 *  ambiance déduite, aménagement par zones, trois vues, rendus ; progression par étape
 *  pendant que l'éditeur reste utilisable ; arrivée sur les rendus avec les accès pour
 *  affiner ; tout s'annule. (Finition coupée ici : le test ne charge pas le modèle
 *  d'images ; le serveur de test coupe le chemin par défaut, ce projet le rallume.) */

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const SHOTS = path.resolve("e2e/screenshots");

test("zéro geste : déposer un plan, voir la progression, arriver sur les rendus", async ({
  page,
}) => {
  test.skip(!existsSync(PLAN_DXF), "fixture plan_rect.dxf absente");
  test.setTimeout(300_000);
  await gotoApp(page, "/");
  await createEmptyProject(page, "E2E zéro geste");
  await expect(
    page.getByRole("heading", { name: "Ajouter une source" }),
  ).toBeVisible();
  const id = /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
  const patched = await page.request.patch(`/api/projects/${id}`, {
    data: {
      render: { auto_path: true, auto_finish: false, auto_inspiration: false },
    },
  });
  expect(patched.ok()).toBeTruthy();

  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  // La progression s'affiche, par étape, avec le temps restant ; l'éditeur reste utilisable.
  const progress = page.getByRole("status", { name: "Chemin automatique" });
  await expect(progress).toBeVisible();
  await expect(progress).toContainText(/Étape \d+\/\d+/);
  await expect(progress).toContainText(/restantes/);
  await page.keyboard.press("v");
  await page
    .locator("polygon.wall")
    .first()
    .click({ position: { x: 200, y: 4 } });
  // T7d1 : le panneau de droite est fermé par défaut ; on l'ouvre pour voir la fiche.
  await page.getByRole("button", { name: "Afficher le panneau" }).click();
  await expect(page.locator(".inspector h2").first()).toBeVisible();
  await page.getByRole("button", { name: "Masquer le panneau" }).click();

  // Arrivée (T7d1) : l'espace de travail cadré sur les rendus ; KAN-64 : le panneau de droite
  // est ouvert sur ses zones (Assistant, Commentaires), pas sur le volet Rendus ;
  // « Vos rendus » est dans le volet Rendus, ouvert par « Réglages et galerie » du cadre.
  await expect(
    page.locator(".board-item.kind-render:not(.role-inspiration)").first(),
  ).toBeVisible({ timeout: 240_000 });
  await expectSpace(page, "board");
  await expect(page.locator(".side.closed")).toHaveCount(0);
  await expect(page.locator(".side .board-side")).toBeVisible();
  await openRendersPane(page);
  const arrival = page.getByLabel("Vos rendus");
  await expect(arrival).toBeVisible();
  for (const name of [
    "Affiner le plan",
    "Changer l'ambiance",
    "Modifier les vues",
  ])
    await expect(arrival.getByRole("button", { name })).toBeVisible();
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<
      string,
      {
        elements: Record<
          string,
          {
            id: string;
            type: string;
            yaw?: number;
            metadata?: Record<string, unknown>;
            intent?: unknown;
          }
        >;
      }
    >;
  };
  const elements = Object.values(Object.values(project.levels)[0]!.elements);
  expect(elements.filter((e) => e.type === "furniture").length).toBeGreaterThan(
    2,
  );
  // Pièce de 12 m² : la vue de zone peut ne pas trouver de recul sans mur ; au moins
  // l'ensemble et le détail, et un rendu par vue.
  const views = elements.filter(
    (e) => e.type === "camera" && e.metadata?.auto_view,
  ).length;
  expect(views).toBeGreaterThanOrEqual(2);
  // Le cadre Rendus de l'espace de travail : une carte par vue, aucune « Re-rendre ».
  const rendersFrame = page.locator(".board-item.kind-frame.role-renders");
  await expect(
    page.locator(".board-item.kind-render:not(.role-inspiration)"),
  ).toHaveCount(views);
  await expect(
    rendersFrame.getByRole("button", { name: /Re-rendre/ }),
  ).toHaveCount(0);
  await expect(
    rendersFrame.getByRole("button", { name: "Réglages et galerie" }),
  ).toBeVisible();
  // T7d2 : la visionneuse plein écran (ex-écran Résultats) : une vue à la fois, chacune
  // rendue ; ← → passent de l'une à l'autre, Échap ferme.
  let stage = await openViewer(page);
  await expect(stage.locator(".renders-grid img")).toHaveCount(1);
  await expect(stage).toContainText(`1 / ${views}`);
  await expect(stage.locator(".badge.stale")).toHaveCount(0);
  // La place des commentaires reste masquée tant que B2 n'existe pas (décision PO).
  await expect(
    stage.getByRole("complementary", { name: "Commentaires de la vue" }),
  ).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "v1_arrivee.png") });
  for (let i = 2; i <= views; i++) {
    await page.keyboard.press("ArrowRight");
    await expect(stage).toContainText(`${i} / ${views}`);
    await expect(stage.locator(".renders-grid img")).toHaveCount(1);
  }
  await stage.getByRole("button", { name: "Vue suivante" }).click();
  await expect(stage).toContainText(`1 / ${views}`);
  expect(elements.find((e) => e.type === "room")?.intent).toBeTruthy();
  await page.keyboard.press("Escape");
  await expect(stage).toHaveCount(0);

  // « Changer l'ambiance » : retour au plan, fiche de la pièce ouverte.
  await arrival.getByRole("button", { name: "Changer l'ambiance" }).click();
  const card = page.getByLabel("Fiche Ambiance");
  await expect(card).toBeVisible();
  await expectSpace(page, "plan");

  // Une retouche après les rendus : dans la visionneuse, chaque vue est « périmée » (P1),
  // et l'en-tête les refait toutes.
  await card.getByLabel(/^Ambiance : /).selectOption("chalet");
  await expect(card.locator(".intent-tag").first()).toHaveText("choisi");
  const label = `Re-rendre les ${views} vues`;
  stage = await openViewer(page);
  for (let i = 1; i <= views; i++) {
    await expect(stage.locator(".badge.stale")).toHaveCount(1);
    await page.keyboard.press("ArrowRight");
  }
  await expect(stage.getByRole("button", { name: label })).toBeVisible();
  await expect(stage.getByRole("button", { name: label })).toHaveAttribute(
    "title",
    /changé/,
  );
  await page.screenshot({ path: path.join(SHOTS, "p1_rendus_perimes.png") });
  await page.keyboard.press("Escape");

  // P2 : la bande de rendus, sous le plan, dit les vues périmées et les refait en un clic
  // sans quitter le plan ; elle ne chevauche ni la barre du bas ni la bande des photos.
  await goSpace(page, "plan");
  const strip = page.getByRole("navigation", { name: "Bande de rendus" });
  await expect(strip.locator("li")).toHaveCount(views);
  await expect(strip.locator(".dot")).toHaveCount(views);
  await expect(strip.getByRole("button", { name: label })).toBeVisible();
  await expect(strip.locator(".dot")).toHaveCount(views);
  const overlap = async (a: string, b: string) => {
    const x = await page.locator(a).boundingBox();
    const y = await page.locator(b).boundingBox();
    if (!x || !y) return false;
    return (
      x.x < y.x + y.width &&
      y.x < x.x + x.width &&
      x.y < y.y + y.height &&
      y.y < x.y + x.height
    );
  };
  expect(await overlap(".render-strip", ".bottombar")).toBe(false);
  await strip.getByRole("button", { name: label }).click();
  await expectSpace(page, "plan");
  await expect(strip.locator(".dot")).toHaveCount(0, { timeout: 180_000 });
  await expect(strip.getByRole("button", { name: /Re-rendre/ })).toHaveCount(0);

  // Ne toucher qu'à une caméra ne périme que sa vue (P2) ; à la réouverture, le projet
  // s'ouvre sur Rendus (P1).
  const camera = elements.find(
    (e) => e.type === "camera" && e.metadata?.auto_view,
  )!;
  const lid = Object.keys(project.levels)[0]!;
  const moved = await page.request.post(
    `/api/projects/${id}/levels/${lid}/ops`,
    {
      data: {
        op: "update_element",
        element_id: camera.id,
        fields: { yaw: (camera.yaw ?? 0) + 0.2 },
      },
    },
  );
  expect(moved.ok()).toBeTruthy();
  await reloadApp(page);
  // T7d1 : la réouverture se fait sur l'espace de travail ; l'en-tête du cadre Rendus dit
  // la vue périmée et la refait en un clic.
  await expectSpace(page, "board");
  await expect(
    rendersFrame.getByRole("button", { name: "Re-rendre 1 vue périmée" }),
  ).toBeVisible();
  stage = await openViewer(page);
  let staleSeen = 0;
  for (let i = 1; i <= views; i++) {
    staleSeen += await stage.locator(".badge.stale").count();
    await page.keyboard.press("ArrowRight");
  }
  expect(staleSeen).toBe(1);
  await expect(
    stage.getByRole("button", { name: "Re-rendre la vue" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await goSpace(page, "plan");
  await expect(
    strip.getByRole("button", { name: "Re-rendre la vue" }),
  ).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "p2_bande.png") });

  // Raccourcis 1 à 4 depuis le plan : la 3D s'ouvre au point de vue demandé.
  await page.keyboard.press("1");
  await expect(page.locator(".viewer3d")).toHaveAttribute("data-preset", "top");

  // P3 : « Affiner cette vue » sous l'image ferme la visionneuse et ouvre la 3D à la
  // caméra du rendu, la pièce regardée choisie et sa fiche ouverte.
  stage = await openViewer(page);
  await stage
    .getByRole("button", { name: "Affiner cette vue" })
    .first()
    .click();
  await expect(stage).toHaveCount(0);
  await expectSpace(page, "3d");
  await expect(page.getByLabel("Fiche Ambiance")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "p3_affiner.png") });

  // P4b : désigner un meuble dans le rendu. On cherche par l'API un point où se voit un
  // meuble, puis on clique l'image à cet endroit.
  stage = await openViewer(page);
  const frame = stage.locator(".pick-frame").first();
  const picture = frame.locator("img").first();
  const src = (await picture.getAttribute("src")) ?? "";
  const runId = /runs\/([0-9a-f-]{36})\//.exec(src)![1]!;
  let spot: { x: number; y: number } | null = null;
  for (let gy = 0.35; gy < 0.95 && !spot; gy += 0.1)
    for (let gx = 0.1; gx < 0.95 && !spot; gx += 0.1) {
      const hit = (await (
        await page.request.get(
          `/api/projects/${id}/renders/${runId}/pick?x=${gx}&y=${gy}`,
        )
      ).json()) as { element_type: string | null };
      if (hit.element_type === "furniture") spot = { x: gx, y: gy };
    }
  expect(spot).not.toBeNull();
  const box = (await picture.boundingBox())!;
  const clickAt = async () =>
    picture.click({
      position: { x: spot!.x * box.width, y: spot!.y * box.height },
    });
  await clickAt();
  const pickCard = page.getByRole("dialog", { name: "Élément désigné" });
  await expect(pickCard).toContainText("Meuble :");
  await expect(frame.locator(".pick-outline")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "p4b_designer.png") });
  // Échap ferme d'abord la carte de désignation, pas la visionneuse.
  await page.keyboard.press("Escape");
  await expect(pickCard).toHaveCount(0);
  await expect(stage).toBeVisible();

  // « Remplacer » : un modèle de la même famille, un seul pas d'historique.
  const historyLength = async () =>
    (
      (await (await page.request.get(`/api/projects/${id}`)).json()) as {
        history_cursor: number;
      }
    ).history_cursor;
  const before = await historyLength();
  await clickAt();
  await pickCard.getByRole("button", { name: "Remplacer" }).click();
  const models = pickCard.getByRole("list", {
    name: "Modèles de cette famille",
  });
  await expect(models.getByRole("button").first()).toBeVisible();
  await models.getByRole("button").last().click();
  await expect.poll(historyLength).toBe(before + 1);

  // « Retirer » : la vue de ce rendu devient périmée, sous les yeux (la visionneuse reste
  // ouverte) ; annulable.
  await clickAt();
  await pickCard.getByRole("button", { name: "Retirer" }).click();
  await expect.poll(historyLength).toBe(before + 2);
  await expect(stage).toBeVisible();
  await expect(stage.locator(".badge.stale")).toHaveCount(1);
  await expect(
    stage.getByRole("button", { name: `Re-rendre les ${views} vues` }),
  ).toBeVisible();
  await page.keyboard.press("Control+z");
  await expect.poll(historyLength).toBe(before + 1);

  // P5b : « Demander… » sur un meuble désigné ferme la visionneuse et ouvre l'assistant
  // dans l'espace de travail, ce meuble en contexte ; une phrase y change l'ambiance de sa
  // pièce, « / » ferme et rouvre.
  await clickAt();
  await pickCard.getByRole("button", { name: "Demander…" }).click();
  const assistant = page.locator("aside.assistant");
  await expect(assistant).toBeVisible();
  await expect(stage).toHaveCount(0);
  await expectSpace(page, "board");
  await expect(assistant.locator(".assistant-context")).toContainText(/\w/);
  const ask = page.getByLabel("Votre demande à l'assistant");
  await ask.fill("plus chaleureux, lumière du soir");
  await ask.press("Enter");
  const said = assistant.locator(".bubble.assistant").last();
  await expect(said.locator(".status-done")).toContainText(/Ambiance de/);
  await expect(said).toContainText("lu par phrases simples");
  await page.screenshot({ path: path.join(SHOTS, "p5b_assistant_rendus.png") });
  await page.locator("body").click({ position: { x: 5, y: 790 } });
  await page.keyboard.press("/");
  await expect(assistant).toHaveCount(0);
  await page.keyboard.press("/");
  await expect(assistant).toBeVisible();
  // Le fil est relu du journal local après un rechargement.
  await reloadApp(page);
  await expectSpace(page, "board");
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await expect(
    page.locator("aside.assistant .bubble.user", {
      hasText: "plus chaleureux, lumière du soir",
    }),
  ).toBeVisible();

  // Zéro geste à l'arrivée : la mesure le confirme ; le clic « Affiner » est compté ; la
  // visionneuse est comptée à part (ce n'est pas un geste), sans aller-retour (T7d2).
  const m = (await (
    await page.request.get(`/api/projects/${id}/metrics`)
  ).json()) as {
    gestures_before_first_render: number;
    refine_clicks: number;
    viewer_opens: number;
    results_editor_trips: number;
  };
  expect(m.gestures_before_first_render).toBe(0);
  expect(m.refine_clicks).toBeGreaterThanOrEqual(2);
  expect(m.viewer_opens).toBeGreaterThanOrEqual(5);
  expect(m.results_editor_trips).toBe(0);
});
