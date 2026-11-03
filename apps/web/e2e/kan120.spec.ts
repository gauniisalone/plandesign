import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { reloadApp } from "./app-ready";
import {
  createEmptyProject,
  goSpace,
  hasWeights,
  PHOTO_WEIGHTS,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-120 : le plan se génère sur un bouton du node Photos de la pièce, plus à l'import d'une
 *  photo. Aucune analyse avant le clic ; une seule construction pour N photos (la fusion KAN-95
 *  les lit toutes) ; puis « Régénérer le plan », avec son avertissement, en une seule étape
 *  d'annulation. La règle serveur (replace, meubles, Ctrl+Z) est couverte par
 *  tests/integration/test_kan120_replace.py. */

const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);
const SHOTS = path.resolve("e2e/screenshots");

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

async function openBoard(page: Page, name: string) {
  const id = await createEmptyProject(page, name);
  // Chemin automatique coupé : aucun rendu après le plan.
  const patched = await page.request.patch(`/api/projects/${id}`, {
    data: { render: { auto_path: false } },
  });
  expect(patched.ok()).toBeTruthy();
  await reloadApp(page);
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  return {
    id,
    board,
    photos: board.locator(".board-item.kind-frame.role-photos"),
    plan: board.locator(".board-item.kind-plan"),
  };
}

async function importPhotos(page: Page, photos: ReturnType<typeof page.locator>, n: number) {
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (
    await chooser
  ).setFiles(
    Array.from({ length: n }, (_, i) => ({
      name: `piece-${i + 1}.png`,
      mimeType: "image/png",
      buffer: PNG,
    })),
  );
}

/** Les demandes qui lanceraient une analyse : construction du plan, profondeur envoyée. */
function watchAnalysis(page: Page) {
  const calls: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() !== "GET" && /\/(autoplan|depth|photo-fusion|attic-fusion)$/.test(p))
      calls.push(`${r.method()} ${p}${new URL(r.url()).search}`);
  });
  return calls;
}

test("KAN-120 : une photo importée ne lance rien ; le node propose le bouton", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const calls = watchAnalysis(page);
  const { board, photos, plan } = await openBoard(page, "E2E KAN-120 import");
  await importPhotos(page, photos, 1);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 30_000,
  });
  const button = photos.getByRole("button", {
    name: "Générer le plan à partir de 1 photo",
  });
  await expect(button).toBeVisible();
  await expect(
    photos.getByText(
      "Ajoutez 2 ou 3 photos prises depuis d'autres coins de la pièce : le plan sera plus sûr.",
    ),
  ).toBeVisible();
  // Rien ne part tout seul, même avec une seule photo (décision du PO).
  await page.waitForTimeout(3000);
  expect(calls).toEqual([]);
  await expect(plan.locator("img.board-plan")).toHaveCount(0);
  // Le cadre Plan vide lit les exigences ; la bascule « au dépôt » a disparu.
  await expect(
    plan.getByRole("button", { name: "Générer le plan d'après mes exigences" }),
  ).toBeVisible();
  await expect(page.getByText("Générer automatiquement au dépôt d'une photo")).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "kan120_une_photo.png") });

  // Le clic construit le plan (une seule demande).
  await button.click();
  await expect(plan.locator("img.board-plan")).toBeVisible({ timeout: 180_000 });
  expect(calls.filter((c) => c.includes("/autoplan"))).toHaveLength(1);
  await expect(photos.getByRole("button", { name: "Régénérer le plan" })).toBeVisible();
});

test("KAN-120 : trois photos, une seule analyse, « 3 photos utilisées », puis Régénérer", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  const calls = watchAnalysis(page);
  const jobId = "00000000-0000-4000-8000-000000000120";
  let target: { pid: string; lid: string; imageId: string } | null = null;
  let replaced = false;
  // La construction elle-même est simulée (copie sans poids ni GPU) : le serveur pose quatre
  // murs et trois caméras réelles, la tâche les rend avec ce que la fusion KAN-95 y écrit.
  await page.route("**/api/projects/*/levels/*/images/*/autoplan*", (route) => {
    const url = new URL(route.request().url());
    const m = url.pathname.match(
      /projects\/([^/]+)\/levels\/([^/]+)\/images\/([^/]+)\/autoplan/,
    );
    target = m ? { pid: m[1]!, lid: m[2]!, imageId: m[3]! } : null;
    replaced = url.searchParams.get("replace") === "true";
    return route.fulfill({
      status: 202,
      json: { id: jobId, kind: "autoplan", status: "running", result: null, error: null },
    });
  });
  let built = false;
  await page.route(`**/api/projects/*/jobs/${jobId}`, async (route) => {
    const t = target!;
    const ops = `/api/projects/${t.pid}/levels/${t.lid}/ops`;
    if (!built) {
      built = true;
      const corners = [
        [0, 0],
        [4, 0],
        [4, 3],
        [0, 3],
      ];
      for (let i = 0; i < 4; i++) {
        const [a, b] = [corners[i]!, corners[(i + 1) % 4]!];
        await page.request.post(ops, {
          data: { op: "add_wall", start: { x: a[0], y: a[1] }, end: { x: b[0], y: b[1] } },
        });
      }
      for (let i = 0; i < 3; i++)
        await page.request.post(ops, {
          data: { op: "add_camera", position: { x: 1 + i, y: 1 }, yaw: 0.5 },
        });
    }
    const project = (await (await page.request.get(`/api/projects/${t.pid}`)).json()) as {
      images: Record<string, unknown>;
      levels: Record<string, { elements: Record<string, Record<string, unknown>> }>;
    };
    const ids = Object.keys(project.images);
    const cameras = Object.values(project.levels[t.lid]!.elements).filter(
      (e) => e.type === "camera",
    );
    cameras.forEach((camera, i) => {
      camera.image_id = ids[i];
      camera.metadata = {
        fusion_summary: "3 photos concordent à ±4 %",
        fusion_factor: 1,
        fusion: { kept: true, reason: "murs du fond", weight: 0.33, ridge_m: null, pitch_deg: null },
      };
    });
    return route.fulfill({
      json: {
        id: jobId,
        kind: "autoplan",
        status: "done",
        error: null,
        stage: "Plan prêt",
        result: {
          run: {
            run_id: "00000000-0000-4000-8000-000000000121",
            image_id: t.imageId,
            kind: "photo",
            method: "depth",
            backup: null,
            scores: {},
            stages: [],
            notices: replaced ? ["1 meuble hors des nouveaux murs retiré."] : [],
            low_confidence: false,
            estimate_confidence: null,
            close_up: false,
            project,
            result: {
              step: "autoplan",
              confidence: 0.6,
              assumptions: [],
              warnings: [],
              artifacts: [],
              duration_s: 0,
              model_ids: [],
              patch: [],
            },
          },
          report: { score: 1, suggestions: [], valid: true, violations: [], warnings: [] },
        },
      },
    });
  });

  const { board, photos } = await openBoard(page, "E2E KAN-120 trois photos");
  await importPhotos(page, photos, 3);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(3, {
    timeout: 30_000,
  });
  await expect(
    photos.getByText("Plusieurs angles : les photos seront recoupées entre elles."),
  ).toBeVisible();
  await page.waitForTimeout(2000);
  expect(calls).toEqual([]);

  await photos.getByRole("button", { name: "Générer le plan à partir de 3 photos" }).click();
  await expect(photos.getByText("3 photos utilisées.")).toBeVisible({ timeout: 30_000 });
  await expect(photos.getByText(/3 photos concordent à ±4 %/)).toBeVisible();
  await expect(board.locator(".board-photo-use.use-used")).toHaveCount(3);
  // Une seule construction pour les trois photos, sans `replace`.
  expect(calls.filter((c) => c.includes("/autoplan"))).toHaveLength(1);
  expect(calls.some((c) => c.includes("replace=true"))).toBe(false);
  await page.screenshot({ path: path.join(SHOTS, "kan120_trois_photos.png") });

  // Régénérer : l'avertissement d'abord ; Échap = Annuler, rien ne part.
  await photos.getByRole("button", { name: "Régénérer le plan" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Remplacer le plan actuel ?" });
  await expect(confirm).toBeVisible();
  await expect(confirm).toContainText("remplacera le plan actuel");
  await expect(confirm).toContainText(
    "Portes, fenêtres et éléments accrochés aux anciens murs sont refaits avec les nouveaux.",
  );
  await page.keyboard.press("Escape");
  await expect(confirm).toHaveCount(0);
  expect(calls.filter((c) => c.includes("/autoplan"))).toHaveLength(1);

  await photos.getByRole("button", { name: "Régénérer le plan" }).click();
  await page.screenshot({ path: path.join(SHOTS, "kan120_regenerer.png") });
  await confirm.getByRole("button", { name: "Régénérer le plan" }).click();
  const toast = page
    .getByRole("status")
    .filter({ hasText: "Plan régénéré. 1 meuble hors des nouveaux murs retiré." });
  await expect(toast).toBeVisible({ timeout: 30_000 });
  await expect(toast.getByRole("button", { name: "Annuler" })).toBeVisible();
  const autoplans = calls.filter((c) => c.includes("/autoplan"));
  expect(autoplans).toHaveLength(2);
  expect(autoplans[1]).toContain("replace=true");
});

test("KAN-120 : trois photos réelles, la fusion les utilise toutes (poids requis)", async ({
  page,
}) => {
  // Dépend des poids de profondeur (fusion KAN-95) : sautée sur une copie sans poids.
  test.skip(!(await hasWeights(page, PHOTO_WEIGHTS)), "poids de profondeur absents");
  test.setTimeout(400_000);
  const calls = watchAnalysis(page);
  const { board, photos, plan } = await openBoard(page, "E2E KAN-120 fusion réelle");
  await importPhotos(page, photos, 3);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(3, {
    timeout: 30_000,
  });
  await photos.getByRole("button", { name: "Générer le plan à partir de 3 photos" }).click();
  await expect(plan.locator("img.board-plan")).toBeVisible({ timeout: 360_000 });
  expect(calls.filter((c) => c.includes("/autoplan"))).toHaveLength(1);
  await expect(photos.getByText("3 photos utilisées.")).toBeVisible({ timeout: 30_000 });
});
