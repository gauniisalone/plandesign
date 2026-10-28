import { expect, test, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";

/** KAN-110 : les Réglages s'ouvrent lisibles. Bug de prod (MEP 25) : depuis l'accueil, le
 *  panneau sortait collé en haut à gauche, haut de 24 px (son seul padding), avec une barre
 *  de défilement (deux règles CSS lui donnaient à la fois `top` et `bottom`). Les tests
 *  d'avant cliquaient dedans sans le voir : Playwright fait défiler un panneau avant chaque
 *  clic. Ici, la boîte elle-même est mesurée, aux deux tailles d'écran relevées. */

const SIZES = [
  { width: 1411, height: 796 },
  { width: 1920, height: 1080 },
];

/** Le panneau montre tout son contenu sans défiler (sa hauteur suit son contenu ; avec le
 *  bug, 0 px utile pour plusieurs centaines de contenu), et tient dans l'écran. */
async function expectWhole(page: Page, panel: Locator): Promise<void> {
  await expect(panel).toBeVisible();
  const fit = await panel.evaluate((el) => ({
    scroll: el.scrollHeight,
    client: el.clientHeight,
  }));
  expect(fit.client).toBeGreaterThan(40);
  expect(fit.scroll).toBeLessThanOrEqual(fit.client + 1);
  const box = await panel.boundingBox();
  const screen = page.viewportSize();
  expect(box && screen).toBeTruthy();
  if (!box || !screen) return;
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(screen.height + 1);
  expect(box.x + box.width).toBeLessThanOrEqual(screen.width + 1);
}

for (const size of SIZES) {
  test.describe(`${size.width} × ${size.height}`, () => {
    test.use({ viewport: size });

    test("KAN-110 : les Réglages de l'accueil s'ouvrent sous l'engrenage, entiers", async ({
      page,
    }) => {
      await gotoApp(page, "/");
      const gear = page.getByRole("button", { name: "Réglages", exact: true });
      await gear.click();
      const panel = page.getByRole("dialog", { name: "Réglages" });
      await expectWhole(page, panel);
      await expect(panel.locator(".theme-field")).toBeInViewport({ ratio: 1 });
      // Sous l'engrenage, aligné à droite : pas collé au bord gauche de l'écran.
      const g = await gear.boundingBox();
      const box = await panel.boundingBox();
      expect(g && box).toBeTruthy();
      if (!g || !box) return;
      expect(box.y).toBeGreaterThanOrEqual(g.y + g.height);
      expect(box.x).toBeGreaterThan(size.width / 2);
    });

    test("KAN-110 : les Réglages d'un espace de travail (Paramètres avancés) se lisent", async ({
      page,
    }) => {
      const r = await page.request.post("/api/projects", {
        data: { name: "Volet KAN-110" },
      });
      const { id } = (await r.json()) as { id: string };
      await gotoApp(page, `/#/projects/${id}`);
      await page.getByRole("button", { name: "Paramètres avancés" }).click();
      const group = page.locator("details.adv-group", {
        has: page.getByText("Réglages : unités, nord et thème"),
      });
      await expect(group).toBeVisible();
      // Le volet défile, c'est voulu : au moins 200 px de réglages à l'écran, le thème dedans.
      const box = await group.boundingBox();
      const screen = page.viewportSize();
      expect(box && screen).toBeTruthy();
      if (!box || !screen) return;
      const shown =
        Math.min(box.y + box.height, screen.height) - Math.max(box.y, 0);
      expect(shown).toBeGreaterThanOrEqual(200);
      await expect(group.locator(".theme-field")).toBeInViewport();
    });
  });
}
