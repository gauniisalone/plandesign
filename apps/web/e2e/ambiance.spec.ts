import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";

/** V1 (note 10, point 1) : fiche « Ambiance » de la pièce, lisible ; ce que l'utilisateur
 *  choisit est marqué « choisi » (prioritaire sur l'automatique) ; annulable. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<string> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 650],
    [800, 650],
    [800, 350],
    [300, 350],
    [300, 650],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  return /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
}

type Intent = {
  style: string;
  mood: string[];
  palette?: string[];
  instruction?: string | null;
  translated_by?: string | null;
  sources: Record<string, { source: string }>;
} | null;

async function intentOf(page: Page, id: string): Promise<Intent> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<
      string,
      { elements: Record<string, { type: string; intent?: Intent }> }
    >;
  };
  const room = Object.values(Object.values(project.levels)[0]!.elements).find(
    (e) => e.type === "room",
  );
  return room?.intent ?? null;
}

test("fiche Ambiance : lisible, choix marqués « choisi », deux humeurs au plus, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E fiche ambiance");
  await page.keyboard.press("v");
  await page.locator("svg.plan").click({ position: { x: 420, y: 450 } });
  const card = page.getByLabel("Fiche Ambiance");
  await expect(card).toBeVisible();
  await expect(card.locator(".intent-summary")).toContainText("Naturel");
  await expect(card.locator(".intent-tag").first()).toHaveText("déduit");
  expect(await intentOf(page, id)).toBeNull();

  // Style choisi : marqué « choisi », enregistré user_defined.
  await card.getByLabel(/^Ambiance : /).selectOption("chalet");
  await expect
    .poll(async () => (await intentOf(page, id))?.style)
    .toBe("chalet");
  expect((await intentOf(page, id))!.sources.style!.source).toBe(
    "user_defined",
  );
  await expect(card.locator(".intent-tag").first()).toHaveText("choisi");

  // Humeurs : deux au plus, la plus ancienne cède.
  const moods = card.getByRole("group", { name: "Humeur (deux au plus)" });
  for (const m of ["chaleureux", "lumineux", "cosy"]) {
    await moods.getByRole("button", { name: m, exact: true }).click();
    await expect(
      moods.getByRole("button", { name: m, exact: true }),
    ).toHaveAttribute("aria-pressed", "true");
  }
  await expect
    .poll(async () => (await intentOf(page, id))?.mood)
    .toEqual(["lumineux", "cosy"]);
  await expect(card.locator(".intent-summary")).toContainText(
    "Chalet · lumineux et cosy",
  );
  await page.screenshot({ path: path.join(SHOTS, "v1_fiche_ambiance.png") });

  // Annuler : l'humeur d'avant revient.
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await intentOf(page, id))?.mood)
    .toEqual(["chaleureux", "lumineux"]);
});

test("consigne libre : écrite à l'assistant depuis la fiche de la pièce, comprise, annulée", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E consigne");
  await page.keyboard.press("v");
  await page.locator("svg.plan").click({ position: { x: 420, y: 450 } });
  const card = page.getByLabel("Fiche Ambiance");
  // P5 : la fiche n'a plus de champ « Consigne » ; elle ouvre l'assistant, la pièce en
  // contexte.
  await expect(card.getByLabel("Consigne d'ambiance")).toHaveCount(0);
  await card
    .getByRole("button", {
      name: "Écrire à l'assistant à propos de cette pièce",
    })
    .click();
  const panel = page.locator(".assistant");
  await expect(panel.locator(".assistant-context")).toContainText("Pièce 1");
  const input = page.getByLabel("Votre demande à l'assistant");
  await input.fill(
    "chambre scandinave cosy, tons vert sauge et lin, sans tapis",
  );
  await input.press("Enter");
  await expect
    .poll(async () => (await intentOf(page, id))?.style)
    .toBe("scandinave");
  const intent = (await intentOf(page, id))!;
  expect(intent.mood).toEqual(["cosy"]);
  expect(intent.palette).toEqual(["vert sauge", "lin", "blanc casse"]);
  expect(intent.instruction).toContain("chambre scandinave");
  expect(intent.sources.style!.source).toBe("user_defined");
  const turn = panel.locator(".bubble.assistant").last();
  await expect(turn.locator(".status-done")).toContainText(
    /Ambiance de Pièce 1/,
  );
  await expect(turn).toContainText(/scandinave/i);
  await expect(turn).toContainText("lu par phrases simples");
  await page.screenshot({ path: path.join(SHOTS, "v1_consigne.png") });
  // « Annuler » défait tout le tour.
  await turn.getByRole("button", { name: "Annuler" }).click();
  await expect.poll(async () => await intentOf(page, id)).toBeNull();
});
