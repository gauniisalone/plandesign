import { expect, test, type Page } from "@playwright/test";
import { copyFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import path from "node:path";
import { createEmptyProject, goSpace } from "./helpers";

/** US2 : galerie des rendus du projet. Les rendus sont posés directement dans le dossier du
 *  projet (comme les écrirait Blender : `render.json` et images à côté), pour tester la
 *  galerie sans lancer de rendu : groupes par vue, favori et filtre, renommer, supprimer
 *  puis annuler, comparer, favoris en zip. */

const DATA = path.resolve(".e2e-data");
const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const SHOTS = path.resolve("e2e/screenshots");

function seedRender(
  projectId: string,
  name: string,
  opts: { minutesAgo: number; inspiration?: boolean; viewGroup?: string },
): string {
  const runId = randomUUID();
  const dir = path.join(DATA, projectId, "runs", runId, "blender");
  mkdirSync(dir, { recursive: true });
  copyFileSync(PHOTO, path.join(dir, "preview.png"));
  const created = new Date(Date.now() - opts.minutesAgo * 60_000).toISOString();
  writeFileSync(
    path.join(dir, "render.json"),
    JSON.stringify({
      run_id: runId,
      camera_id: null,
      camera_name: name,
      samples: 16,
      device: "cpu",
      render_s: 3,
      width_px: 640,
      height_px: 480,
      created_at: created,
    }),
  );
  if (opts.inspiration) {
    copyFileSync(PHOTO, path.join(dir, "inspiration.png"));
    writeFileSync(
      path.join(dir, "inspiration.json"),
      JSON.stringify({
        run_id: runId,
        mode: "img2img",
        strength: 0.8,
        control_scale: 1.2,
        prompt: "essai",
        seed: 0,
        model_ids: [],
        duration_s: 1,
        created_at: created,
      }),
    );
  }
  if (opts.viewGroup)
    writeFileSync(
      path.join(dir, "gallery.json"),
      JSON.stringify({
        view_group: opts.viewGroup,
        ambiance: "Naturel · chaleureux",
      }),
    );
  return runId;
}

interface Listed {
  run_id: string;
  title: string | null;
  favorites: string[];
}

async function listed(page: Page, id: string): Promise<Listed[]> {
  return (await (
    await page.request.get(`/api/projects/${id}/renders`)
  ).json()) as Listed[];
}

test("US2 : galerie groupée par vue, favori et filtre, renommer, supprimer et annuler, comparer, zip", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture image.png absente");
  const id = await createEmptyProject(page, "E2E galerie");
  const a = seedRender(id, "Vue A", {
    minutesAgo: 1,
    inspiration: true,
    viewGroup: "overall",
  });
  const b = seedRender(id, "Vue B", { minutesAgo: 5 });
  seedRender(id, "Vue C", { minutesAgo: 9 });
  await page.goto(`/#/projects/${id}`);
  await goSpace(page, "renders");
  const gallery = page.getByRole("region", { name: "Galerie des rendus" });
  await expect(gallery).toBeVisible();

  // Groupes par vue, le plus récent d'abord ; l'ambiance et la vue sont dites.
  const overall = gallery.getByRole("region", { name: "Vues d'ensemble" });
  const hand = gallery.getByRole("region", { name: "Points de vue à la main" });
  await expect(overall.locator(".gallery2-card")).toHaveCount(1);
  await expect(overall).toContainText("Naturel · chaleureux");
  await expect(hand.locator(".gallery2-card strong")).toHaveText([
    "Vue B",
    "Vue C",
  ]);
  const cardA = gallery.locator(".gallery2-card", { hasText: "Vue A" });
  // Bascule des images : inspiration puis Blender.
  const variants = cardA.getByRole("group", { name: "Images de Vue A" });
  await expect(
    variants.getByRole("button", { name: "Inspiration" }),
  ).toHaveAttribute("aria-pressed", "true");

  // Favori sur l'inspiration de A, puis filtre « Favoris ».
  const star = cardA.getByRole("button", {
    name: "Favori : Vue A (Inspiration)",
  });
  await star.click();
  await expect(star).toHaveAttribute("aria-pressed", "true");
  await expect
    .poll(
      async () =>
        (await listed(page, id)).find((e) => e.run_id === a)?.favorites,
    )
    .toEqual(["inspiration"]);
  await gallery.getByRole("button", { name: /Favoris \(1\)/ }).click();
  await expect(gallery.locator(".gallery2-card")).toHaveCount(1);
  await page.screenshot({ path: path.join(SHOTS, "us2_favoris.png") });
  await gallery.getByRole("button", { name: "Tous" }).click();
  await expect(gallery.locator(".gallery2-card")).toHaveCount(3);

  // Favoris en zip.
  const zipLink = gallery.getByRole("link", { name: "Favoris en zip" });
  const zipUrl = await zipLink.getAttribute("href");
  const zip = await page.request.get(zipUrl!);
  expect(zip.status()).toBe(200);
  expect(zip.headers()["content-type"]).toBe("application/zip");

  // Renommer A.
  await cardA.getByRole("button", { name: "Renommer « Vue A »" }).click();
  const input = gallery.getByRole("textbox", {
    name: "Nouveau titre de « Vue A »",
  });
  await input.fill("Séjour du matin");
  await input.press("Enter");
  const cardSejour = gallery.locator(".gallery2-card", {
    hasText: "Séjour du matin",
  });
  await expect(cardSejour).toBeVisible();
  await expect(cardSejour).toContainText("Vue A"); // le point de vue reste dit
  await expect
    .poll(
      async () => (await listed(page, id)).find((e) => e.run_id === a)?.title,
    )
    .toBe("Séjour du matin");

  // Supprimer B : confirmation, puis « Annuler » dans le bandeau.
  const cardB = gallery.locator(".gallery2-card", { hasText: "Vue B" });
  await cardB.getByRole("button", { name: "Supprimer Vue B" }).click();
  await cardB
    .getByRole("group", { name: "Confirmer" })
    .getByRole("button", { name: "Supprimer" })
    .click();
  await expect(
    gallery.locator(".gallery2-card", { hasText: "Vue B" }),
  ).toHaveCount(0);
  const toast = page.getByRole("status").filter({ hasText: "supprimé" });
  await expect(toast).toContainText("Rendu « Vue B » supprimé.");
  await page.screenshot({ path: path.join(SHOTS, "us2_supprime_annuler.png") });
  expect((await listed(page, id)).map((e) => e.run_id)).not.toContain(b);
  expect(existsSync(path.join(DATA, id, "runs", ".trash", b))).toBe(true);
  await toast.getByRole("button", { name: "Annuler" }).click();
  await expect(
    gallery.locator(".gallery2-card", { hasText: "Vue B" }),
  ).toHaveCount(1);
  expect((await listed(page, id)).map((e) => e.run_id)).toContain(b);
  // « Garder » annule la confirmation sans rien supprimer.
  await cardB.getByRole("button", { name: "Supprimer Vue B" }).click();
  await cardB.getByRole("button", { name: "Garder" }).click();
  await expect(cardB).toBeVisible();

  // Comparer deux images : côte à côte, puis curseur avant/après.
  await cardSejour
    .getByRole("button", { name: /^Comparer Séjour du matin/ })
    .click();
  await cardB.getByRole("button", { name: /^Comparer Vue B/ }).click();
  await gallery.getByRole("button", { name: "Comparer (2/2)" }).click();
  const dialog = page.getByRole("dialog", { name: "Comparer deux images" });
  await expect(dialog.locator(".compare-side img")).toHaveCount(2);
  await dialog.getByRole("button", { name: "Avant / après" }).click();
  const slider = dialog.getByRole("slider", { name: "Curseur avant/après" });
  await slider.fill("25");
  await expect(dialog.locator(".compare-top")).toHaveAttribute(
    "style",
    /inset\(0px 75% 0px 0px\)|inset\(0 75% 0 0\)/,
  );
  await page.screenshot({ path: path.join(SHOTS, "us2_comparer.png") });
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);

  // Agrandir la galerie, puis Échap.
  await gallery.getByRole("button", { name: "Agrandir la galerie" }).click();
  await expect(page.locator(".gallery2.expanded")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".gallery2.expanded")).toHaveCount(0);
});
