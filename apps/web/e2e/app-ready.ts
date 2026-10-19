import type { Page } from "@playwright/test";

/** Lot site + app du 19 oct. (PO) : après un chargement, attendre l'application affichée,
 *  pas son squelette. App.tsx pose `data-app-ready` sur <html> une fois /api/health lue et
 *  la session connue ; un serveur de test lent ne fait plus échouer la première assertion. */
const READY_MS = 30_000;

export async function appReady(page: Page): Promise<void> {
  await page
    .locator("html[data-app-ready]")
    .waitFor({ state: "attached", timeout: READY_MS });
}

export async function gotoApp(page: Page, url: string): Promise<void> {
  await page.goto(url);
  await appReady(page);
}

export async function reloadApp(page: Page): Promise<void> {
  await page.reload();
  await appReady(page);
}
