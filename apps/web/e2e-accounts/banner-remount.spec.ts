import {
  expect,
  test,
  type Browser,
  type Page,
  type WebSocketRoute,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-48 : le bandeau « Reconnexion… » apparaît puis s'en va sans remonter l'espace de
 *  travail. Un repère posé sur son élément survit, les lectures faites au montage (médias,
 *  rendus contextualisés, réglages de l'accueil) ne repartent pas, et l'état local reste :
 *  sélection, zoom, texte en cours dans le compositeur. La relecture du tableau et du projet
 *  au retour de la connexion (B2a) est la logique de reconnexion : elle n'est pas comptée. Sa
 *  propre adresse (limite de 10 connexions par minute et par adresse). */

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

/** Lectures faites par l'espace de travail à son montage. */
const MOUNT_READS = /\/media$|\/contextual$|^\/api\/home\/settings$/;

test("KAN-48 : bandeau « Reconnexion… », l'espace de travail n'est pas remonté et garde son état", async ({
  browser,
}) => {
  const repeat = test.info().repeatEachIndex;
  const page = await person(
    browser,
    repeat === 0 ? "198.51.100.48" : `198.51.100.${200 + repeat}`,
  );
  let refuse = false;
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws\/projects\//, (ws) => {
    if (refuse) {
      void ws.close({ code: 1011, reason: "refusé" });
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
  });
  const reads: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() === "GET" && p.startsWith("/api/")) reads.push(p);
  });

  const email = `kan48-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.org`;
  await signIn(page, email);
  const made = await page.request.post("/api/projects", {
    data: { name: "Espace stable (KAN-48)" },
    headers: { Origin: new URL(page.url()).origin },
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}/board`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => sockets.length).toBeGreaterThan(0);
  // Page calme : en développement, React monte l'espace de travail deux fois au chargement.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  console.log(
    `KAN-48 lectures de montage au chargement : ${reads.filter((p) => MOUNT_READS.test(p)).join(", ")}`,
  );

  // L'état local : un repère sur l'élément, une sélection, un zoom, un texte non envoyé.
  const board = page.locator("section.board");
  await board.evaluate((el) => {
    (el as HTMLElement).dataset.kan48 = "monté";
  });
  const photos = page.locator(".board-item.kind-frame.role-photos");
  await photos.locator(".board-frame-name").click();
  await expect(photos).toHaveClass(/\bselected\b/);
  const zoom = page.getByRole("group", { name: "Zoom de l'espace de travail" });
  // Le zoom s'enregistre 1,2 s après le geste (KAN-43 : PUT …/view, plus par le tableau) :
  // on attend cet enregistrement avant de compter.
  const viewSaved = page.waitForResponse(
    (r) =>
      r.request().method() === "PUT" &&
      new URL(r.url()).pathname.endsWith("/view"),
  );
  await zoom.getByRole("button", { name: "Zoomer", exact: true }).click();
  await page.waitForTimeout(300);
  const zoomBefore = (await zoom.textContent()) ?? "";
  const composer = page.getByRole("region", { name: "Compositeur" });
  const field = composer.getByLabel("Vos envies et contraintes");
  await field.fill("salon clair, bois blond");
  await viewSaved;
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  const mark = reads.length;

  // Coupure : la connexion tombe et ne revient pas tout de suite, le bandeau s'affiche.
  refuse = true;
  for (const ws of sockets.splice(0))
    await ws.close({ code: 1012, reason: "redémarrage" });
  const banner = page.getByRole("status").filter({ hasText: "Reconnexion" });
  await expect(banner).toBeVisible();
  // (3) Le bandeau passe au-dessus du compositeur, jamais dessus (KAN-22).
  const bannerBox = (await banner.boundingBox())!;
  const composerBox = (await composer.boundingBox())!;
  expect(bannerBox.y + bannerBox.height).toBeLessThanOrEqual(composerBox.y);
  await shot(page, "kan48-bandeau");
  await page.waitForTimeout(1_500);
  // Retour du serveur : le bandeau s'en va.
  refuse = false;
  await expect(banner).toHaveCount(0, { timeout: 30_000 });
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1_000);
  const after = reads.slice(mark);
  console.log(`KAN-48 lectures après le bandeau : ${after.join(", ")}`);

  // (1) Pas de remontage, aucune lecture de montage.
  await expect(board).toHaveAttribute("data-kan48", "monté");
  expect(after.filter((p) => MOUNT_READS.test(p))).toEqual([]);
  // (2) L'état local est là.
  await expect(photos).toHaveClass(/\bselected\b/);
  await expect(zoom).toHaveText(zoomBefore);
  await expect(field).toHaveValue("salon clair, bois blond");
  await shot(page, "kan48-apres");
});
