import {
  expect,
  test,
  type Browser,
  type Locator,
  type Page,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";

/** KAN-50 : en lecture seule, l'espace de travail n'offre aucun ajout. Ni bouton « Importer »
 *  dans les cadres Photos et Inspirations, ni envoi par glisser-déposer, ni note ou lien
 *  collés, ni raccourci qui crée, duplique, supprime ou réordonne ; un message court le dit.
 *  Rien ne change pour un éditeur. Le rôle « lecteur » est simulé côté navigateur (même
 *  procédé que le test du lecteur sans outils) : le serveur, lui, accepterait l'envoi, ce qui
 *  prouve que c'est l'écran qui n'envoie rien. Un test par Must have (chacun prouve son
 *  propre rouge sans le correctif), chacun avec sa propre adresse (limite de 10 connexions
 *  par minute et par adresse). */

/** Le message entier : un mot plus court se retrouverait ailleurs (nom du projet, titres). */
const NOTICE = "Lecture seule : rien n'est ajouté à ce projet.";

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

async function openBoard(page: Page, name: string): Promise<void> {
  const made = await page.request.post("/api/projects", {
    data: { name },
    headers: { Origin: new URL(page.url()).origin },
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}/board`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
}

const frame = (page: Page, role: "photos" | "inspirations") =>
  page.locator(`.board-item.kind-frame.role-${role}`);

interface Viewer {
  page: Page;
  items: Locator;
  before: number;
  /** Requêtes d'écriture vers le projet (envoi d'image ou de pièce jointe, tableau). */
  writes: string[];
}

/** Un lecteur (rôle simulé) devant l'espace de travail d'un projet vide. */
async function viewer(
  browser: Browser,
  ip: string,
  n: number,
): Promise<Viewer> {
  const page = await person(browser, ip);
  await signIn(page, `lecteur-kan50-${n}@example.org`);
  await page.route("**/api/projects/*/role", (route) =>
    route.fulfill({ json: { role: "viewer" } }),
  );
  await openBoard(page, `Projet partagé ${n} (KAN-50)`);
  await expect(page.locator(".editor.read-only")).toBeVisible({
    timeout: 30_000,
  });
  const writes: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (r.method() !== "GET" && p.startsWith("/api/projects/"))
      writes.push(`${r.method()} ${p}`);
  });
  const items = page.locator(".board-item");
  return { page, items, before: await items.count(), writes };
}

/** Rien n'a bougé : même nombre d'éléments, aucune écriture, le message est là. */
async function nothingAdded(v: Viewer): Promise<void> {
  await expect(v.page.getByText(NOTICE)).toBeVisible();
  await v.page.waitForTimeout(1_000);
  await expect(v.items).toHaveCount(v.before);
  expect(v.writes).toEqual([]);
}

test("KAN-50 (1) : lecteur, aucun « Importer » dans les cadres Photos et Inspirations", async ({
  browser,
}) => {
  const { page } = await viewer(browser, "198.51.100.50", 1);
  for (const role of ["photos", "inspirations"] as const) {
    await expect(frame(page, role)).toBeVisible();
    await expect(
      frame(page, role).getByRole("button", { name: /Importer/ }),
    ).toHaveCount(0);
  }
  // État vide neutre : aucune invitation à déposer, ni la ligne « Plan depuis photo ».
  await expect(frame(page, "photos")).toContainText("Aucune photo de la pièce");
  await expect(frame(page, "inspirations")).toContainText(
    "Aucune image d'ambiance",
  );
  for (const role of ["photos", "inspirations"] as const)
    await expect(frame(page, role)).not.toContainText(
      /Déposez|Plan depuis photo/,
    );
  await shot(page, "kan50-1-lecteur-cadres");
});

test("KAN-50 (2) : lecteur, un fichier déposé n'envoie rien", async ({
  browser,
}) => {
  const v = await viewer(browser, "198.51.100.52", 2);
  const box = (await frame(v.page, "photos").boundingBox())!;
  const dt = await v.page.evaluateHandle(() => {
    const data = new DataTransfer();
    data.items.add(
      new File([new Uint8Array([137, 80, 78, 71])], "piece.png", {
        type: "image/png",
      }),
    );
    return data;
  });
  await v.page.locator(".board-stage").dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
  await nothingAdded(v);
  await shot(v.page, "kan50-2-lecteur-depot");
});

test("KAN-50 (4) : lecteur, un lien ou un texte collé ne crée rien", async ({
  browser,
}) => {
  const v = await viewer(browser, "198.51.100.53", 4);
  for (const text of ["https://example.org/inspiration", "Une note collée"]) {
    await v.page.evaluate((value) => {
      const data = new DataTransfer();
      data.setData("text/plain", value);
      window.dispatchEvent(
        new ClipboardEvent("paste", { clipboardData: data }),
      );
    }, text);
  }
  await nothingAdded(v);
  await shot(v.page, "kan50-4-lecteur-collage");
});

test("KAN-50 (5) : lecteur, aucun raccourci ne crée, duplique, supprime ni réordonne", async ({
  browser,
}) => {
  const v = await viewer(browser, "198.51.100.54", 5);
  await frame(v.page, "photos").locator(".board-frame-head").click();
  for (const key of [
    "Delete",
    "Backspace",
    "Control+d",
    "Control+l",
    "]",
    "[",
    "n",
    "f",
    "l",
  ])
    await v.page.keyboard.press(key);
  await expect(v.page.locator(".board-stage.tool-note")).toHaveCount(0);
  await expect(v.page.locator(".board-stage.tool-frame")).toHaveCount(0);
  await expect(frame(v.page, "photos")).toBeVisible();
  await nothingAdded(v);
  await shot(v.page, "kan50-5-lecteur-raccourcis");
});

test("KAN-50 (3) : éditeur, les « Importer » des cadres restent là", async ({
  browser,
}) => {
  const page = await person(browser, "198.51.100.51");
  await signIn(page, "editeur-kan50@example.org");
  await openBoard(page, "Projet de l'éditeur (KAN-50)");
  await expect(page.locator(".editor.read-only")).toHaveCount(0);
  for (const role of ["photos", "inspirations"] as const)
    await expect(
      frame(page, role).getByRole("button", { name: /Importer/ }),
    ).toBeVisible();
  // L'éditeur garde l'invitation à déposer, pas l'état vide du lecteur.
  await expect(frame(page, "inspirations")).toContainText(
    "Déposez vos images d'ambiance",
  );
  for (const role of ["photos", "inspirations"] as const)
    await expect(frame(page, role)).not.toContainText("Aucune");
  await shot(page, "kan50-3-editeur-importer");
});
