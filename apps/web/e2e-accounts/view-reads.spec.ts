import {
  expect,
  test,
  type Browser,
  type Page,
  type WebSocketRoute,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-56 : zoomer ou déplacer la vue ne relit ni les médias ni les rendus contextualisés
 *  de l'espace de travail ; un média ajouté (chez moi ou chez un autre membre) ou un rendu
 *  contextualisé qui se termine les relit toujours, sans recharger la page. Un test par
 *  Must have, chacun avec sa propre adresse (limite de 10 connexions par minute et par
 *  adresse). */

/** Une image PNG de 1 × 1 pixel. */
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

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

/** Connexion par code, sur l'accueil ou sur une invitation déjà ouverte. */
async function signIn(page: Page, email: string, path = "/"): Promise<void> {
  await gotoApp(page, path);
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
  await expect(skip.or(page.locator(".editor, .boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await skip.isVisible()) await skip.click();
}

const unique = (who: string) =>
  `${who}-kan56-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.org`;

/** Les lectures que KAN-56 compte. */
function counter(page: Page) {
  const reads: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() === "GET" && /\/(media|contextual)$/.test(p)) reads.push(p);
  });
  return {
    mark: () => reads.length,
    since: (mark: number) => reads.slice(mark),
  };
}

async function newBoard(page: Page, name: string): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name },
    headers: { Origin: new URL(page.url()).origin },
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}/board`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  // Page calme : en développement, React monte l'espace de travail deux fois.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);
  return id;
}

// KAN-43 : la vue (zoom, déplacement) s'enregistre par PUT …/view, plus par le tableau.
const boardSaved = (page: Page) =>
  page.waitForResponse(
    (r) =>
      r.request().method() === "PUT" &&
      new URL(r.url()).pathname.endsWith("/view"),
  );

test("KAN-56 (1) : zoomer puis enregistrer la vue ne relit ni médias ni rendus contextualisés", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.56");
  const reads = counter(page);
  await signIn(page, unique("vue"));
  await newBoard(page, "Vue seule (KAN-56)");
  const mark = reads.mark();
  // KAN-43 : la preuve même : zoomer n'écrit plus le tableau (aucun PATCH …/board/items).
  const boardWrites: string[] = [];
  page.on("request", (r) => {
    if (
      r.method() === "PATCH" &&
      new URL(r.url()).pathname.endsWith("/board/items")
    )
      boardWrites.push(r.url());
  });
  const saved = boardSaved(page);
  const zoom = page.getByRole("group", { name: "Zoom de l'espace de travail" });
  await zoom.getByRole("button", { name: "Zoomer", exact: true }).click();
  await zoom.getByRole("button", { name: "Zoomer", exact: true }).click();
  await saved;
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(800);
  expect(reads.since(mark)).toEqual([]);
  expect(boardWrites).toEqual([]);
});

test("KAN-56 (2a) : une image importée dans Inspirations relit les médias", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.57");
  const reads = counter(page);
  await signIn(page, unique("import"));
  await newBoard(page, "Import (KAN-56)");
  const mark = reads.mark();
  const chooser = page.waitForEvent("filechooser");
  await page
    .locator(".board-item.kind-frame.role-inspirations")
    .getByRole("button", { name: /Importer/ })
    .click();
  await (
    await chooser
  ).setFiles({ name: "ambiance.png", mimeType: "image/png", buffer: PNG });
  await expect(page.locator(".board-item.kind-file")).toHaveCount(1, {
    timeout: 15_000,
  });
  await expect
    .poll(() => reads.since(mark).filter((p) => p.endsWith("/media")).length)
    .toBeGreaterThan(0);
  await shot(page, "kan56-2a-import");
});

test("KAN-56 (2b) : un rendu contextualisé qui se termine apparaît sans recharger la page", async ({
  browser,
}) => {
  // La fin d'un rendu est simulée (pas de GPU) : à la relecture suivante du tableau, le
  // serveur renvoie la carte de la variante, et la liste des rendus contextualisés son entrée.
  const page = await person(browser, "198.51.100.58");
  let allow = true;
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws\/projects\//, (ws) => {
    if (!allow) {
      void ws.close({ code: 1011, reason: "refusé" });
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
  });
  const reads = counter(page);
  await signIn(page, unique("rendu"));
  await newBoard(page, "Rendu terminé (KAN-56)");
  await expect.poll(() => sockets.length).toBeGreaterThan(0);
  const run = "00000000-0000-4000-8000-000000000056";
  await page.route("**/api/projects/*/board", async (route) => {
    const res = await route.fetch();
    const board = (await res.json()) as { items: Record<string, unknown>[] };
    board.items.push({
      id: "kan56-variante",
      kind: "contextual",
      x: 0,
      y: 1200,
      w: 320,
      h: 240,
      z: 1,
      locked: false,
      frame: null,
      ref: `${run}:moodboard`,
      text: "Variante terminée",
      role: null,
    });
    await route.fulfill({ response: res, json: board });
  });
  await page.route("**/api/projects/*/contextual", (route) =>
    route.fulfill({
      json: [
        {
          run_id: run,
          variant: "moodboard",
          image: "moodboard.png",
          prompt: "salon clair",
          intention: "",
          budget: "standard",
          source: { kind: "photo", id: "x", label: "Photo" },
          context: [],
          estimated: true,
          strength: 0.6,
          control_scale: 0.8,
          seed: 1,
          steps: 20,
          model_ids: [],
          duration_s: 1,
          created_at: new Date().toISOString(),
          label: "Variante terminée",
        },
      ],
    }),
  );
  const mark = reads.mark();
  // La relecture du tableau (comme après la fin d'un rendu ou un retour de connexion).
  allow = false;
  for (const ws of sockets.splice(0))
    await ws.close({ code: 1012, reason: "redémarrage" });
  await page.waitForTimeout(1_500);
  allow = true;
  await expect(page.locator(".board-item.kind-contextual")).toHaveCount(1, {
    timeout: 30_000,
  });
  await expect
    .poll(
      () => reads.since(mark).filter((p) => p.endsWith("/contextual")).length,
    )
    .toBeGreaterThan(0);
  await shot(page, "kan56-2b-rendu-termine");
});

test("KAN-56 (3) : un média ajouté par un autre membre apparaît en direct", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.59");
  const reads = counter(owner);
  await signIn(owner, unique("alice"));
  const id = await newBoard(owner, "Partagé (KAN-56)");
  const invite = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: { Origin: new URL(owner.url()).origin },
  });
  const { token } = (await invite.json()) as { token: string };
  const guest = await person(browser, "198.51.100.60");
  await signIn(guest, unique("bruno"), `/#/invitation/${token}`);
  await expect(guest.locator(".editor")).toBeVisible({ timeout: 30_000 });
  await gotoApp(guest, `/#/projects/${id}/board`);
  await expect(guest.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await guest.waitForLoadState("networkidle");
  const mark = reads.mark();
  const chooser = guest.waitForEvent("filechooser");
  await guest
    .locator(".board-item.kind-frame.role-inspirations")
    .getByRole("button", { name: /Importer/ })
    .click();
  await (
    await chooser
  ).setFiles({ name: "ambiance.png", mimeType: "image/png", buffer: PNG });
  await expect(owner.locator(".board-item.kind-file")).toHaveCount(1, {
    timeout: 20_000,
  });
  await expect
    .poll(() => reads.since(mark).filter((p) => p.endsWith("/media")).length)
    .toBeGreaterThan(0);
  await shot(owner, "kan56-3-autre-membre");
});
