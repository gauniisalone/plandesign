import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-92, lot L2 : la zone de sol dans le plan 2D. Hachurée, sa hauteur écrite (« +20 cm ») ;
 *  choisie, ses 4 coins se déplacent (un pas d'historique) ; Suppr la retire, Ctrl+Z la rend.
 *  La zone est posée par l'API (le dessin en 3D est le lot L3). */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

type Pt = { x: number; y: number };
type ProjectJson = {
  levels: Record<
    string,
    { elements: Record<string, { type: string; polygon?: Pt[] }> }
  >;
};

async function planWithRoom(
  page: Page,
): Promise<{ id: string; level: string }> {
  const id = await createEmptyProject(page, "Estrade");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  return { id, level: Object.keys(project.levels)[0]! };
}

async function zones(page: Page, id: string, level: string) {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  return Object.entries(project.levels[level]!.elements).filter(
    ([, e]) => e.type === "floor_zone",
  );
}

test("zone de sol en 2D : hachure, hauteur, coin déplacé, Suppr, Ctrl+Z", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { id, level } = await planWithRoom(page);
  const origin = { Origin: new URL(page.url()).origin };
  // Un carré au milieu de la pièce (son intérieur, dérivé par le serveur).
  const derived = (await (
    await page.request.get(`/api/projects/${id}/levels/${level}/derived`)
  ).json()) as { rooms: Record<string, { polygon: Pt[] }> };
  const inner = Object.values(derived.rooms)[0]!.polygon;
  const xs = inner.map((p) => p.x);
  const ys = inner.map((p) => p.y);
  const [x0, x1, y0, y1] = [
    Math.min(...xs),
    Math.max(...xs),
    Math.min(...ys),
    Math.max(...ys),
  ];
  const at = (u: number, v: number) => ({
    x: x0 + (x1 - x0) * u,
    y: y0 + (y1 - y0) * v,
  });
  const ops = `/api/projects/${id}/levels/${level}/ops`;
  const made = await page.request.post(ops, {
    headers: origin,
    data: {
      op: "add_floor_zone",
      polygon: [at(0.3, 0.3), at(0.6, 0.3), at(0.6, 0.6), at(0.3, 0.6)],
    },
  });
  expect(made.status(), await made.text()).toBe(200);
  const [[zoneId]] = await zones(page, id, level);
  const raised = await page.request.post(ops, {
    headers: origin,
    data: {
      op: "update_element",
      element_id: zoneId,
      fields: { elevation: 0.2 },
    },
  });
  expect(raised.status()).toBe(200);
  await page.reload();
  await goSpace(page, "plan");

  const area = page.locator(".plan-zone-area");
  await expect(area).toHaveCount(1, { timeout: 30_000 });
  await expect(page.locator(".plan-zone-label")).toHaveText("+20 cm");

  // Choisie : 4 coins ; glisser un coin le déplace, en un seul geste.
  await area.click();
  const corners = page.locator(".plan-zone-corner");
  await expect(corners).toHaveCount(4);
  const before = (await zones(page, id, level))[0]![1].polygon!;
  const box = (await corners.nth(2).boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + 40, box.y + 30, { steps: 8 });
  await page.mouse.up();
  await expect
    .poll(async () =>
      JSON.stringify((await zones(page, id, level))[0]![1].polygon),
    )
    .not.toBe(JSON.stringify(before));
  const after = (await zones(page, id, level))[0]![1].polygon!;
  expect(after[0]).toEqual(before[0]);
  expect(after[1]).toEqual(before[1]);
  expect(after[3]).toEqual(before[3]);
  if (process.env.E2E_CAPTURE_DIR)
    await page.screenshot({
      path: `${process.env.E2E_CAPTURE_DIR}/kan92-1-zone-2d.png`,
    });

  // Suppr la retire ; Ctrl+Z la rend.
  await area.click();
  await page.keyboard.press("Delete");
  await expect.poll(async () => (await zones(page, id, level)).length).toBe(0);
  await expect(area).toHaveCount(0);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await zones(page, id, level)).length).toBe(1);
  await expect(area).toHaveCount(1);
});
