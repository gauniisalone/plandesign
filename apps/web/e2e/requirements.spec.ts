import { expect, test } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** T1 : la note Exigences du tableau. Une seule par tableau ; ses champs (questions guidées),
 *  son texte et la ligne « compris » ; le contenu est gardé dans le projet (réponses et
 *  consigne), relu à la réouverture ; le tableau garde sa place. Aucune requête externe. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

test("note Exigences : champs guidés, texte, compris, gardés dans le projet", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  const id = await createEmptyProject(page, "Exigences");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Tableau" });
  const card = board.locator(".board-item.kind-requirements");
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("Exigences");
  await expect(card).toContainText("Rien n'est obligatoire");

  // Les champs guidés et le texte : « compris » suit, le projet garde tout.
  await card.getByLabel("Surface (m²)").fill("24");
  await card.getByLabel("Chambres").fill("1");
  await card.getByLabel("Style").selectOption("chalet");
  await card.getByLabel("Vos envies et contraintes").fill("grenier chaleureux, bois et lin");
  await card.getByLabel("Vos envies et contraintes").blur();
  await expect(card).toContainText(/compris : .*24 m².*1 chambre.*chalet/);
  await expect(card).toContainText("chaleureux");
  await expect
    .poll(async () => {
      const project = (await (await page.request.get(`/api/projects/${id}`)).json()) as {
        pending_instruction: string | null;
        pending_answers: { area_m2: number; bedrooms: number; style: string } | null;
      };
      return [
        project.pending_instruction,
        project.pending_answers?.area_m2,
        project.pending_answers?.bedrooms,
        project.pending_answers?.style,
      ];
    })
    .toEqual(["grenier chaleureux, bois et lin", 24, 1, "chalet"]);

  // Réouverture : une seule note, mêmes valeurs ; « Nouvelle variante » envoie à l'assistant.
  await page.reload();
  await expectSpace(page, "board");
  await expect(card).toHaveCount(1);
  await expect(card.getByLabel("Surface (m²)")).toHaveValue("24");
  await expect(card.getByLabel("Style")).toHaveValue("chalet");
  await expect(card.getByLabel("Vos envies et contraintes")).toHaveValue(
    "grenier chaleureux, bois et lin",
  );
  await card.getByRole("button", { name: "Nouvelle variante → assistant" }).click();
  await expect(page.getByLabel("Votre demande à l'assistant")).toBeVisible();
  await expect(page.getByRole("complementary", { name: "Assistant" })).toContainText(
    "grenier chaleureux, bois et lin",
  );
  expect(outside).toEqual([]);
});
