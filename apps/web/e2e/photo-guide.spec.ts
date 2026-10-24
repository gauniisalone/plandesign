import { expect, test, type Page } from "@playwright/test";
import { existsSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, goSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** KAN-91 L5 : le guide de prise de photo ne s'ouvre que par son lien (décision d'augus, D4),
 *  à côté de « Importer » du cadre Photos ; après l'import, une photo qui montre un détail est
 *  annoncée (alerte « gros plan », avec le lien du guide). La construction du plan est simulée
 *  pour la seule tâche : le critère (plan de moins de 2 m et photo qui ne voit pas loin) est
 *  couvert par tests/unit/test_close_up_kan91.py. */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const SHOTS = path.resolve("e2e/screenshots");
const GUIDE = "Guide de prise de photo";
const CLOSE_UP =
  "Cette photo montre un détail (un meuble, un coin) : prenez la pièce entière, ou ajoutez une mesure de référence.";

async function openBoard(page: Page, name: string, autoOnDrop: boolean) {
  const id = await createEmptyProject(page, name);
  // Chemin automatique coupé : aucun rendu après le plan.
  const patched = await page.request.patch(`/api/projects/${id}`, {
    data: { render: { auto_on_drop: autoOnDrop, auto_path: false } },
  });
  expect(patched.ok()).toBeTruthy();
  await reloadApp(page);
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  return {
    id,
    board,
    photos: board.locator(".board-item.kind-frame.role-photos"),
  };
}

test("KAN-91 L5 : le guide de prise de photo s'ouvre par son lien, jamais seul", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture absente");
  await page.setViewportSize({ width: 1440, height: 900 });
  const { board, photos } = await openBoard(page, "E2E guide de prise", false);
  const guide = page.getByRole("dialog", { name: GUIDE });
  await expect(guide).toHaveCount(0);

  // Cadre vide : le lien à côté de « Importer ».
  const row = photos.locator(".board-frame-import-row");
  await expect(row.getByRole("button", { name: "Importer" })).toBeVisible();
  await row.getByRole("button", { name: GUIDE }).click();
  await expect(guide).toBeVisible();
  for (const title of [
    "Reculez au maximum",
    "Grand angle",
    "Appareil à ~1,50 m",
    "Deux coins et le sol",
  ])
    await expect(guide.getByRole("heading", { name: title })).toBeVisible();
  await expect(guide.getByText("depuis l'angle opposé")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "kan91_guide.png") });
  await page.keyboard.press("Escape");
  await expect(guide).toHaveCount(0);

  // « Importer des photos » du guide ouvre le choix de fichiers du cadre ; après l'import,
  // le guide ne revient pas tout seul.
  await row.getByRole("button", { name: GUIDE }).click();
  const chooser = page.waitForEvent("filechooser");
  await guide.getByRole("button", { name: "Importer des photos" }).click();
  await expect(guide).toHaveCount(0);
  await (await chooser).setFiles(PHOTO);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 30_000,
  });
  await expect(guide).toHaveCount(0);
  await expect(page.locator(".board-closeup")).toHaveCount(0);

  // Cadre rempli : le lien dans l'en-tête, à côté du « + ».
  const head = photos.locator(".board-frame-actions");
  await expect(
    head.getByRole("button", { name: "Ajouter des photos" }),
  ).toBeVisible();
  await head.getByRole("button", { name: GUIDE }).click();
  await expect(guide).toBeVisible();
  await guide.getByRole("button", { name: "Fermer" }).click();
  await expect(guide).toHaveCount(0);
});

test("KAN-91 L5 : alerte « gros plan » après l'import, avec le guide", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture absente");
  await page.setViewportSize({ width: 1440, height: 900 });
  const jobId = "00000000-0000-4000-8000-0000000000c1";
  let target: { pid: string; lid: string; imageId: string } | null = null;
  await page.route("**/api/projects/*/levels/*/images/*/autoplan*", (route) => {
    const m = new URL(route.request().url()).pathname.match(
      /projects\/([^/]+)\/levels\/([^/]+)\/images\/([^/]+)\/autoplan/,
    );
    target = m ? { pid: m[1]!, lid: m[2]!, imageId: m[3]! } : null;
    return route.fulfill({
      status: 202,
      json: {
        id: jobId,
        kind: "autoplan",
        status: "running",
        result: null,
        error: null,
      },
    });
  });
  await page.route(`**/api/projects/*/jobs/${jobId}`, async (route) => {
    const t = target!;
    const project = await (
      await page.request.get(`/api/projects/${t.pid}`)
    ).json();
    return route.fulfill({
      json: {
        id: jobId,
        kind: "autoplan",
        status: "done",
        error: null,
        result: {
          run: {
            run_id: "00000000-0000-4000-8000-0000000000c2",
            image_id: t.imageId,
            kind: "photo",
            method: "lines",
            backup: null,
            scores: {},
            stages: [],
            notices: [],
            low_confidence: true,
            estimate_confidence: {
              level: "approximatif",
              label: "approximatif",
              reason: "pièce estimée à moins de 2,00 m de large",
              expected_error: 0.5,
              factors: [],
            },
            close_up: true,
            project,
            result: {
              step: "autoplan",
              confidence: 0.2,
              assumptions: [],
              warnings: [],
              artifacts: [],
              duration_s: 0,
              model_ids: [],
              patch: [],
            },
          },
          report: {
            score: 1,
            suggestions: [],
            valid: true,
            violations: [],
            warnings: [],
          },
        },
      },
    });
  });

  const { board, photos } = await openBoard(page, "E2E gros plan", true);
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (await chooser).setFiles(PHOTO);

  const alert = page.getByRole("alert").filter({ hasText: CLOSE_UP });
  await expect(alert).toBeVisible({ timeout: 30_000 });
  const photo = board.locator(".board-item.kind-photo");
  await expect(photo).toHaveClass(/photo-closeup/);
  // Le guide n'est pas ouvert d'office : seulement par le lien de l'alerte.
  const guide = page.getByRole("dialog", { name: GUIDE });
  await expect(guide).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "kan91_gros_plan.png") });
  await alert.getByRole("button", { name: GUIDE }).click();
  await expect(guide).toBeVisible();
  await expect(
    guide.getByRole("button", { name: "Importer des photos" }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(guide).toHaveCount(0);

  await alert.getByRole("button", { name: "fermer" }).click();
  await expect(alert).toHaveCount(0);
  await expect(photo).not.toHaveClass(/photo-closeup/);
});
