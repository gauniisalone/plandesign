import { expect, test } from "@playwright/test";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** T3 : le cadre Plan vide. Il propose « Tracer moi-même » et « Générer le plan » ; sans rien
 *  à lire, l'écran le dit ; avec une surface dans la note Exigences, « Générer le plan »
 *  construit le plan de départ et le cadre montre le plan. Aucune requête externe. */

test("cadre Plan vide : tracer ou générer, progression, plan de départ depuis les exigences", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  const id = await createEmptyProject(page, "Plan vide");
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const plan = board.locator(".board-item.kind-plan");
  await expect(plan).toContainText("Votre plan");
  await expect(
    plan.getByRole("button", { name: "Tracer moi-même" }),
  ).toBeVisible();
  const generate = plan.getByRole("button", { name: "Générer le plan" });
  await expect(generate).toBeVisible();
  await expect(plan).not.toContainText("Modifier le plan");

  // Rien à lire : l'écran le dit, rien ne part.
  await generate.click();
  await expect(
    page.getByText(/Rien à lire pour générer le plan/),
  ).toBeVisible();

  // Une surface dans le compositeur (T7b) : le plan de départ se construit depuis le cadre.
  const card = page.getByRole("region", { name: "Compositeur" });
  await card.getByRole("tab", { name: "Questions guidées" }).click();
  await card.getByLabel("Surface (m²)").fill("24");
  await expect
    .poll(async () => {
      const p = (await (
        await page.request.get(`/api/projects/${id}`)
      ).json()) as {
        pending_answers: { area_m2: number } | null;
      };
      return p.pending_answers?.area_m2 ?? null;
    })
    .toBe(24);
  await generate.click();
  await expect(plan.locator("img.board-plan")).toBeVisible({ timeout: 30_000 });
  await expect(plan).toContainText("Modifier le plan");
  await expect(plan).not.toContainText("Votre plan");
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, { type: string }> }>;
  };
  const level = Object.values(project.levels)[0]!;
  expect(
    Object.values(level.elements).filter((e) => e.type === "wall").length,
  ).toBeGreaterThanOrEqual(4);
  // « Tracer moi-même » n'a plus lieu d'être ; « Modifier le plan » mène au plan.
  await plan.getByRole("button", { name: "Modifier le plan" }).click();
  await expectSpace(page, "plan");
  expect(outside).toEqual([]);
});
