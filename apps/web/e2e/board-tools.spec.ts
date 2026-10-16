import { expect, test } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** T7c (maquette d'augus) : dans l'espace de travail, l'import est dans la barre d'outils de
 *  gauche (« Photo ou plan de la pièce » ouvre « Ajouter une source », « Fichiers » joint des
 *  fichiers) ; la barre des actions de la sélection n'apparaît qu'avec une sélection, en bas,
 *  au-dessus du compositeur. Aucune requête externe. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

test("espace de travail : import dans la barre de gauche, barre de la sélection en bas", async ({
  page,
}) => {
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  await createEmptyProject(page, "Outils T7c");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");

  // Plus de « Ajouter une source » dans la barre du haut : il est dans « Importer ».
  await expect(page.locator(".topbar .add-source")).toHaveCount(0);
  const rail = page.getByRole("toolbar", {
    name: "Outils de l'espace de travail",
  });
  await rail.getByRole("button", { name: "Importer" }).click();
  const menu = page.getByRole("menu", { name: "Importer" });
  await expect(menu.getByRole("menuitem")).toHaveCount(2);
  await expect(menu).toContainText("Fichiers dans l'espace de travail");
  await menu
    .getByRole("menuitem", { name: /Photo ou plan de la pièce/ })
    .click();
  await expectSpace(page, "source");
  await expect(
    page.getByRole("heading", { name: "Ajouter une source" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Ajouter une source/ }).click();
  await goSpace(page, "board");

  // Sans sélection, pas de barre ; avec une sélection, en bas, au-dessus du compositeur.
  const bar = page.getByRole("toolbar", { name: "Actions de la sélection" });
  await expect(bar).toHaveCount(0);
  const board = page.getByRole("region", { name: "Espace de travail" });
  await board.locator(".board-item.kind-plan .board-plan-head").click({
    position: { x: 20, y: 10 },
  });
  await expect(bar).toBeVisible();
  await expect(
    bar.getByRole("button", { name: "Modifier le plan" }),
  ).toBeVisible();
  const composer = page.getByRole("region", { name: "Compositeur" });
  const barBox = (await bar.boundingBox())!;
  const composerBox = (await composer.boundingBox())!;
  expect(barBox.y + barBox.height).toBeLessThanOrEqual(composerBox.y + 1);
  await page.keyboard.press("Escape");
  await board.locator(".board-stage").click({ position: { x: 5, y: 5 } });
  await expect(bar).toHaveCount(0);
  expect(outside).toEqual([]);
});
