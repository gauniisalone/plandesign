import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";

/** E5 : bibliothèque d'ouvertures. Section « Portes et fenêtres » en cartes avec vignettes,
 *  pose d'un clic sur un mur (fantôme qui suit le mur), symboles normalisés, poignées E2 et
 *  retournement E4, annulable, export SVG. */

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

type Json = Record<string, unknown>;

async function openingsOf(page: Page, id: string): Promise<Json[]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, Json> }>;
  };
  return Object.values(Object.values(project.levels)[0]!.elements).filter(
    (e) => e.type === "opening",
  );
}

async function pick(page: Page, card: string) {
  if (!(await page.locator("section.library").isVisible()))
    await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.locator(`button.card[data-asset="opening:${card}"]`).click();
}

test("portes et fenêtres : cartes, pose sur un mur, symboles, poignées, retournement, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E bibliothèque d'ouvertures");
  const box = (await page.locator("svg.plan").boundingBox())!;

  // La section en cartes, chacune avec sa vignette dessinée.
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  const chip = page.getByRole("button", {
    name: "Portes et fenêtres",
    exact: true,
  });
  await chip.click();
  await expect(chip).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".stairs-cards")).toHaveCount(0);
  const cards = page.locator(".opening-cards button.card");
  await expect(cards).toHaveCount(10);
  await expect(page.locator(".opening-cards svg.opening-thumb")).toHaveCount(
    10,
  );
  await page.screenshot({ path: path.join(SHOTS, "e5_cartes.png") });

  // Double porte : le fantôme suit le mur du bas, un clic la pose (1,40 m, deux vantaux).
  await page.locator('button.card[data-asset="opening:double_door"]').click();
  await page.mouse.move(box.x + 520, box.y + 652);
  await expect(page.getByTestId("opening-ghost")).toBeVisible();
  await page.mouse.click(box.x + 520, box.y + 650);
  await expect(page.locator("polygon.opening.double_door")).toHaveCount(1);
  const [door] = await openingsOf(page, id);
  expect(door).toMatchObject({ kind: "double_door", width: 1.4 });
  expect(door!.swing).toBeTruthy();
  await expect(page.locator("svg.plan path.swing")).toHaveCount(2);

  // Poignées de largeur (E2) et retournement (E4) comme une porte simple.
  await page.keyboard.press("v");
  await page.locator("polygon.opening.double_door").click();
  await expect(
    page.locator('rect.resize-handle[data-handle^="o|"]'),
  ).toHaveCount(2);
  await expect(page.locator("circle.flip-handle")).toBeVisible();
  await page.keyboard.press("g");
  await expect
    .poll(
      async () =>
        ((await openingsOf(page, id))[0]!.swing as { hinge: string }).hinge,
    )
    .toBe("right");

  // Coulissante en applique sur le mur de gauche : rail visible, pas de débattement.
  await pick(page, "sliding_door");
  await page.mouse.click(box.x + 300, box.y + 480);
  await expect(page.locator("polygon.opening.sliding_door")).toHaveCount(1);
  await expect(page.locator("polyline.opening-symbol.rail")).toHaveCount(1);

  // Baie vitrée coulissante sur le mur du haut : deux vantaux vitrés.
  await pick(page, "sliding_window");
  await page.mouse.click(box.x + 560, box.y + 350);
  await expect(page.locator("polygon.opening.sliding_window")).toHaveCount(1);
  await expect(page.locator("polyline.opening-symbol.glass")).toHaveCount(2);
  await page.screenshot({ path: path.join(SHOTS, "e5_plan.png") });

  // Export SVG : les symboles y sont, par sorte.
  const level = Object.keys(
    (
      (await (await page.request.get(`/api/projects/${id}`)).json()) as {
        levels: Record<string, unknown>;
      }
    ).levels,
  )[0]!;
  const svg = await (
    await page.request.get(`/api/projects/${id}/exports/svg?level_id=${level}`)
  ).text();
  expect(svg).toContain("opening double_door");
  expect(svg).toContain("opening-symbol rail");

  // Annuler : la baie part.
  await page.keyboard.press("Control+z");
  await expect(page.locator("polygon.opening.sliding_window")).toHaveCount(0);
  expect(await openingsOf(page, id)).toHaveLength(2);
});
