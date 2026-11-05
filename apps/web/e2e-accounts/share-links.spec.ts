import { execFileSync, spawn } from "node:child_process";
import path from "node:path";
import {
  expect,
  request as playwrightRequest,
  type Browser,
  type BrowserContext,
  type Page,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { person as openPerson, test } from "./people";
import { SHARE_ACCOUNT } from "./share-account";

/** KAN-101 : partager UN rendu réaliste par un lien public. L'éditeur (compte autorisé aux
 *  rendus) crée le lien depuis la visionneuse ; la page s'ouvre sans compte, sans nom de
 *  projet quand il est décoché, avec `noindex` et une CSP stricte ; un lecteur est refusé ;
 *  « Révoquer » rend le lien 404 ; réglage éteint (second serveur sur la même base, sans
 *  OPENPLAN_SHARE_LINKS) : 404. Le rendu fait est posé par seed_done_render.py (la place
 *  d'un Mo des e2e-accounts refuserait une demande). Chaque test a sa propre adresse, de
 *  198.51.100.123 à 131 (libres ; .101 à .103 sont à accounts.spec). */

const ROOT = path.resolve("../..");
const DIR = "apps/web/.e2e-accounts";
const DATABASE =
  process.env.E2E_DATABASE_URL?.trim() || `sqlite:///${DIR}/comptes.db`;
const TOKEN_URL = /\/api\/share\/[A-Za-z0-9_-]{43}$/;
type State = Awaited<ReturnType<BrowserContext["storageState"]>>;
/** La session du compte autorisé : une seule connexion pour tout le fichier (3 codes par
 *  adresse et par quart d'heure), reprise par chaque test avec sa propre adresse IP. */
let editorState: State | undefined;
/** Les pages qui portent la route de /api/health : retirée en fin de test, sinon un
 *  route.fetch encore en vol tombe hors de tout test (« Test ended »). */
const routed: Page[] = [];

// Avant la fermeture des contextes par la fixture de ./people (qui vient après afterEach).
test.afterEach(async () => {
  for (const page of routed.splice(0)) {
    if (!page.isClosed()) await page.unrouteAll({ behavior: "ignoreErrors" });
  }
});

async function person(
  browser: Browser,
  ip: string,
  storageState?: State,
): Promise<Page> {
  const page = await openPerson(browser, ip, { storageState });
  // Comme la production (ni Blender ni GPU) : le cadre Rendus montre les rendus réalistes du
  // poste. Le serveur des e2e-accounts, lui, peut trouver Blender sur la machine.
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch();
    const json = { ...(await response.json()), blender: false };
    await route.fulfill({ response, json });
  });
  routed.push(page);
  return page;
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

/** Le compte autorisé, connecté (une fois par fichier), sur la page d'accueil. */
async function editor(browser: Browser, ip: string): Promise<Page> {
  if (!editorState) {
    const first = await person(browser, "198.51.100.123");
    await signIn(first, SHARE_ACCOUNT.email);
    editorState = await first.context().storageState();
    await first.context().close();
  }
  const page = await person(browser, ip, editorState);
  await gotoApp(page, "/");
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible({ timeout: 30_000 });
  return page;
}

const origin = (page: Page) => ({ Origin: new URL(page.url()).origin });

/** Un espace avec une pièce de 4 × 3 m, un point de vue, et un rendu réaliste fait. */
async function renderedProject(
  page: Page,
  name: string,
): Promise<{ id: string; rid: string }> {
  const created = await page.request.post("/api/projects", {
    data: { name },
    headers: origin(page),
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(project.levels)[0]!;
  const ops = `/api/projects/${project.id}/levels/${level}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
  for (let i = 0; i < 4; i++) {
    const [a, b] = [corners[i]!, corners[(i + 1) % 4]!];
    const wall = await page.request.post(ops, {
      data: {
        op: "add_wall",
        start: { x: a[0], y: a[1] },
        end: { x: b[0], y: b[1] },
      },
      headers: origin(page),
    });
    expect(wall.ok()).toBe(true);
  }
  const camera = await page.request.post(ops, {
    data: {
      op: "add_camera",
      position: { x: 1, y: 1 },
      yaw: 0.6,
      name: "Vue fenêtre",
    },
    headers: origin(page),
  });
  expect(camera.ok()).toBe(true);
  const rid = execFileSync(
    "uv",
    [
      "run",
      "--extra",
      "comptes",
      "python",
      "apps/web/e2e-accounts/seed_done_render.py",
      DATABASE,
      `${DIR}/data`,
      project.id,
      SHARE_ACCOUNT.email,
    ],
    { cwd: ROOT, encoding: "utf-8" },
  )
    .trim()
    .split("\n")
    .pop()!;
  return { id: project.id, rid };
}

/** La visionneuse du rendu fait, ouverte depuis la galerie du cadre Rendus. */
async function openViewer(page: Page, id: string) {
  await gotoApp(page, `/#/projects/${id}/board`);
  const frame = page
    .getByRole("region", { name: "Espace de travail" })
    .getByRole("region", { name: "Rendus réalistes" });
  await frame
    .getByRole("button", { name: /^Ouvrir le rendu/ })
    .click({ timeout: 30_000 });
  return page.getByRole("dialog", { name: "Visionneuse des rendus" });
}

test("un éditeur partage un rendu ; la page s'ouvre sans compte, sans nom de projet", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const page = await editor(browser, "198.51.100.124");
  const { id } = await renderedProject(page, "Chambre de Léa (KAN-101)");
  const viewer = await openViewer(page, id);
  const named = viewer.getByLabel("Afficher le nom du projet");
  await expect(named).toBeChecked();
  await named.uncheck();
  await viewer.getByRole("button", { name: "Partager", exact: true }).click();
  const link = viewer.getByRole("textbox", { name: "Lien public du rendu" });
  await expect(link).toHaveValue(TOKEN_URL);
  const url = await link.inputValue();
  await expect(
    viewer.getByRole("list", { name: "Liens publics actifs" }).getByRole("listitem"),
  ).toHaveCount(1);

  // Un visiteur sans compte, ailleurs.
  const visitor = await browser.newContext({
    extraHTTPHeaders: { "X-Forwarded-For": "198.51.100.125" },
  });
  const anonymous = await visitor.newPage();
  const opened = await anonymous.goto(url);
  expect(opened?.status()).toBe(200);
  const headers = opened!.headers();
  expect(headers["x-robots-tag"]).toContain("noindex");
  expect(headers["content-security-policy"]).toContain("default-src 'none'");
  expect(headers["content-security-policy"]).not.toContain("script-src");
  expect(headers["referrer-policy"]).toBe("no-referrer");
  expect(headers["cache-control"]).toBe("no-store");
  await expect(anonymous.locator('meta[name="robots"]')).toHaveAttribute(
    "content",
    /noindex/,
  );
  const image = anonymous.getByRole("img", { name: "Rendu réaliste" });
  await expect
    .poll(() => image.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBe(320);
  const text = await anonymous.locator("body").innerText();
  expect(text).toContain("PlanDesign");
  expect(text).not.toContain("Chambre de Léa");
  expect(text).not.toContain(SHARE_ACCOUNT.email);
  await visitor.close();
});

test("un lecteur ne peut ni créer ni lister de lien ; un non-membre non plus", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const owner = await editor(browser, "198.51.100.126");
  const { id, rid } = await renderedProject(owner, "Salon (KAN-101)");
  const invited = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "viewer" },
    headers: origin(owner),
  });
  expect(invited.status()).toBe(201);
  const { token } = (await invited.json()) as { token: string };

  const reader = await person(browser, "198.51.100.127");
  await signIn(reader, "lecteur-kan101@example.org");
  const create = `/api/projects/${id}/realistic/${rid}/share-links`;
  const list = `/api/projects/${id}/share-links`;
  // Pas encore membre : la même 404 qu'un projet inconnu.
  expect(
    (await reader.request.post(create, { data: {}, headers: origin(reader) }))
      .status(),
  ).toBe(404);
  const joined = await reader.request.post("/api/auth/invitation/accept", {
    data: { token },
    headers: origin(reader),
  });
  expect(joined.status()).toBe(200);
  expect(
    (await reader.request.post(create, { data: {}, headers: origin(reader) }))
      .status(),
  ).toBe(403);
  expect((await reader.request.get(list)).status()).toBe(403);
  // Le propriétaire, lui, crée bien.
  const made = await owner.request.post(create, {
    data: {},
    headers: origin(owner),
  });
  expect(made.status()).toBe(201);
});

test("« Révoquer » : le lien répond 404 aussitôt", async ({ browser }) => {
  test.setTimeout(120_000);
  const page = await editor(browser, "198.51.100.128");
  const { id } = await renderedProject(page, "Bureau (KAN-101)");
  const viewer = await openViewer(page, id);
  await viewer.getByRole("button", { name: "Partager", exact: true }).click();
  const link = viewer.getByRole("textbox", { name: "Lien public du rendu" });
  await expect(link).toHaveValue(TOKEN_URL);
  const url = await link.inputValue();
  const visitor = await playwrightRequest.newContext({
    extraHTTPHeaders: { "X-Forwarded-For": "198.51.100.129" },
  });
  expect((await visitor.get(url)).status()).toBe(200);
  await viewer.getByRole("button", { name: /^Révoquer le lien/ }).click();
  await expect(
    viewer.getByRole("list", { name: "Liens publics actifs" }),
  ).toHaveCount(0);
  await expect(link).toHaveCount(0);
  const gone = await visitor.get(url);
  expect(gone.status()).toBe(404);
  expect(await gone.text()).toContain("plus valide");
  expect((await visitor.get(`${url}/image`)).status()).toBe(404);
  await visitor.dispose();
});

test("réglage éteint : la page publique et les routes répondent 404", async ({
  browser,
}) => {
  test.setTimeout(120_000);
  const page = await editor(browser, "198.51.100.130");
  const { id, rid } = await renderedProject(page, "Entrée (KAN-101)");
  const made = await page.request.post(
    `/api/projects/${id}/realistic/${rid}/share-links`,
    { data: {}, headers: origin(page) },
  );
  expect(made.status()).toBe(201);
  const { token } = (await made.json()) as { token: string };

  // Le même serveur, sur la même base, sans OPENPLAN_SHARE_LINKS (le défaut).
  const port = Number(process.env.OPENPLAN_PORT ?? "8000") + 101;
  const webPort = process.env.OPENPLAN_WEB_PORT ?? "5173";
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    OPENPLAN_DOTENV: "",
    OPENPLAN_AUTO_PATH: "0",
    OPENPLAN_DATABASE_URL: DATABASE,
    OPENPLAN_MAILER: "fake",
    OPENPLAN_SIGNUP: "open",
    OPENPLAN_ADMIN_EMAIL: "admin@example.org",
    OPENPLAN_PUBLIC_URL: `http://localhost:${webPort}`,
    OPENPLAN_RENDER_ACCOUNTS: SHARE_ACCOUNT.id,
  };
  delete env.OPENPLAN_SHARE_LINKS;
  const server = spawn(
    "uv",
    [
      "run",
      "--extra",
      "comptes",
      "openplan",
      "serve",
      "--port",
      String(port),
      "--data-dir",
      `${DIR}/data-off`,
    ],
    { cwd: ROOT, env, detached: true, stdio: "ignore" },
  );
  const off = await playwrightRequest.newContext({
    baseURL: `http://127.0.0.1:${port}`,
    extraHTTPHeaders: { "X-Forwarded-For": "198.51.100.131" },
  });
  try {
    await expect
      .poll(
        async () => {
          try {
            return (await off.get("/api/health")).status();
          } catch {
            return 0;
          }
        },
        { timeout: 60_000, intervals: [500] },
      )
      .toBe(200);
    const health = (await (await off.get("/api/health")).json()) as {
      share_links?: boolean;
    };
    expect(health.share_links).toBe(false);
    expect((await off.get(`/api/share/${token}`)).status()).toBe(404);
    expect((await off.get(`/api/share/${token}/image`)).status()).toBe(404);
    // Allumé (le serveur des e2e), le même lien s'ouvre : la 404 vient du réglage.
    expect((await page.request.get(`/api/share/${token}`)).status()).toBe(200);
  } finally {
    await off.dispose();
    if (server.pid) process.kill(-server.pid, "SIGTERM");
  }
});
