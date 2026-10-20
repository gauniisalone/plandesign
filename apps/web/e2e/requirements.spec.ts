import { expect, test } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** T1, T7b : les exigences dans le compositeur flottant. Deux onglets (prompt libre, questions
 *  guidées), la ligne « compris » ; le contenu est gardé dans le projet (réponses et
 *  consigne), relu à la réouverture ; rien sur le tableau, et rien de visible quand le
 *  compositeur est fermé (une pastille « Générer » le rouvre). Aucune requête externe. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

test("compositeur : onglets, champs guidés, texte, compris, gardés dans le projet", async ({
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
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(board.locator(".board-item.kind-requirements")).toHaveCount(0);
  const composer = page.getByRole("region", {
    name: "Compositeur",
    exact: true,
  });
  await expect(composer).toHaveCount(1);
  // T7d1 : l'aide est en infobulle (moins de texte).
  await expect(composer).toHaveAttribute("title", /Rien n'est obligatoire/);
  await expect(
    composer.getByRole("tab", { name: "Prompt libre" }),
  ).toHaveAttribute("aria-selected", "true");

  // Les champs guidés puis le texte : « compris » suit, le projet garde tout.
  await composer.getByRole("tab", { name: "Questions guidées" }).click();
  await composer.getByLabel("Surface (m²)").fill("24");
  await composer.getByLabel("Chambres").fill("1");
  await composer.getByLabel("Style").selectOption("chalet");
  await composer.getByRole("tab", { name: "Prompt libre" }).click();
  await composer
    .getByLabel("Vos envies et contraintes")
    .fill("grenier chaleureux, bois et lin");
  await composer.getByLabel("Vos envies et contraintes").blur();
  await expect(composer).toContainText(/compris : .*24 m².*1 chambre.*chalet/);
  await expect(composer).toContainText("chaleureux");
  await expect
    .poll(async () => {
      const project = (await (
        await page.request.get(`/api/projects/${id}`)
      ).json()) as {
        pending_instruction: string | null;
        pending_answers: {
          area_m2: number;
          bedrooms: number;
          style: string;
        } | null;
      };
      return [
        project.pending_instruction,
        project.pending_answers?.area_m2,
        project.pending_answers?.bedrooms,
        project.pending_answers?.style,
      ];
    })
    .toEqual(["grenier chaleureux, bois et lin", 24, 1, "chalet"]);

  // KAN-64 : la zone « Assistant » du panneau se replie ; rouverte, tout est là.
  const fold = page.locator(".side .board-zone-toggle", {
    hasText: "Assistant",
  });
  await fold.click();
  await expect(composer).toHaveCount(0);
  await expect(board).not.toContainText("grenier chaleureux");
  await fold.click();
  await expect(composer.getByLabel("Vos envies et contraintes")).toHaveValue(
    "grenier chaleureux, bois et lin",
  );

  // Réouverture de la page : mêmes valeurs ; « Nouvelle variante » envoie à l'assistant.
  await reloadApp(page);
  await expectSpace(page, "board");
  await expect(composer).toHaveCount(1);
  await expect(composer.getByLabel("Vos envies et contraintes")).toHaveValue(
    "grenier chaleureux, bois et lin",
  );
  await composer.getByRole("tab", { name: "Questions guidées" }).click();
  await expect(composer.getByLabel("Surface (m²)")).toHaveValue("24");
  await expect(composer.getByLabel("Style")).toHaveValue("chalet");
  await composer
    .getByRole("button", { name: "Nouvelle variante → assistant" })
    .click();
  await expect(page.getByLabel("Votre demande à l'assistant")).toBeVisible();
  await expect(
    page.getByRole("complementary", { name: "Assistant" }),
  ).toContainText("grenier chaleureux, bois et lin");
  expect(outside).toEqual([]);
});
