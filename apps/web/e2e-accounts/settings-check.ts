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
    const out = panel.getByRole("button", { name: "Me déconnecter partout" });
    await expect(out).toBeVisible();
    // Une seule mesure, prise d'un coup dans la page : la section Compte charge sa place
    // disque après l'ouverture, le panneau grandit alors (deux mesures séparées se
    // contrediraient). Relue jusqu'à stabilité ; avec le bug, 0 px utile pour toujours.
    const g = await gear.boundingBox();
    const gearBottom = g ? g.y + g.height : 0;
    await expect
      .poll(() =>
        panel.evaluate((el, below) => {
          const r = el.getBoundingClientRect();
          const button = [...el.querySelectorAll("button")].find(
            (b) => b.textContent?.trim() === "Me déconnecter partout",
          );
          const o = button?.getBoundingClientRect();
          return {
            whole:
              el.scrollHeight <= el.clientHeight + 1 && el.clientHeight > 100,
            underGear: r.top >= below,
            onScreen: r.bottom <= innerHeight + 1 && r.right <= innerWidth + 1,
            right: r.left > innerWidth / 2,
            accountInside: !!o && o.bottom <= r.bottom + 1,
          };
        }, gearBottom),
      )
      .toEqual({
        whole: true,
        underGear: true,
        onScreen: true,
        right: true,
        accountInside: true,
      });
    const backOffice = panel.getByRole("link", { name: "Back-office" });
    if (admin) await expect(backOffice).toBeVisible();
    else await expect(backOffice).toHaveCount(0);
    await gear.click();
    await expect(panel).toHaveCount(0);
  }
}
