import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject } from "./helpers";

/** E4 : tourner sur le plan. Meubles et tapis (rond au-delà de la face avant, angle en
 *  direct, 90° en priorité, Maj libre) ; escalier (crans de 90°, trémie vérifiée en direct,
 *  refus au lâcher) ; porte (gonds et sens d'un clic ou au clavier, débattement vérifié).
 *  Tout s'annule. */

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

/** Saisit le rond de rotation et l'amène à `angle` (degrés écran, 0 = droite, 90 = haut)
 *  autour de `c`, à 90 px. */
async function turnTo(
  page: Page,
  handle: Locator,
  c: { x: number; y: number },
  angle: number,
) {
  const h = await center(handle);
  await page.mouse.move(h.x, h.y);
  await page.mouse.down();
  const a = (angle * Math.PI) / 180;
  await page.mouse.move(c.x + 90 * Math.cos(a), c.y - 90 * Math.sin(a), {
    steps: 8,
  });
}

const yawOf = (e: Json) => (e.transform as { yaw: number }).yaw;

test("meuble : rond de rotation, angle en direct, 90° en priorité, Maj libre, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E rotation meuble");
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.getByLabel("Rechercher un meuble").fill("bureau 140");
  await page.locator('button.card[data-asset="desk_140"]').click();
  await page.mouse.click(box.x + 550, box.y + 500);
  await expect
    .poll(async () => (await elementsOf(page, id, "furniture")).length)
    .toBe(1);
  await page.keyboard.press("v");
  await page.locator("polygon.furniture").click();
  const handle = page.locator("circle.rotate-handle[data-spin]");
  await expect(handle).toBeVisible();
  // Le rond est hors du meuble, au bout d'une tige.
  const fb = (await page.locator("polygon.furniture").boundingBox())!;
  const h0 = await center(handle);
  expect(
    h0.x < fb.x ||
      h0.x > fb.x + fb.width ||
      h0.y < fb.y ||
      h0.y > fb.y + fb.height,
  ).toBe(true);
  const [desk0] = await elementsOf(page, id, "furniture");
  const mid = await center(page.locator("polygon.furniture"));

  // Vers la droite à 4° près : happé par le cran de 90°, angle affiché en direct.
  await turnTo(page, handle, mid, 4);
  await expect(page.locator("text.rotate-angle")).toHaveText("270°");
  await page.screenshot({ path: path.join(SHOTS, "e4_meuble_rotation.png") });
  await page.mouse.up();
  await expect
    .poll(async () => yawOf((await elementsOf(page, id, "furniture"))[0]!))
    .toBeCloseTo(-Math.PI / 2, 2);

  // Maj : angle libre (ni 15° ni 90°).
  await page.locator("polygon.furniture").click();
  const mid2 = await center(page.locator("polygon.furniture"));
  await page.keyboard.down("Shift");
  await turnTo(page, handle, mid2, 52);
  await page.mouse.up();
  await page.keyboard.up("Shift");
  await expect
    .poll(async () => {
      const deg =
        (yawOf((await elementsOf(page, id, "furniture"))[0]!) * 180) / Math.PI;
      return Math.abs(deg / 15 - Math.round(deg / 15)) > 0.05;
    })
    .toBe(true);

  // Annuler deux fois : le lacet d'origine revient.
  await page.keyboard.press("Control+z");
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => yawOf((await elementsOf(page, id, "furniture"))[0]!))
    .toBeCloseTo(yawOf(desk0!), 3);
});

test("tapis : même rond de rotation qu'un meuble", async ({ page }) => {
  const id = await createRoom(page, "E2E rotation tapis");
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.getByLabel("Rechercher un meuble").fill("tapis");
  await page.locator('button.card[data-asset="rug_rect"]').click();
  await page.mouse.click(box.x + 550, box.y + 500);
  // Le fantôme de pose est aussi un polygone « furniture » : attendre le tapis côté serveur.
  await expect
    .poll(async () => (await elementsOf(page, id, "furniture")).length)
    .toBe(1);
  await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(1);
  await page.keyboard.press("v");
  const [rug0] = await elementsOf(page, id, "furniture");
  await page.locator("polygon.furniture").click();
  const handle = page.locator("circle.rotate-handle[data-spin]");
  await expect(handle).toBeVisible();
  const rmid = await center(page.locator("polygon.furniture"));
  await turnTo(page, handle, rmid, 180);
  await expect(page.locator("text.rotate-angle")).toHaveText("90°");
  await page.mouse.up();
  await expect
    .poll(async () =>
      Math.abs(
        yawOf((await elementsOf(page, id, "furniture"))[0]!) - yawOf(rug0!),
      ),
    )
    .toBeGreaterThan(1.5);
});

test("escalier : rotation par crans de 90°, trémie dans un mur refusée au lâcher, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E rotation escalier");
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.locator('button.card[data-asset="stairs:straight"]').click();
  await page.mouse.click(box.x + 550, box.y + 620);
  await expect(page.locator("g.stairs")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("g.stairs polygon.stairs-outline").click({ force: true });
  const handle = page.locator("circle.rotate-handle[data-spin-stairs]");
  await expect(handle).toBeVisible();
  const [st0] = await elementsOf(page, id, "stairs");
  const smid = await center(page.locator("g.stairs polygon.stairs-outline"));

  // Vers le haut (perpendiculaire au mur du bas) : la trémie passe dans le mur, rouge en
  // direct, refus au lâcher, l'escalier ne bouge pas.
  await turnTo(page, handle, smid, 95);
  await expect(page.locator("text.rotate-angle")).toBeVisible();
  await expect(page.locator("g.stairs.invalid")).toHaveCount(1);
  await page.screenshot({ path: path.join(SHOTS, "e4_escalier_refus.png") });
  await page.mouse.up();
  await expect(page.getByText(/Non tourné/)).toBeVisible();
  expect((await elementsOf(page, id, "stairs"))[0]!.yaw).toBeCloseTo(
    st0!.yaw as number,
    5,
  );

  // Demi-tour (sens de montée inversé) : accepté, crans de 90°.
  await page.locator("g.stairs polygon.stairs-outline").click({ force: true });
  const h = await center(handle);
  await page.mouse.move(h.x, h.y);
  await page.mouse.down();
  await page.mouse.move(2 * smid.x - h.x + 3, 2 * smid.y - h.y + 2, {
    steps: 8,
  });
  await page.mouse.up();
  await expect
    .poll(async () => {
      const yaw = (await elementsOf(page, id, "stairs"))[0]!.yaw as number;
      const d = Math.abs(yaw - (st0!.yaw as number));
      return (
        Math.abs(d - Math.PI) < 0.01 ||
        Math.abs(d - Math.PI) > 2 * Math.PI - 0.01
      );
    })
    .toBe(true);
  await page.screenshot({
    path: path.join(SHOTS, "e4_escalier_demi_tour.png"),
  });
  await page.keyboard.press("Control+z");
  await expect
    .poll(async () => (await elementsOf(page, id, "stairs"))[0]!.yaw as number)
    .toBeCloseTo(st0!.yaw as number, 5);
});

test("porte : gonds et sens d'un clic ou au clavier, débattement vérifié, annuler", async ({
  page,
}) => {
  const id = await createRoom(page, "E2E rotation porte");
  const box = (await page.locator("svg.plan").boundingBox())!;
  await page.keyboard.press("d");
  await page.mouse.click(box.x + 550, box.y + 350);
  await expect(page.locator("polygon.opening.door")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("polygon.opening.door").click();
  const flip = page.locator("circle.flip-handle");
  await expect(flip).toBeVisible();
  const swing = async () =>
    (await elementsOf(page, id, "opening"))[0]!.swing as {
      hinge: string;
      direction: string;
    };
  expect(await swing()).toMatchObject({ hinge: "left", direction: "inward" });
  const arc0 = await page.locator("path.swing").getAttribute("d");

  // Un clic : gonds de l'autre côté, le débattement se redessine.
  await flip.click();
  await expect.poll(async () => (await swing()).hinge).toBe("right");
  await expect(page.locator("path.swing")).not.toHaveAttribute("d", arc0!);
  // O : ouverture vers l'extérieur.
  await page.keyboard.press("o");
  await expect.poll(async () => (await swing()).direction).toBe("outward");
  await page.screenshot({ path: path.join(SHOTS, "e4_porte_exterieur.png") });

  // Une table de chevet contre le mur, sous la porte : possible tant qu'elle s'ouvre
  // dehors ; la rouvrir vers l'intérieur est refusé (débattement gêné).
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.getByLabel("Rechercher un meuble").fill("chevet");
  await page.locator('button.card[data-asset="nightstand"]').click();
  await page.mouse.click(box.x + 550, box.y + 375);
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("polygon.opening.door").click();
  await page.keyboard.press("o");
  await expect(page.locator("path.swing.invalid")).toHaveCount(1);
  await expect(page.getByText(/Non retournée/)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "e4_porte_refus.png") });
  expect((await swing()).direction).toBe("outward");

  // G : gonds ; puis annuler revient à droite.
  await page.keyboard.press("g");
  await expect.poll(async () => (await swing()).hinge).toBe("left");
  await page.keyboard.press("Control+z");
  await expect.poll(async () => (await swing()).hinge).toBe("right");
});
