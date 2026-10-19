import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";
import { gotoApp } from "./app-ready";

/** E1 : tracé « cranté » (angles par crans, accroches intelligentes, Maj libre), joué dans un
 *  vrai navigateur ; les murs enregistrés sont relus par l'API. */

const SHOTS = path.resolve("e2e/screenshots");

async function createProject(page: Page, name: string): Promise<string> {
  await gotoApp(page, "/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  await expect(page.locator("svg.plan")).toBeVisible();
  return /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;
}

interface Pt {
  x: number;
  y: number;
}

/** Murs enregistrés : extrémités en mètres, dans l'ordre de création. */
async function savedWalls(page: Page, id: string): Promise<[Pt, Pt][]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    levels: Record<
      string,
      {
        vertices: Record<string, { position: Pt }>;
        elements: Record<
          string,
          { type: string; start_vertex?: string; end_vertex?: string }
        >;
      }
    >;
  };
  const level = Object.values(project.levels)[0]!;
  return Object.values(level.elements)
    .filter((e) => e.type === "wall")
    .map((w) => [
      level.vertices[w.start_vertex!]!.position,
      level.vertices[w.end_vertex!]!.position,
    ]);
}

const label = (page: Page) => page.locator("g.snap text.snap-label");
/** Le mur posé revient du serveur avant qu'on bouge : ses accroches en dépendent. */
const walls = (page: Page, n: number) =>
  expect(page.locator("polygon.wall")).toHaveCount(n);

test("tracé cranté : angles, milieu, perpendiculaire, Maj libre, sommet déplacé", async ({
  page,
}) => {
  const id = await createProject(page, "E2E accroches");
  const svg = page.locator("svg.plan");
  const box = (await svg.boundingBox())!;
  const at = (x: number, y: number) => page.mouse.move(box.x + x, box.y + y);
  const click = (x: number, y: number) =>
    page.mouse.click(box.x + x, box.y + y);

  await page.keyboard.press("w");
  // 1. Un mur presque horizontal (6 px d'écart) s'aimante à 0° et le dit.
  await click(200, 500);
  await at(600, 506);
  await expect(label(page)).toHaveText("0°");
  await expect(page.locator("g.snap line.snap-guide")).not.toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "e1_angle.png") });
  await click(600, 506);
  await walls(page, 1);
  // 2. Retour presque vertical : 90°.
  await at(604, 300);
  await expect(label(page)).toHaveText("90°");
  await click(604, 300);
  await walls(page, 2);
  await page.keyboard.press("Escape");
  let saved = await savedWalls(page, id);
  expect(saved).toHaveLength(2);
  expect(Math.abs(saved[0]![0].y - saved[0]![1].y)).toBeLessThan(1e-6);
  expect(Math.abs(saved[1]![0].x - saved[1]![1].x)).toBeLessThan(1e-6);

  // 3. Le milieu du premier mur.
  await click(380, 250);
  await at(402, 502);
  await expect(label(page)).toHaveText("Milieu");
  await page.screenshot({ path: path.join(SHOTS, "e1_milieu.png") });
  await page.keyboard.press("Escape");

  // 4. Mur oblique, puis perpendiculaire à lui depuis son extrémité.
  // (sous la barre d'outils flottante : on reste dans la partie libre du plan)
  await click(700, 760);
  await click(900, 660);
  await walls(page, 3);
  await at(900 - 40, 660 - 80);
  await expect(label(page)).toHaveText("Perpendiculaire");
  await page.screenshot({ path: path.join(SHOTS, "e1_perpendiculaire.png") });
  await click(900 - 40, 660 - 80);
  await walls(page, 4);
  await page.keyboard.press("Escape");
  saved = await savedWalls(page, id);
  const [a, b] = saved[2]!;
  const [c, d] = saved[3]!;
  const dot = (b.x - a.x) * (d.x - c.x) + (b.y - a.y) * (d.y - c.y);
  expect(Math.abs(dot)).toBeLessThan(1e-6);

  // 5. Maj : tracé libre, rien n'accroche (le mur garde son petit angle).
  await click(200, 650);
  await page.keyboard.down("Shift");
  await at(600, 656);
  await expect(label(page)).toHaveCount(0);
  await click(600, 656);
  await walls(page, 5);
  await page.keyboard.up("Shift");
  await page.keyboard.press("Escape");
  saved = await savedWalls(page, id);
  const free = saved[saved.length - 1]!;
  expect(Math.abs(free[0].y - free[1].y)).toBeGreaterThan(0.01);

  // 6. Déplacer un sommet : mêmes accroches, depuis son voisin.
  await page.keyboard.press("v");
  await page.mouse.move(box.x + 600, box.y + 300);
  await page.mouse.down();
  const before = saved[1]!;
  await page.mouse.move(box.x + 606, box.y + 260, { steps: 4 });
  await expect(label(page)).toHaveText("90°");
  await page.screenshot({ path: path.join(SHOTS, "e1_sommet.png") });
  await page.mouse.up();
  saved = await savedWalls(page, id);
  const after = saved[1]!;
  expect(Math.abs(after[0].x - after[1].x)).toBeLessThan(1e-6);
  const length = (w: [Pt, Pt]) => Math.hypot(w[1].x - w[0].x, w[1].y - w[0].y);
  expect(length(after)).toBeGreaterThan(length(before) + 0.2);
});
