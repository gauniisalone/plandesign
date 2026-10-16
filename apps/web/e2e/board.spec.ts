import { expect, test, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** B2 : le tableau du projet. Le plan y est posé ; une note s'écrit, s'annule et se
 *  rétablit sans toucher l'historique du plan ; redimensionner le cadre du plan ne change
 *  pas sa géométrie ; la disposition revient à la réouverture ; un envoi périmé (autre
 *  onglet) recharge le tableau et le dit. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);

async function projectJson(page: Page, id: string) {
  return (await (await page.request.get(`/api/projects/${id}`)).json()) as {
    history_cursor: number;
    levels: Record<string, { vertices: unknown }>;
  };
}

test("tableau : plan posé, note, annuler séparé du plan, cadre du plan, réouverture, conflit", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const id = await createEmptyProject(page, "Tableau");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const plan = board.locator(".board-item.kind-plan");
  await expect(plan).toBeVisible();
  await expect(plan).toContainText("Modifier le plan");
  const before = await projectJson(page, id);

  // Une note : outil Note, un clic, du texte.
  await board.getByRole("button", { name: "Note" }).click();
  const stage = board.locator(".board-stage");
  const box = (await stage.boundingBox())!;
  await stage.click({ position: { x: 120, y: box.height - 160 } });
  const text = board.getByRole("textbox", { name: "Texte de la note" });
  await text.fill("Idée : une banquette sous la fenêtre");
  await stage.click({ position: { x: box.width - 60, y: 40 } });
  const note = board.locator(".board-item.kind-note", { hasText: "banquette" });
  await expect(note).toBeVisible();
  // B4 : l'indicateur d'enregistrement de l'atelier, dans la barre du haut.
  const status = page.getByRole("status", {
    name: "Enregistrement",
    exact: true,
  });
  await expect(status).toContainText("Enregistré", { timeout: 10_000 });

  // Annuler / rétablir du tableau : l'historique du plan ne bouge pas.
  await page.keyboard.press("Control+z");
  await expect(note).toHaveCount(0);
  await page.keyboard.press("Control+Shift+z");
  await expect(note).toHaveCount(1);
  await expect(status).toContainText("Enregistré", { timeout: 10_000 });
  expect((await projectJson(page, id)).history_cursor).toBe(
    before.history_cursor,
  );

  // Redimensionner le cadre du plan : sa géométrie ne change pas.
  await plan.locator(".board-plan-head").click();
  const handle = plan.locator(".board-resize");
  const h = (await handle.boundingBox())!;
  await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
  await page.mouse.down();
  await page.mouse.move(h.x - 200, h.y - 150, { steps: 5 });
  await page.mouse.up();
  await expect(status).toContainText("Enregistré", { timeout: 10_000 });
  const after = await projectJson(page, id);
  expect(after.levels).toEqual(before.levels);
  const saved = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as {
    revision: number;
    items: { kind: string; w: number; text: string }[];
  };
  expect(saved.items.find((i) => i.kind === "plan")!.w).toBeLessThan(900);

  // Réouverture : la note est là. B4 : une photo arrivée entre-temps entre dans le tableau
  // (synchronisation à l'ouverture) ; l'indicateur dit « Enregistré » sans geste, et y reste.
  await page.request.post(`/api/projects/${id}/images`, {
    multipart: { file: { name: "p.png", mimeType: "image/png", buffer: PNG } },
  });
  await page.reload();
  await expectSpace(page, "board");
  await expect(note).toBeVisible();
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1);
  await expect(status).toHaveText("Enregistré");
  await page.waitForTimeout(2_000);
  await expect(status).toHaveText("Enregistré");

  // B3 : entrer dans le plan depuis son cadre (zoom continu), puis « ← Tableau » rend la vue
  // telle qu'elle était, sans que ce zoom d'entrée soit enregistré.
  const world = board.locator(".board-world");
  await expect(status).toContainText("Enregistré", { timeout: 10_000 });
  const seen = await world.getAttribute("style");
  await plan
    .locator(".board-plan-head")
    .getByRole("button", { name: "Modifier le plan" })
    .click();
  await expectSpace(page, "plan");
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await page
    .getByRole("navigation", { name: "Espaces du projet" })
    .getByRole("button", { name: "← Espace de travail" })
    .click();
  await expectSpace(page, "board");
  await expect(world).toHaveAttribute("style", seen ?? "");

  // Un autre onglet enregistre entre-temps : le geste suivant est refusé, le tableau
  // rechargé, et l'atelier le dit.
  const other = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as {
    revision: number;
  };
  await page.request.put(`/api/projects/${id}/board`, {
    data: { ...other, view: { x: 0, y: 0, zoom: 1 } },
  });
  await note.dragTo(stage, {
    targetPosition: { x: box.width / 2, y: box.height / 2 },
  });
  await expect(board.getByRole("alert")).toContainText("enregistré ailleurs", {
    timeout: 10_000,
  });
  await page.screenshot({
    path: path.resolve("e2e/screenshots/b2_tableau.png"),
  });

  // A1 : un geste suivi d'un rechargement immédiat n'est pas perdu (envoi `keepalive`
  // quand la page se cache ou se ferme).
  const noteBefore = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: { kind: string; x: number }[] };
  const xBefore = noteBefore.items.find((i) => i.kind === "note")!.x;
  await note.dragTo(stage, {
    targetPosition: { x: box.width - 200, y: box.height - 200 },
  });
  await page.reload();
  await expectSpace(page, "board");
  await expect
    .poll(async () => {
      const after = (await (
        await page.request.get(`/api/projects/${id}/board`)
      ).json()) as { items: { kind: string; x: number }[] };
      return after.items.find((i) => i.kind === "note")!.x;
    })
    .not.toBe(xBefore);
});
