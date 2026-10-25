import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

/** KAN-92 (validation du ticket) : en 3D, dessiner une zone de 4 segments au sol (Z), l'élever
 *  de 20 cm au clavier (P, clic sur son dessus, « 20 », Entrée), tirer un mur (épaisseur),
 *  puis Ctrl+Z annule ce dernier geste, un pas à la fois. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const SHOTS = process.env.E2E_CAPTURE_DIR;

type Pt = { x: number; y: number };
type Element = {
  type: string;
  thickness?: number;
  elevation?: number;
  polygon?: Pt[];
};
type ProjectJson = {
  levels: Record<string, { elements: Record<string, Element> }>;
};

async function elements(page: Page, id: string, level: string) {
  const r = await page.request.get(`/api/projects/${id}`);
  return Object.entries(
    ((await r.json()) as ProjectJson).levels[level]!.elements,
  );
}

/** Le point du plan (x, y, z en mètres) à l'écran, via la sonde du viewer (e2e seulement). */
async function screen(
  page: Page,
  x: number,
  y: number,
  z: number,
): Promise<Pt> {
  return page.evaluate(
    ([a, b, c]) =>
      (
        window as unknown as {
          __planDesign3d: { toScreen: (x: number, y: number, z: number) => Pt };
        }
      ).__planDesign3d.toScreen(a!, b!, c!),
    [x, y, z],
  );
}

/** Attendre le GLB demandé après un geste (la scène se recharge). */
async function sceneReloaded(page: Page, act: () => Promise<void>) {
  const glb = page.waitForResponse((r) => r.url().includes("/exports/glb"), {
    timeout: 60_000,
  });
  await act();
  await glb;
  await page.waitForTimeout(800); // chargement et premier rendu
}

test("Pousser/Tirer en 3D : zone de 4 segments, +20 cm au clavier, mur tiré, Ctrl+Z", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await page.addInitScript(() => {
    (
      window as unknown as { __planDesign3dProbe: boolean }
    ).__planDesign3dProbe = true;
  });
  const id = await createEmptyProject(page, "Estrade 3D");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ProjectJson;
  const level = Object.keys(project.levels)[0]!;
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

  // La 3D seule, vue de dessus : on clique le sol.
  await sceneReloaded(page, async () => {
    await goSpace(page, "3d");
  });
  const viewer = page.locator(".viewer3d");
  await page.keyboard.press("1");
  await page.waitForTimeout(500);

  // Z : 4 clics au sol, le 4ᵉ ferme la zone (un seul geste).
  await page.keyboard.press("z");
  await expect(viewer).toHaveAttribute("data-tool", "floorzone");
  // Les deux outils sont dans la colonne d'outils, visible en 3D seule ; l'outil choisi y est
  // mis en avant.
  const tools = page.getByRole("toolbar", { name: "Outils" });
  await expect(
    tools.getByRole("button", { name: "Pousser/Tirer", exact: true }),
  ).toBeEnabled();
  await expect(
    tools.getByRole("button", { name: "Zone de sol", exact: true }),
  ).toHaveClass(/active/);
  const corners = [at(0.3, 0.3), at(0.65, 0.3), at(0.65, 0.6), at(0.3, 0.6)];
  await sceneReloaded(page, async () => {
    for (const [i, c] of corners.entries()) {
      const p = await screen(page, c.x, c.y, 0);
      await page.mouse.move(p.x, p.y, { steps: 3 });
      if (i === 3)
        await expect(page.locator(".viewer3d-cote")).toContainText(
          "ce clic ferme",
        );
      await page.mouse.click(p.x, p.y);
    }
  });
  const zones = async () =>
    (await elements(page, id, level)).filter(
      ([, e]) => e.type === "floor_zone",
    );
  await expect.poll(async () => (await zones()).length).toBe(1);
  const [[zoneId, zone]] = await zones();
  expect(zone!.elevation).toBe(0);

  // P : clic sur le dessus de la zone, « 20 », Entrée → +20 cm.
  await page.keyboard.press("p");
  await expect(viewer).toHaveAttribute("data-tool", "pushpull");
  const mid = at(0.475, 0.45);
  const top = await screen(page, mid.x, mid.y, 0.005);
  await page.mouse.move(top.x, top.y, { steps: 3 });
  await expect(page.locator(".viewer3d-cote")).toContainText(
    "hauteur de la zone",
  );
  await page.mouse.click(top.x, top.y);
  await page.keyboard.type("20");
  await expect(page.locator(".viewer3d-cote")).toContainText("+20 cm");
  if (SHOTS)
    await page.screenshot({ path: `${SHOTS}/kan92-2-zone-3d-cote.png` });
  await sceneReloaded(page, async () => {
    await page.keyboard.press("Enter");
  });
  await expect
    .poll(async () => (await zones())[0]![1].elevation)
    .toBeCloseTo(0.2, 3);

  // Un mur tiré, vu de face : la face extérieure du mur proche, « 30 » → 30 cm d'épaisseur.
  await page.keyboard.press("2");
  await page.waitForTimeout(500);
  const walls = (await elements(page, id, level)).filter(
    ([, e]) => e.type === "wall",
  );
  let pulled: string | null = null;
  for (const y of [y0 - 0.02, y1 + 0.02]) {
    const p = await screen(
      page,
      (x0 + x1) / 2,
      y < y0 ? y0 - 0.1 : y1 + 0.1,
      1.2,
    );
    await page.mouse.move(p.x, p.y, { steps: 3 });
    const text =
      (await page
        .locator(".viewer3d-cote")
        .textContent()
        .catch(() => "")) ?? "";
    if (text.includes("épaisseur du mur")) {
      await page.mouse.click(p.x, p.y);
      await page.keyboard.type("30");
      await expect(page.locator(".viewer3d-cote")).toContainText("30 cm");
      await sceneReloaded(page, async () => {
        await page.keyboard.press("Enter");
      });
      pulled = "ok";
      break;
    }
  }
  expect(pulled, "une face de mur tirable vue de face").toBe("ok");
  const thick = async () =>
    (await elements(page, id, level)).filter(
      ([, e]) => e.type === "wall" && e.thickness === 0.3,
    );
  await expect.poll(async () => (await thick()).length).toBe(1);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/kan92-3-mur-tire.png` });

  // Ctrl+Z : le mur reprend son épaisseur ; encore Ctrl+Z : la zone redescend au sol.
  await sceneReloaded(page, async () => {
    await page.keyboard.press("Control+z");
  });
  await expect.poll(async () => (await thick()).length).toBe(0);
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await zones())[0]![1].elevation).toBe(0);
  expect(zoneId).toBeTruthy();
  expect(walls.length).toBe(4);
});
