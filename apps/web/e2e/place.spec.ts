import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, showPanel } from "./helpers";
import { gotoApp } from "./app-ready";

/** E5c : escalier déplacé à la souris (accroché à un mur parallèle, règles en direct, refus
 *  expliqué, annulable) ; une porte s'ouvre vers la pièce quel que soit le sens de tracé du
 *  mur, et « intérieur » veut dire « vers la pièce ». */

const SHOTS = path.resolve("e2e/screenshots");

type Corner = readonly [number, number];
const CLOCKWISE: Corner[] = [
  [300, 650],
  [800, 650],
  [800, 350],
  [300, 350],
  [300, 650],
];

async function createRoom(
  page: Page,
  name: string,
  corners: Corner[] = CLOCKWISE,
): Promise<string> {
  await gotoApp(page, "/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  // T7d1 : le panneau (fermé par défaut) porte les cartes et la fiche.
  await showPanel(page);
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of corners) await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  return /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
}

type Json = Record<string, unknown>;
type P2 = { x: number; y: number };

async function levelOf(page: Page, id: string) {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<
      string,
      {
        elements: Record<string, Json>;
        vertices: Record<string, { position: P2 }>;
      }
    >;
  };
  return Object.values(project.levels)[0]!;
}

async function stairsOf(page: Page, id: string) {
  const level = await levelOf(page, id);
  return Object.values(level.elements).find((e) => e.type === "stairs") as {
    position: P2;
    yaw: number;
    width: number;
  };
}

async function center(locator: Locator): Promise<P2> {
  const b = (await locator.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

async function dragBy(page: Page, from: P2, dx: number, dy: number) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + dx, from.y + dy, { steps: 10 });
}

test("escalier : glissé à la souris, accroché au mur, refusé hors de la pièce, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E escalier déplacé");
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.locator('button.card[data-asset="stairs:straight"]').click();
  await page.mouse.click(box.x + 550, box.y + 620);
  await expect(page.locator("g.stairs")).toHaveCount(1);
  await page.keyboard.press("v");
  const outline = page.locator("g.stairs polygon.stairs-outline");
  const st0 = await stairsOf(page, id);

  // Vers le milieu de la pièce : il suit la souris, la montée garde son sens.
  await dragBy(page, await center(outline), 0, -120);
  await page.mouse.up();
  await expect
    .poll(async () => (await stairsOf(page, id)).position.y)
    .toBeGreaterThan(st0.position.y + 1.0);
  const st1 = await stairsOf(page, id);
  expect(st1.yaw).toBeCloseTo(st0.yaw, 5);

  // Près du mur du haut : son flanc s'y accroche (demi-largeur + 2 cm de la face).
  await dragBy(page, await center(outline), 0, -110);
  await page.screenshot({ path: path.join(SHOTS, "e5c_escalier_glisse.png") });
  await page.mouse.up();
  const level = await levelOf(page, id);
  const topY = Math.max(
    ...Object.values(level.vertices).map((v) => v.position.y),
  );
  const wall = Object.values(level.elements).find((e) => e.type === "wall") as {
    thickness: number;
  };
  await expect
    .poll(async () => (await stairsOf(page, id)).position.y)
    .toBeCloseTo(topY - wall.thickness / 2 - st0.width / 2 - 0.02, 1);

  // Hors de la pièce, au-delà du mur du bas : refusé, la raison est dite.
  const before = await stairsOf(page, id);
  await dragBy(page, await center(outline), 0, 420);
  await expect(page.locator("g.stairs.invalid")).toHaveCount(1);
  await page.mouse.up();
  await expect(page.getByText(/Non déplacé/)).toBeVisible();
  expect((await stairsOf(page, id)).position.y).toBeCloseTo(
    before.position.y,
    5,
  );

  // Annuler : il revient au milieu.
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await stairsOf(page, id)).position.y)
    .toBeCloseTo(st1.position.y, 5);
});

test("porte : s'ouvre vers la pièce même quand les murs sont tracés dans l'autre sens", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E porte vers la pièce", [
    [300, 650],
    [300, 350],
    [800, 350],
    [800, 650],
    [300, 650],
  ]);
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.keyboard.press("d");
  await page.mouse.click(box.x + 550, box.y + 650);
  await expect(page.locator("polygon.opening.door")).toHaveCount(1);
  const door = Object.values((await levelOf(page, id)).elements).find(
    (e) => e.type === "opening",
  ) as { swing: { direction: string } };
  // Le mur du bas va de droite à gauche : sa normale gauche pointe dehors.
  expect(door.swing.direction).toBe("outward");
  const arc = await center(page.locator("svg.plan path.swing"));
  expect(arc.y).toBeLessThan(box.y + 650);

  // La fiche dit « vers l'intérieur » : vers la pièce.
  await page.keyboard.press("v");
  await page.locator("polygon.opening.door").click();
  const select = page.getByLabel("Ouverture");
  await expect(select.locator("option:checked")).toHaveText("vers l'intérieur");
  await page.screenshot({ path: path.join(SHOTS, "e5c_porte_piece.png") });
});
