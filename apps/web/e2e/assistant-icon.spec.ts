import { expect, test } from "@playwright/test";
import path from "node:path";
import { reloadApp } from "./app-ready";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** Un projet qui arrive avec une consigne pose d'avance le volet de l'assistant, panneau fermé.
 *  Un seul clic sur l'icône Assistant ouvre le fil : sur l'espace de travail comme dans le plan
 *  (avant, ce premier clic refermait le volet posé d'avance et rien ne s'ouvrait). */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

test("projet arrivé avec une consigne : un seul clic sur l'icône Assistant ouvre le fil", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const id = await createEmptyProject(page, "Icône Assistant");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  const composer = page.getByRole("region", { name: "Compositeur", exact: true });
  const wish = composer.getByLabel("Vos envies et contraintes");
  await wish.fill("salon lumineux, chêne clair");
  await wish.blur();
  await expect
    .poll(async () => {
      const p = (await (await page.request.get(`/api/projects/${id}`)).json()) as {
        pending_instruction: string | null;
      };
      return p.pending_instruction ?? "";
    })
    .toContain("chêne clair");

  const icon = page.getByRole("button", { name: "Assistant", exact: true });
  const ask = page.getByLabel("Votre demande à l'assistant");

  // Espace de travail : la consigne arrive avec le projet, un clic ouvre le fil.
  await reloadApp(page);
  await expectSpace(page, "board");
  await expect(ask).toHaveCount(0);
  await icon.click();
  await expect(ask).toBeVisible();
  // Un second clic le referme : les zones du panneau (l'Assistant) reviennent.
  await icon.click();
  await expect(ask).toHaveCount(0);
  await expect(composer).toHaveCount(1);

  // Plan : même arrivée, puis le plan ; un clic ouvre le fil. Dans le plan, le panneau fermé
  // garde son volet dans la page, caché : on regarde ce qui se voit.
  await reloadApp(page);
  await expectSpace(page, "board");
  await goSpace(page, "plan");
  await expect(ask).toBeHidden();
  await icon.click();
  await expect(ask).toBeVisible();
  await icon.click();
  await expect(ask).toBeHidden();
});
