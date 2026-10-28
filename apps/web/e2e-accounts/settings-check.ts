import { expect, type Page } from "@playwright/test";

/** KAN-110 : les Réglages de l'accueil s'ouvrent entiers, sous l'engrenage, aux deux tailles
 *  d'écran relevées. La boîte elle-même est mesurée : un clic de Playwright passe même dans
 *  un panneau de 24 px, qu'il fait défiler (c'est ainsi que le bug a échappé aux tests). */

export const SETTINGS_SIZES = [
  { width: 1411, height: 796 },
  { width: 1920, height: 1080 },
];

export async function expectHomeSettingsWhole(
  page: Page,
  admin: boolean,
): Promise<void> {
  for (const size of SETTINGS_SIZES) {
    await page.setViewportSize(size);
    const gear = page.getByRole("button", { name: "Réglages", exact: true });
    await gear.click();
    const panel = page.getByRole("dialog", { name: "Réglages" });
    await expect(panel).toBeVisible();
    // Tout le contenu se voit sans défiler (avec le bug : 0 px utile, le reste en défilement).
    const fit = await panel.evaluate((el) => ({
      scroll: el.scrollHeight,
      client: el.clientHeight,
    }));
    expect(fit.client).toBeGreaterThan(100);
    expect(fit.scroll).toBeLessThanOrEqual(fit.client + 1);
    const box = await panel.boundingBox();
    const g = await gear.boundingBox();
    expect(box && g).toBeTruthy();
    if (!box || !g) return;
    expect(box.y).toBeGreaterThanOrEqual(g.y + g.height);
    expect(box.y + box.height).toBeLessThanOrEqual(size.height + 1);
    expect(box.x).toBeGreaterThan(size.width / 2);
    expect(box.x + box.width).toBeLessThanOrEqual(size.width + 1);
    // Le compte se lit dans la boîte, sans la faire défiler.
    const out = panel.getByRole("button", { name: "Me déconnecter partout" });
    await expect(out).toBeVisible();
    const o = await out.boundingBox();
    expect(o).not.toBeNull();
    if (!o) return;
    expect(o.y + o.height).toBeLessThanOrEqual(box.y + box.height + 1);
    const backOffice = panel.getByRole("link", { name: "Back-office" });
    if (admin) await expect(backOffice).toBeVisible();
    else await expect(backOffice).toHaveCount(0);
    await gear.click();
    await expect(panel).toHaveCount(0);
  }
}
