import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { showPanel } from "../e2e/helpers";

/** KAN-124 (phase 2) : le panneau « Assistant » avec le faux modèle du serveur
 *  (OPENPLAN_ASSISTANT_URL=fake) : une hauteur acceptée, une correction, un repli sur le
 *  formulaire (modèle trop lent). La hauteur est relue dans le projet (route du registre). */

async function roomProject(page: Page, name: string): Promise<{ pid: string; lid: string }> {
  const made = await page.request.post("/api/projects", { data: { name } });
  const project = (await made.json()) as { id: string; levels: Record<string, unknown> };
  const pid = project.id;
  const lid = Object.keys(project.levels)[0]!;
  const ops = `/api/projects/${pid}/levels/${lid}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
  for (let i = 0; i < 4; i++) {
    const [a, b] = [corners[i]!, corners[(i + 1) % 4]!];
    await page.request.post(ops, {
      data: { op: "add_wall", start: { x: a[0], y: a[1] }, end: { x: b[0], y: b[1] } },
    });
  }
  await page.request.post(ops, { data: { op: "detect_rooms" } });
  return { pid, lid };
}

async function height(page: Page, pid: string, lid: string): Promise<number> {
  const p = (await (await page.request.get(`/api/projects/${pid}`)).json()) as {
    levels: Record<string, { height: number }>;
  };
  return p.levels[lid]!.height;
}

async function openAssistant(page: Page, pid: string) {
  await gotoApp(page, `/#/projects/${pid}`);
  await showPanel(page);
  const panel = page.getByRole("complementary", { name: "Assistant" });
  await expect(panel).toBeVisible();
  const question = panel.getByRole("region", { name: "Quelle est la hauteur sous plafond ?" });
  await expect(question).toBeVisible();
  return { panel, question };
}

test("KAN-124 : une hauteur acceptée passe par le recalage du registre", async ({ page }) => {
  const { pid, lid } = await roomProject(page, "Calage accepté");
  const { panel, question } = await openAssistant(page, pid);
  await question.getByLabel(/^Votre réponse/).fill("2m45");
  await question.getByRole("button", { name: "Envoyer" }).click();
  await expect(question.getByRole("status")).toHaveText(
    "Régler la hauteur sous plafond à 2,45 m ?",
  );
  // Rien n'est modifié avant le clic.
  expect(await height(page, pid, lid)).toBeCloseTo(2.5);
  await question.getByRole("button", { name: "Oui" }).click();
  await expect.poll(() => height(page, pid, lid)).toBeCloseTo(2.45);
  await expect(
    panel.getByRole("region", { name: "Quelle est la hauteur sous plafond ?" }),
  ).toHaveCount(0);
});

test("KAN-124 : corriger une réponse avant de la valider", async ({ page }) => {
  const { pid, lid } = await roomProject(page, "Calage corrigé");
  const { question } = await openAssistant(page, pid);
  await question.getByLabel(/^Votre réponse/).fill("2m45");
  await question.getByRole("button", { name: "Envoyer" }).click();
  await question.getByRole("button", { name: "Corriger" }).click();
  await question.getByLabel(/^Votre réponse/).fill("plutôt 2,60");
  await question.getByRole("button", { name: "Envoyer" }).click();
  await expect(question.getByRole("status")).toHaveText(
    "Régler la hauteur sous plafond à 2,60 m ?",
  );
  await question.getByRole("button", { name: "Oui" }).click();
  await expect.poll(() => height(page, pid, lid)).toBeCloseTo(2.6);
});

test("KAN-124 : modèle trop lent, le formulaire classique prend le relais", async ({
  page,
}) => {
  const { pid, lid } = await roomProject(page, "Calage en repli");
  const { question } = await openAssistant(page, pid);
  await question.getByLabel(/^Votre réponse/).fill("trop lent");
  await question.getByRole("button", { name: "Envoyer" }).click();
  await expect(
    question.getByText("L'assistant met trop de temps : répondez avec le formulaire."),
  ).toBeVisible();
  await question.getByLabel("Hauteur sous plafond (m)").fill("2,55");
  await question.getByRole("button", { name: "Valider" }).click();
  await expect.poll(() => height(page, pid, lid)).toBeCloseTo(2.55);
});
