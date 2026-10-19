import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";
import { gotoApp } from "./app-ready";

/** E2 : redimensionner sur le plan (meuble, ouverture, escalier) par des poignées ; cote et
 *  règles en direct, bornes du catalogue, Alt depuis le centre, annulable. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<string> {
  await gotoApp(page, "/");
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

async function elementsOf(
  page: Page,
  id: string,
  type: string,
): Promise<Json[]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<string, { elements: Record<string, Json> }>;
  };
  return Object.values(Object.values(project.levels)[0]!.elements).filter(
    (e) => e.type === type,
  );
}

async function center(locator: Locator): Promise<{ x: number; y: number }> {
  const b = (await locator.boundingBox())!;
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

/** Tire une poignée de `px` pixels dans la direction centre → poignée. */
async function pull(
  page: Page,
  handle: Locator,
  from: { x: number; y: number },
  px: number,
) {
  const h = await center(handle);
  const dx = h.x - from.x;
  const dy = h.y - from.y;
  const n = Math.hypot(dx, dy) || 1;
  await page.mouse.move(h.x, h.y);
  await page.mouse.down();
  await page.mouse.move(h.x + (dx / n) * px, h.y + (dy / n) * px, { steps: 5 });
}

test("meuble : poignées, cote en direct, bornes du catalogue, Alt depuis le centre, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E poignées meuble");
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.getByLabel("Rechercher un meuble").fill("bureau 140");
  await page.locator('button.card[data-asset="desk_140"]').click();
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.mouse.click(box.x + 550, box.y + 500);
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("polygon.furniture").click();
  const handles = page.locator('rect.resize-handle[data-handle^="f|"]');
  await expect(handles).toHaveCount(8);
  const catalog = (await (await page.request.get("/api/catalog")).json()) as {
    assets: { id: string; width: { max: number } }[];
  };
  const maxWidth = catalog.assets.find((a) => a.id === "desk_140")!.width.max;
  const [desk0] = await elementsOf(page, id, "furniture");
  const bbox0 = desk0!.bbox as { width: number; depth: number };
  const mid = await center(page.locator("polygon.furniture"));

  // Côté droit (hu = 1, hv = 0) : plus large, cote affichée pendant le glisser.
  await pull(
    page,
    page.locator('rect.resize-handle[data-handle$="|1|0"]'),
    mid,
    25,
  );
  await expect(page.locator("text.resize-label")).toContainText("×");
  await page.screenshot({ path: path.join(SHOTS, "e2_meuble.png") });
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (
          (await elementsOf(page, id, "furniture"))[0]!.bbox as {
            width: number;
          }
        ).width,
    )
    .toBeGreaterThan(bbox0.width + 0.05);

  // Très loin : bloqué au maximum du catalogue.
  await pull(
    page,
    page.locator('rect.resize-handle[data-handle$="|1|0"]'),
    mid,
    600,
  );
  await page.mouse.up();
  await expect
    .poll(
      async () =>
        (
          (await elementsOf(page, id, "furniture"))[0]!.bbox as {
            width: number;
          }
        ).width,
    )
    .toBeCloseTo(maxWidth, 5);

  // Alt sur la poignée avant : depuis le centre, le meuble ne bouge pas.
  const before = (await elementsOf(page, id, "furniture"))[0]!;
  const pos0 = (before.transform as { position: { x: number; y: number } })
    .position;
  const mid2 = await center(page.locator("polygon.furniture"));
  await page.keyboard.down("Alt");
  await pull(
    page,
    page.locator('rect.resize-handle[data-handle$="|0|1"]'),
    mid2,
    10,
  );
  await page.mouse.up();
  await page.keyboard.up("Alt");
  await expect
    .poll(
      async () =>
        (
          (await elementsOf(page, id, "furniture"))[0]!.bbox as {
            depth: number;
          }
        ).depth,
    )
    .not.toBeCloseTo(bbox0.depth, 3);
  const after = (await elementsOf(page, id, "furniture"))[0]!;
  const pos1 = (after.transform as { position: { x: number; y: number } })
    .position;
  expect(Math.hypot(pos1.x - pos0.x, pos1.y - pos0.y)).toBeLessThan(0.011);

  // Annuler : la profondeur revient.
  await page.keyboard.press("Control+z");
  await expect
    .poll(
      async () =>
        (
          (await elementsOf(page, id, "furniture"))[0]!.bbox as {
            depth: number;
          }
        ).depth,
    )
    .toBeCloseTo((before.bbox as { depth: number }).depth, 5);
});

test("ouverture et escalier : largeur le long du mur, largeur d'emmarchement bornée", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E poignées ouverture");
  const box = (await page.locator("svg.plan").boundingBox())!;
  // Fenêtre sur le mur du haut.
  await page.keyboard.press("f");
  await page.mouse.click(box.x + 550, box.y + 350);
  await expect(page.locator("polygon.opening")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("polygon.opening").click();
  const ends = page.locator('rect.resize-handle[data-handle^="o|"]');
  await expect(ends).toHaveCount(2);
  const [win0] = await elementsOf(page, id, "opening");
  const mid = await center(page.locator("polygon.opening"));
  await pull(
    page,
    page.locator('rect.resize-handle[data-handle$="|1"]'),
    mid,
    30,
  );
  await expect(page.locator("text.resize-label")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "e2_ouverture.png") });
  await page.mouse.up();
  await expect
    .poll(
      async () => (await elementsOf(page, id, "opening"))[0]!.width as number,
    )
    .toBeGreaterThan((win0!.width as number) + 0.1);

  // Escalier droit posé le long du mur du bas, puis élargi : bloqué à 1,20 m.
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.locator('button.card[data-asset="stairs:straight"]').click();
  await page.mouse.click(box.x + 550, box.y + 620);
  await expect(page.locator("g.stairs")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("g.stairs polygon.stairs-outline").click({ force: true });
  const sides = page.locator('rect.resize-handle[data-handle^="s|"]');
  await expect(sides).toHaveCount(2);
  const smid = await center(page.locator("g.stairs polygon.stairs-outline"));
  // Côté pièce (le plus haut à l'écran) : la rive du mur reste en place.
  const [a, b] = [await center(sides.nth(0)), await center(sides.nth(1))];
  const roomSide = a.y < b.y ? sides.nth(0) : sides.nth(1);
  const wallSide = a.y < b.y ? sides.nth(1) : sides.nth(0);
  await pull(page, roomSide, smid, 200);
  await expect(page.locator("text.resize-label")).toContainText("Largeur");
  await page.screenshot({ path: path.join(SHOTS, "e2_escalier.png") });
  await page.mouse.up();
  await expect
    .poll(
      async () => (await elementsOf(page, id, "stairs"))[0]!.width as number,
    )
    .toBeCloseTo(1.2, 5);
  // Côté mur : la trémie entrerait dans le mur, refusé avec la raison (E4).
  await page.keyboard.press("Control+z");
  await expect
    .poll(
      async () => (await elementsOf(page, id, "stairs"))[0]!.width as number,
    )
    .toBeCloseTo(0.9, 5);
  await page.locator("g.stairs polygon.stairs-outline").click({ force: true });
  await pull(page, wallSide, smid, 200);
  await page.mouse.up();
  await expect(page.getByText(/Non élargi/)).toBeVisible();
  expect((await elementsOf(page, id, "stairs"))[0]!.width).toBeCloseTo(0.9, 5);
});
