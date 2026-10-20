import { expect, test, type Locator, type Page } from "@playwright/test";
import { createEmptyProject, goSpace } from "./helpers";

/** KAN-64 : plus rien ne se chevauche dans l'espace de travail. L'Assistant et les
 *  Commentaires vivent dans le panneau de droite (zones empilées, séparation réglable, zones
 *  repliables) ; la bulle de la barre du haut ouvre les Commentaires et compte les fils
 *  ouverts ; en bas, il ne reste que le zoom ; panneau fermé, l'espace de travail prend toute
 *  la largeur. */

type Box = { x: number; y: number; width: number; height: number };

function overlap(a: Box, b: Box): boolean {
  const tol = 1;
  return (
    a.x + tol < b.x + b.width &&
    b.x + tol < a.x + a.width &&
    a.y + tol < b.y + b.height &&
    b.y + tol < a.y + a.height
  );
}

async function boardWithAComment(page: Page, name: string): Promise<string> {
  const id = await createEmptyProject(page, name);
  await page.request.post(`/api/projects/${id}/comments`, {
    data: {
      target: { at: { x: 300, y: 200 } },
      text: "Et si on ouvrait ce mur ?",
    },
  });
  await goSpace(page, "board");
  await expect(page.locator(".board-stage")).toBeVisible();
  return id;
}

const fixed = (page: Page): [string, Locator][] => [
  ["barre du haut", page.locator(".topbar")],
  ["panneau de droite", page.locator(".side .side-card")],
  ["zoom", page.getByRole("group", { name: "Zoom de l'espace de travail" })],
  [
    "outils",
    page.getByRole("toolbar", { name: "Outils de l'espace de travail" }),
  ],
];

for (const [width, height] of [
  [1280, 800],
  [1920, 1080],
] as const) {
  test(`KAN-64 (MH1) : à ${width}×${height}, aucun élément fixe n'en chevauche un autre`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await boardWithAComment(page, `Disposition ${width}`);
    const boxes: [string, Box][] = [];
    for (const [label, locator] of fixed(page)) {
      await expect(locator, label).toBeVisible();
      boxes.push([label, (await locator.boundingBox())!]);
    }
    for (let i = 0; i < boxes.length; i++)
      for (let j = i + 1; j < boxes.length; j++)
        expect(
          overlap(boxes[i]![1], boxes[j]![1]),
          `${boxes[i]![0]} / ${boxes[j]![0]}`,
        ).toBe(false);
    // Tout tient dans la fenêtre ; la barre « Commentaires (n) » du bas n'existe plus.
    for (const [label, box] of boxes) {
      expect(box.x + box.width, label).toBeLessThanOrEqual(width + 1);
      expect(box.y + box.height, label).toBeLessThanOrEqual(height + 1);
    }
    await expect(page.locator(".board-comments-bar")).toHaveCount(0);
    if (process.env.E2E_CAPTURE_DIR)
      await page.screenshot({
        path: `${process.env.E2E_CAPTURE_DIR}/kan64-${width}x${height}.png`,
      });
  });
}

test("KAN-64 (MH2, MH4, MH6) : zones du panneau, bulle des Commentaires, panneau fermé", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await boardWithAComment(page, "Panneau de droite");
  const side = page.locator(".side");
  // MH2 : l'Assistant en haut, les Commentaires dessous, une séparation réglable.
  const composer = side.getByRole("region", { name: "Compositeur" });
  const list = side.getByRole("region", { name: "Commentaires", exact: true });
  await expect(composer).toBeVisible();
  await expect(list).toBeVisible();
  expect((await composer.boundingBox())!.y).toBeLessThan(
    (await list.boundingBox())!.y,
  );
  const split = side.getByRole("separator", {
    name: "Séparation entre l'Assistant et les Commentaires",
  });
  const top = side.locator(".board-zone.top");
  const before = (await top.boundingBox())!.height;
  const s = (await split.boundingBox())!;
  await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2);
  await page.mouse.down();
  await page.mouse.move(s.x + s.width / 2, s.y + s.height / 2 - 60, {
    steps: 4,
  });
  await page.mouse.up();
  expect((await top.boundingBox())!.height).toBeLessThan(before - 30);
  // Zones repliables.
  await side.getByRole("button", { name: "Replier les commentaires" }).click();
  await expect(list).toHaveCount(0);
  await side.getByRole("button", { name: "Déplier les commentaires" }).click();
  await expect(list).toBeVisible();
  await side.getByRole("button", { name: "Replier l'Assistant" }).click();
  await expect(composer).toHaveCount(0);
  await side.getByRole("button", { name: "Déplier l'Assistant" }).click();
  await expect(composer).toBeVisible();
  // L'Activité est repliée d'office, et se déplie.
  await expect(side.getByRole("region", { name: "Activité" })).toHaveCount(0);
  await side.getByRole("button", { name: "Déplier l'activité" }).click();
  await expect(side.getByRole("region", { name: "Activité" })).toBeVisible();

  // MH4 : la bulle compte les fils ouverts et ouvre les Commentaires.
  const bubble = page.locator(".topbar").getByRole("button", {
    name: /^Commentaires, 1 ouvert$/,
  });
  await expect(bubble).toBeVisible();
  await expect(page.getByTestId("comments-count")).toHaveText("1");
  await side.getByRole("button", { name: "Replier les commentaires" }).click();
  await bubble.click();
  await expect(list).toBeVisible();

  // MH6 : panneau fermé, toute la largeur ; rouvert d'un clic.
  const stage = page.locator(".board-stage");
  const narrow = (await stage.boundingBox())!.width;
  await page.getByRole("button", { name: "Masquer le panneau" }).click();
  await expect(side).toHaveClass(/\bclosed\b/);
  expect((await stage.boundingBox())!.width).toBeGreaterThan(narrow + 200);
  await page.getByRole("button", { name: "Afficher le panneau" }).click();
  await expect(composer).toBeVisible();
});

test("KAN-64 (propriétés) : une sélection prend la place de l'Assistant, retour en un clic", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await boardWithAComment(page, "Sélection");
  const side = page.locator(".side");
  const frame = page.locator(".board-item.kind-frame.role-photos");
  await frame.locator(".board-frame-name").click();
  await expect(
    side.getByRole("toolbar", { name: "Actions de la sélection" }),
  ).toBeVisible();
  await expect(side.getByRole("region", { name: "Compositeur" })).toHaveCount(
    0,
  );
  await side.getByRole("button", { name: "← Assistant" }).click();
  await expect(side.getByRole("region", { name: "Compositeur" })).toBeVisible();
});
