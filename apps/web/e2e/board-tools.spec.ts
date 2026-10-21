import { expect, test } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** T7c, KAN-77 (MH10) : la barre de gauche n'a plus que Sélection, Main, Post-it, Cadre,
 *  Commentaire, puis Annuler et Rétablir à part (Lien et Importer sont dans les cadres :
 *  e2e/kan77.spec.ts) ; les actions de la sélection n'apparaissent qu'avec une sélection
 *  (KAN-64 : à la place de l'Assistant, dans le panneau de droite). Aucune requête externe. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

test("espace de travail : barre de gauche, barre de la sélection", async ({
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

  // Plus de « Ajouter une source » dans la barre du haut ; KAN-77 : plus de Lien ni
  // d'Importer dans la barre de gauche.
  await expect(page.locator(".topbar .add-source")).toHaveCount(0);
  const rail = page.getByRole("toolbar", {
    name: "Outils de l'espace de travail",
  });
  await expect(rail.getByRole("button")).toHaveCount(7);
  const names = await rail
    .getByRole("button")
    .evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")));
  expect(names).toEqual([
    "Sélection",
    "Main",
    "Post-it",
    "Cadre",
    "Commentaire",
    "Annuler dans l'espace de travail",
    "Rétablir dans l'espace de travail",
  ]);

  // Sans sélection, pas d'actions de sélection, l'Assistant ; avec une sélection (KAN-64),
  // ses actions prennent la place de l'Assistant dans le panneau de droite, jamais l'une
  // sur l'autre.
  const bar = page.getByRole("toolbar", { name: "Actions de la sélection" });
  const composer = page.getByRole("region", {
    name: "Compositeur",
    exact: true,
  });
  await expect(bar).toHaveCount(0);
  await expect(composer).toBeVisible();
  const board = page.getByRole("region", { name: "Espace de travail" });
  await board.locator(".board-item.kind-plan .board-plan-head").click({
    position: { x: 20, y: 10 },
  });
  await expect(bar).toBeVisible();
  await expect(
    bar.getByRole("button", { name: "Modifier le plan" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".side")
      .getByRole("toolbar", { name: "Actions de la sélection" }),
  ).toBeVisible();
  await expect(composer).toHaveCount(0);
  await page.keyboard.press("Escape");
  await board.locator(".board-stage").click({ position: { x: 5, y: 5 } });
  await expect(bar).toHaveCount(0);
  await expect(composer).toBeVisible();
  expect(outside).toEqual([]);
});
