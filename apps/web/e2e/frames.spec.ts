import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

// T7d1 : isolation des e2e lourds : aucun test ne commence tant que le serveur calcule
// encore pour le précédent (même s'il a échoué).
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** T2 : cadres typés du tableau. Les cadres « Photos de la pièce » et « Inspirations »
 *  sont là dès le départ avec leur consigne ; une image déposée dans Inspirations devient
 *  une image jointe cochée Référence, visible ; une photo déposée dans Photos de la pièce
 *  entre dans le projet et prend sa place dans le cadre. Aucune requête externe. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);

/** Dépose un fichier sur le tableau, au centre d'un cadre (événement drop réel). */
async function dropInto(
  page: Page,
  frame: Locator,
  name: string,
  bytes: Buffer,
) {
  const box = (await frame.boundingBox())!;
  const dt = await page.evaluateHandle(
    ([data, fileName]) => {
      const dt = new DataTransfer();
      dt.items.add(
        new File([new Uint8Array(data as number[])], fileName as string, {
          type: "image/png",
        }),
      );
      return dt;
    },
    [Array.from(bytes), name] as const,
  );
  await frame.dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
}

test("cadres typés : consignes, inspiration cochée Référence, photo dans son cadre", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  const id = await createEmptyProject(page, "Cadres");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const inspirations = board.locator(
    ".board-item.kind-frame.role-inspirations",
  );
  await expect(photos).toContainText("Photos de la pièce");
  // T7d1 : états vides courts ; l'explication est dans l'infobulle.
  await expect(photos).toContainText("Déposez vos photos");
  await expect(photos.locator(".board-frame-hint")).toHaveAttribute(
    "title",
    /photos réelles de la pièce/,
  );
  await expect(inspirations).toContainText("images d'ambiance");

  // Une image dans Inspirations : jointe, visible, cochée Référence, dans le cadre.
  await dropInto(page, inspirations, "chalet.png", PNG);
  const inspiration = board.locator(".board-item.kind-file.reference");
  await expect(inspiration).toHaveCount(1);
  await expect(inspiration.locator("img")).toBeVisible();
  await expect(inspiration).toContainText("chalet.png");
  await expect(inspirations).not.toContainText("images d'ambiance");
  const insideInspirations = await inspiration.evaluate((el) => {
    const frame = el
      .closest(".board-world")
      ?.querySelector(".role-inspirations");
    const a = el.getBoundingClientRect();
    const b = frame?.getBoundingClientRect();
    return !!b && a.left >= b.left && a.top >= b.top && a.right <= b.right;
  });
  expect(insideInspirations).toBe(true);
  // Elle n'est pas une photo du projet.
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    images: Record<string, unknown>;
  };
  expect(Object.keys(project.images)).toHaveLength(0);

  // Une photo dans Photos de la pièce : entre dans le projet, dans son cadre.
  await dropInto(page, photos, "piece.png", PNG);
  const photo = board.locator(".board-item.kind-photo");
  await expect(photo).toHaveCount(1, { timeout: 60_000 });
  await expect(photos).not.toContainText("Déposez vos photos");
  await expect
    .poll(async () => {
      const p = (await (
        await page.request.get(`/api/projects/${id}`)
      ).json()) as {
        images: Record<string, unknown>;
      };
      return Object.keys(p.images).length;
    })
    .toBe(1);
  // Le volet du rendu contextualisé voit la référence cochée.
  await photo.click();
  await page
    .locator(".side")
    .getByRole("button", { name: "Rendu contextualisé…" })
    .click();
  const panel = page.getByRole("region", { name: "Rendu contextualisé" });
  await expect(
    panel.getByRole("list", { name: "Références cochées" }),
  ).toContainText("chalet.png");
  expect(outside).toEqual([]);
});
