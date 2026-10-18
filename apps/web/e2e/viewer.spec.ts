import { expect, test, type Page } from "@playwright/test";
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  openViewer,
} from "./helpers";

/** T7d2 : la visionneuse plein écran remplace l'écran Résultats. Rendus posés directement
 *  dans le dossier du projet (comme les écrirait Blender), un par vue automatique, pour
 *  tester sans lancer de rendu : ouverture depuis le cadre Rendus, une carte, la sélection
 *  et la bande du plan ; ← → et flèches ; Échap ; place des commentaires ; thème sombre ;
 *  plus de bouton « ← Résultats » ni « Ouvrir l'éditeur → » ; la mesure la compte à part. */

const DATA = path.resolve(".e2e-data");
const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<string> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 650],
    [800, 650],
    [800, 350],
    [300, 350],
    [300, 650],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  return /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
}

function seedRender(
  projectId: string,
  cameraId: string,
  name: string,
  i: number,
) {
  const runId = randomUUID();
  const dir = path.join(DATA, projectId, "runs", runId, "blender");
  mkdirSync(dir, { recursive: true });
  copyFileSync(PHOTO, path.join(dir, "preview.png"));
  writeFileSync(
    path.join(dir, "render.json"),
    JSON.stringify({
      run_id: runId,
      camera_id: cameraId,
      camera_name: name,
      samples: 16,
      device: "cpu",
      render_s: 3,
      width_px: 640,
      height_px: 480,
      created_at: new Date(Date.now() - i * 60_000).toISOString(),
    }),
  );
}

/** Clarté entre 0 et 1 d'une couleur calculée : rgb(…), color(srgb …), oklch/oklab(…). */
const lightness = (css: string) => {
  const m = css.match(/\d*\.?\d+/g)!.map(Number);
  if (/^okl(ch|ab)/.test(css)) return m[0]! > 1 ? m[0]! / 100 : m[0]!;
  const scale = css.startsWith("color(") ? 1 : 255;
  return (m[0]! + m[1]! + m[2]!) / (3 * scale);
};

test("T7d2 : visionneuse plein écran des rendus, à la place de l'écran Résultats", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture image.png absente");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  const id = await createRoom(page, "E2E visionneuse");
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, unknown>;
  };
  const lid = Object.keys(project.levels)[0]!;
  const posed = await page.request.post(
    `/api/projects/${id}/levels/${lid}/views/auto`,
    { data: { aspect: "4:3" } },
  );
  expect(posed.ok()).toBeTruthy();
  const after = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<
      string,
      { elements: Record<string, { id: string; type: string; name?: string }> }
    >;
  };
  const cams = Object.values(after.levels[lid]!.elements).filter(
    (e) => e.type === "camera",
  );
  expect(cams.length).toBeGreaterThanOrEqual(2);
  cams.forEach((c, i) => seedRender(id, c.id, c.name ?? `Vue ${i + 1}`, i));
  const n = cams.length;
  await page.reload();

  // Arrivée sur l'espace de travail (des rendus existent) ; plus d'écran de résultats.
  await expectSpace(page, "board");
  const nav = page.getByRole("navigation", { name: "Espaces du projet" });
  await expect(nav.getByRole("button", { name: "← Résultats" })).toHaveCount(0);
  await expect(
    nav.getByRole("button", { name: /Ouvrir l'éditeur/ }),
  ).toHaveCount(0);
  await expect(page.locator(".board-item.kind-render")).toHaveCount(n);

  // « Voir en grand » dans l'en-tête du cadre Rendus : la première vue, en grand.
  const viewer = await openViewer(page);
  await expect(viewer.locator(".renders-grid img")).toHaveCount(1);
  await expect(viewer).toContainText(`1 / ${n}`);
  await expect(
    viewer.getByRole("button", { name: "Affiner cette vue" }),
  ).toBeVisible();
  // Rien de visible pour les commentaires avant B2, ni « agrandir » (redondant en plein
  // écran) : décisions PO du 18 oct.
  await expect(
    viewer.getByRole("complementary", { name: "Commentaires de la vue" }),
  ).toHaveCount(0);
  await expect(viewer.getByRole("link", { name: "agrandir" })).toBeHidden();
  for (const sel of [".render-viewer", ".render-viewer-head"]) {
    const bg = await page
      .locator(sel)
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(lightness(bg), `${sel} : ${bg}`).toBeLessThan(0.35);
  }
  await page.screenshot({
    path: path.join(SHOTS, "t7d2_visionneuse_sombre.png"),
  });
  await page.emulateMedia({ colorScheme: "light" });
  await page.screenshot({
    path: path.join(SHOTS, "t7d2_visionneuse_clair.png"),
  });

  // ← → et les flèches passent d'une vue à l'autre, en boucle.
  await page.keyboard.press("ArrowRight");
  await expect(viewer).toContainText(`2 / ${n}`);
  await viewer.getByRole("button", { name: "Vue précédente" }).click();
  await expect(viewer).toContainText(`1 / ${n}`);
  await page.keyboard.press("ArrowLeft");
  await expect(viewer).toContainText(`${n} / ${n}`);
  await viewer.getByRole("button", { name: "Vue suivante" }).click();
  await expect(viewer).toContainText(`1 / ${n}`);

  // Échap ferme ; on reste dans l'espace de travail.
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expectSpace(page, "board");

  // Double-clic sur une carte de rendu, puis « Voir en grand » de la barre de sélection.
  const cards = page.locator(".board-item.kind-render");
  await cards.nth(1).dblclick();
  await expect(viewer).toBeVisible();
  await viewer.getByRole("button", { name: "Fermer la visionneuse" }).click();
  await expect(viewer).toHaveCount(0);
  await cards.first().click();
  await page
    .getByRole("button", { name: "Voir en grand", exact: true })
    .last()
    .click();
  await expect(viewer).toBeVisible();
  await page.keyboard.press("Escape");

  // La bande des rendus, sous le plan : une vignette ouvre la visionneuse sur sa vue.
  await goSpace(page, "plan");
  const strip = page.getByRole("navigation", { name: "Bande de rendus" });
  await expect(strip.locator("li")).toHaveCount(n);
  await strip
    .locator("li button")
    .nth(n - 1)
    .click();
  await expect(viewer).toContainText(`${n} / ${n}`);
  await page.keyboard.press("Escape");
  await expectSpace(page, "plan");

  // Regarder ses rendus n'est pas un geste : compté à part, sans aller-retour.
  const metrics = async () =>
    (await (await page.request.get(`/api/projects/${id}/metrics`)).json()) as {
      viewer_opens: number;
      results_editor_trips: number;
    };
  await expect.poll(async () => (await metrics()).viewer_opens).toBe(4);
  expect((await metrics()).results_editor_trips).toBe(0);

  // Ouverte depuis l'espace de travail, Ctrl+Z annule dans le plan (ce qu'on y fait, retirer
  // ou remplacer un meuble, est dans le plan), Ctrl+Maj+Z rétablit.
  const cursor = async () =>
    (
      (await (await page.request.get(`/api/projects/${id}`)).json()) as {
        history_cursor: number;
      }
    ).history_cursor;
  const start = await cursor();
  await openViewer(page);
  await page.keyboard.press("Control+z");
  await expect.poll(cursor).toBe(start - 1);
  await page.keyboard.press("Control+Shift+z");
  await expect.poll(cursor).toBe(start);
});
