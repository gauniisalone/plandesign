import { expect, test, type Page } from "@playwright/test";
import { createEmptyProject } from "./helpers";

/** Responsive (directive du 29 sept. 2026) : à chaque largeur d'ordinateur, aucun défilement
 *  horizontal, aucune zone flottante qui en chevauche une autre (vue Plan et vue Plan + 3D),
 *  l'aide-mémoire hors de la palette, les sommets et les cotes hors des barres et des murs. */

const SIZES: [number, number][] = [
  [1920, 1080],
  [1536, 864],
  [1366, 768],
  [1280, 720],
  [1024, 768],
  [768, 1024],
];

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

const overlap = (a: Box, b: Box): boolean =>
  a.x < b.x + b.width - 1 &&
  b.x < a.x + a.width - 1 &&
  a.y < b.y + b.height - 1 &&
  b.y < a.y + a.height - 1;

async function boxes(
  page: Page,
  selectors: string[],
): Promise<[string, Box][]> {
  const out: [string, Box][] = [];
  for (const sel of selectors) {
    const loc = page.locator(sel);
    const count = await loc.count();
    for (let i = 0; i < count; i++) {
      if (!(await loc.nth(i).isVisible())) continue;
      const box = await loc.nth(i).boundingBox();
      if (box) out.push([count > 1 ? `${sel}[${i}]` : sel, box]);
    }
  }
  return out;
}

async function expectDisjoint(page: Page, selectors: string[]): Promise<void> {
  const found = await boxes(page, selectors);
  for (let i = 0; i < found.length; i++)
    for (let j = i + 1; j < found.length; j++)
      expect(
        overlap(found[i]![1], found[j]![1]),
        `${found[i]![0]} chevauche ${found[j]![0]}`,
      ).toBe(false);
}

async function expectNoHorizontalScroll(page: Page): Promise<void> {
  const extra = await page.evaluate(
    () =>
      Math.max(
        document.documentElement.scrollWidth,
        document.body.scrollWidth,
      ) - window.innerWidth,
  );
  expect(extra, "défilement horizontal").toBeLessThanOrEqual(0);
}

for (const [width, height] of SIZES) {
  test(`responsive ${width} × ${height} : ni chevauchement ni défilement horizontal`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    await expect(
      page.getByRole("heading", {
        name: "Vos espaces de travail",
      }),
    ).toBeVisible();
    await expectNoHorizontalScroll(page);
    await createEmptyProject(
      page,
      `Responsive ${width} — un nom de projet volontairement très long`,
    );
    await expect(page).toHaveURL(/\/import$/);
    await expectNoHorizontalScroll(page);
    await expectDisjoint(page, [".topbar", ".import-card"]);
    await page.getByRole("button", { name: "Continuer vers le plan" }).click();

    // Une pièce tracée dans la zone libre du plan, à la taille de cet écran.
    const svg = page.locator("svg.plan");
    await expect(svg).toBeVisible();
    const plan = await svg.boundingBox();
    expect(plan).not.toBeNull();
    if (!plan) return;
    await page.keyboard.press("w");
    const xs = [plan.width * 0.32, plan.width * 0.68];
    const ys = [plan.height * 0.42, plan.height * 0.7];
    const corners: [number, number][] = [
      [xs[0]!, ys[1]!],
      [xs[1]!, ys[1]!],
      [xs[1]!, ys[0]!],
      [xs[0]!, ys[0]!],
      [xs[0]!, ys[1]!],
    ];
    for (const [x, y] of corners) await svg.click({ position: { x, y } });
    await page.keyboard.press("Escape");
    await expect(page.locator("polygon.wall")).toHaveCount(4);
    await page.keyboard.press("v");
    await page.getByRole("button", { name: "Recadrer" }).click();

    // Vue Plan.
    const floats = [
      ".topbar",
      ".palette",
      ".viewbar-wrap",
      ".strip",
      ".bottombar",
      ".side",
    ];
    await expectNoHorizontalScroll(page);
    await expectDisjoint(page, floats);
    const bars = await boxes(page, [
      ".topbar",
      ".palette",
      ".viewbar-wrap",
      ".strip",
      ".bottombar",
    ]);
    const hud = await page.locator("svg.plan text.hud").first().boundingBox();
    expect(hud).not.toBeNull();
    if (hud)
      for (const [name, box] of bars)
        expect(overlap(hud, box), `aide-mémoire sous ${name}`).toBe(false);
    const walls = await boxes(page, ["polygon.wall"]);
    for (const vertex of await page.locator("circle.vertex").all()) {
      const v = await vertex.boundingBox();
      if (!v) continue;
      for (const [name, box] of bars)
        expect(overlap(v, box), `sommet sous ${name}`).toBe(false);
    }
    for (const dim of await page.locator("text.dim").all()) {
      const d = await dim.boundingBox();
      if (!d) continue;
      for (const [name, box] of walls)
        expect(overlap(d, box), `cote sur ${name}`).toBe(false);
    }

    // Vue Plan + 3D : la barre de vues ne croise aucune autre barre.
    await page.getByRole("button", { name: "Plan + 3D" }).click();
    await expect(page.locator(".pane-3d")).toBeVisible();
    await page.waitForTimeout(400);
    await expectNoHorizontalScroll(page);
    await expectDisjoint(page, [
      ".topbar",
      ".palette",
      ".strip",
      ".bottombar",
      ".side",
      ".viewbar-wrap",
    ]);
    // Recadré dans le volet du plan : les sommets restent hors des barres.
    await page.getByRole("button", { name: "Recadrer" }).click();
    await page.waitForTimeout(200);
    const splitBars = await boxes(page, [
      ".palette",
      ".viewbar-wrap",
      ".strip",
      ".bottombar",
    ]);
    for (const vertex of await page.locator("circle.vertex").all()) {
      const v = await vertex.boundingBox();
      if (!v) continue;
      for (const [name, box] of splitBars)
        expect(overlap(v, box), `vue partagée : sommet sous ${name}`).toBe(
          false,
        );
    }
    const split = await boxes(page, [".palette", ".pane-3d"]);
    if (split.length === 2)
      expect(
        overlap(split[0]![1], split[1]![1]),
        "palette sur le volet 3D",
      ).toBe(false);
  });
}
