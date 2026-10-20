import { expect, test, type Locator, type Page } from "@playwright/test";
import {
  expectSpace,
  goSpace,
  openRendersPane,
  waitNoHeavyJob,
} from "./helpers";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { gotoApp } from "./app-ready";

// T7d1 : isolation des e2e lourds : aucun test ne commence tant que le serveur calcule
// encore pour le précédent (même s'il a échoué).
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

test("démarrage lent : le squelette tout de suite, « Connexion au serveur… » après 10 s, puis l'app", async ({
  page,
}) => {
  test.setTimeout(60_000);
  // Seule la première lecture de /api/health traîne (11 s) ; les relectures passent.
  let first = true;
  await page.route("**/api/health", async (route) => {
    if (first) {
      first = false;
      await new Promise((resolve) => setTimeout(resolve, 11_000));
    }
    await route.continue();
  });
  await page.goto("/");
  const top = page.locator(".boards-top");
  await expect(top).toBeVisible();
  await expect(top).toContainText("PlanDesign");
  await expect(page.locator("html[data-app-ready]")).toHaveCount(0);
  await expect(page.getByRole("status")).toHaveText("Connexion au serveur…", {
    timeout: 15_000,
  });
  await expect(page.locator("html[data-app-ready]")).toHaveCount(1, {
    timeout: 20_000,
  });
  await expect(page.getByText("Connexion au serveur…")).toHaveCount(0);
  await expect(page.locator(".boards-foot")).toBeVisible();
});

/** T5 : la galerie de tableaux remplace l'accueil à prompt (principe d'augus : un projet =
 *  un tableau). « + Nouveau projet » ouvre un tableau vide ; exigences, photos, plan et
 *  chemin automatique se font sur le tableau. Ces tests reprennent ceux de l'accueil (L1a,
 *  L1b, L2, L3) sur ce nouveau parcours : rien n'est perdu. */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const SHOTS = path.resolve("e2e/screenshots");
const CONSIGNE = "grenier chaleureux, bois et lin, deux lits";
const T3 =
  "Un appartement de 60 m², deux chambres, une cuisine ouverte et un coin bureau";

interface ApiProject {
  name: string;
  name_auto: boolean;
  images: Record<string, unknown>;
  pending_instruction: string | null;
  pending_answers: Record<string, unknown> | null;
  levels: Record<
    string,
    {
      elements: Record<
        string,
        {
          type: string;
          intent?: {
            instruction?: string | null;
            style?: string;
            views?: { aspect?: string };
            avoid?: string[];
          } | null;
        }
      >;
    }
  >;
}

async function project(page: Page, id: string): Promise<ApiProject> {
  return (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as ApiProject;
}

const idOf = (page: Page) => /projects\/([0-9a-f-]{36})/.exec(page.url())![1]!;

/** « + Nouveau projet » dans la galerie : le tableau s'ouvre ; renvoie le compositeur (T7b),
 *  ouvert d'office sur un projet sans rendu, sur l'onglet « Prompt libre ». */
async function newBoard(page: Page): Promise<Locator> {
  await gotoApp(page, "/");
  await page.getByRole("button", { name: "Nouveau projet" }).click();
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}$/, {
    timeout: 30_000,
  });
  await expectSpace(page, "board", { timeout: 30_000 });
  const card = page.getByRole("region", { name: "Compositeur", exact: true });
  await expect(card).toBeVisible();
  return card;
}

/** Le chemin automatique est coupé sur le serveur de test : ce projet le rallume, sans
 *  finition ni inspiration (qui chargeraient le modèle d'images). */
async function withAutoPath(page: Page) {
  await page.route("**/api/projects", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await route.fetch();
    const created = (await response.json()) as { id: string };
    await page.request.patch(`/api/projects/${created.id}`, {
      data: {
        render: {
          auto_path: true,
          auto_finish: false,
          auto_inspiration: false,
          auto_on_drop: true,
        },
      },
    });
    return route.fulfill({ response });
  });
}

/** Un fichier déposé dans un cadre du tableau (événement drop réel). */
async function dropInto(
  page: Page,
  frame: Locator,
  file: string,
  type: string,
) {
  const bytes = readFileSync(file);
  const box = (await frame.boundingBox())!;
  const dt = await page.evaluateHandle(
    ([data, name, mime]) => {
      const dt = new DataTransfer();
      dt.items.add(
        new File([new Uint8Array(data as number[])], name as string, {
          type: mime as string,
        }),
      );
      return dt;
    },
    [Array.from(bytes), path.basename(file), type] as const,
  );
  await frame.dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
}

/** Attend que les réponses et la consigne de la note soient gardées dans le projet. */
async function kept(page: Page, id: string, check: (p: ApiProject) => boolean) {
  await expect
    .poll(async () => check(await project(page, id)), { timeout: 10_000 })
    .toBe(true);
}

test("galerie : nouveau projet → tableau, consigne et photos → plan, vignette, renommage, suppression", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture image.png absente");
  test.setTimeout(240_000);
  await gotoApp(page, "/");
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
  await expect(page.locator(".boards-foot")).toContainText(
    "vos photos et plans restent sur cette machine ; rien ne part",
  );
  const card = await newBoard(page);
  const id = idOf(page);

  // La consigne dans la note Exigences : « compris » sans réseau, gardée dans le projet.
  const text = card.getByLabel("Vos envies et contraintes");
  await text.fill(CONSIGNE);
  await text.blur();
  await expect(card).toContainText(
    "grenier · chaleureux · lin · 2 lits simples",
  );
  await kept(page, id, (p) => p.pending_instruction === CONSIGNE);

  // Deux photos dans « Photos de la pièce » : la première construit le plan, sans geste.
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await dropInto(page, photos, PHOTO, "image/png");
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({
    timeout: 200_000,
  });
  await dropInto(page, photos, PHOTO, "image/png");
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(2, {
    timeout: 60_000,
  });
  const p = await project(page, id);
  expect(Object.keys(p.images)).toHaveLength(2);
  expect(p.name_auto).toBe(true);
  // A3 : plan reconstruit d'après une photo : statut honnête.
  await goSpace(page, "plan");
  // (à l'arrivée, le bandeau « Plan estimé » tient lieu de ligne de statut)
  await expect(
    page
      .locator('.auto-banner.estimated, [aria-label="Statut du plan"]')
      .first(),
  ).toContainText(/Plan estimé|Estimé — à vérifier/);

  // Retour à la galerie : la carte a sa vignette ; renommer, rouvrir, supprimer.
  await page.getByRole("button", { name: "Projets" }).click();
  const named = (await project(page, id)).name;
  const item = page.locator(".boards-card", { hasText: named });
  const cover = item.locator(".boards-cover img");
  await expect(cover).toBeVisible();
  await expect
    .poll(() => cover.evaluate((img: HTMLImageElement) => img.naturalWidth))
    .toBeGreaterThan(0);
  await page.screenshot({ path: path.join(SHOTS, "t5_galerie.png") });
  await item.locator(".home-name").dblclick();
  const rename = page.getByRole("textbox", { name: /Nouveau nom de/ });
  const fresh = `Grenier de Léa ${Date.now() % 100000}`;
  await rename.fill(fresh);
  await rename.press("Enter");
  await expect(page.locator(".boards-cards")).toContainText(fresh);
  const after = await project(page, id);
  expect(after.name).toBe(fresh);
  expect(after.name_auto).toBe(false);
  await page.locator(".home-name", { hasText: fresh }).click();
  await expect(page.locator(".topbar .project-name")).toHaveText(fresh);
  await page.getByRole("button", { name: "Projets" }).click();
  page.once("dialog", (d) => void d.accept());
  await page.getByRole("button", { name: `Supprimer « ${fresh} »` }).click();
  await expect(page.locator(".boards-cards")).not.toContainText(fresh);
});

test("tableau : échec de la construction → message clair, photo et consigne gardées", async ({
  page,
}) => {
  test.skip(!existsSync(PHOTO), "fixture image.png absente");
  await page.route("**/autoplan*", (route) =>
    route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "construction impossible (essai)" }),
    }),
  );
  const card = await newBoard(page);
  const id = idOf(page);
  await card.getByLabel("Vos envies et contraintes").fill(CONSIGNE);
  await card.getByLabel("Vos envies et contraintes").blur();
  await kept(page, id, (p) => p.pending_instruction === CONSIGNE);
  const board = page.getByRole("region", { name: "Espace de travail" });
  await dropInto(
    page,
    board.locator(".board-item.kind-frame.role-photos"),
    PHOTO,
    "image/png",
  );
  await expect(page.getByRole("alert")).toContainText(
    "construction impossible",
    {
      timeout: 60_000,
    },
  );
  // Rien n'est perdu : la photo est dans le projet, la consigne attend, le plan reste vide.
  const p = await project(page, id);
  expect(Object.keys(p.images)).toHaveLength(1);
  expect(p.pending_instruction).toBe(CONSIGNE);
  await expect(
    board
      .locator(".board-item.kind-plan")
      .getByRole("button", { name: "Générer le plan" }),
  ).toBeVisible();
});

test("à l'ouverture : un nom tapé à la main reste, même s'il ressemble à un fichier", async ({
  page,
}) => {
  const name = "WhatsApp Image 2026 08 25 at 13.36.24";
  const created = (await (
    await page.request.post("/api/projects", { data: { name } })
  ).json()) as { id: string };
  await gotoApp(page, `/#/projects/${created.id}`);
  await expect(page.locator(".topbar .project-name")).toHaveText(name);
  expect((await project(page, created.id)).name_auto).toBe(false);
});

test("L1b sur le tableau : une pièce et sa surface → plan de départ simplifié", async ({
  page,
}) => {
  const card = await newBoard(page);
  const id = idOf(page);
  await card
    .getByLabel("Vos envies et contraintes")
    .fill("Une chambre de 60 m² lumineuse, bois clair");
  await card.getByLabel("Vos envies et contraintes").blur();
  await kept(page, id, (p) => (p.pending_instruction ?? "").includes("60 m²"));
  const board = page.getByRole("region", { name: "Espace de travail" });
  await board
    .locator(".board-item.kind-plan")
    .getByRole("button", { name: "Générer le plan" })
    .click();
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({
    timeout: 60_000,
  });
  await goSpace(page, "plan");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(
    page.getByRole("status").filter({ hasText: "Plan de départ simplifié" }),
  ).toContainText("60 m²");
});

test("L3 sur le tableau : un logement décrit → plan généré, pièces nommées, statut honnête", async ({
  page,
}) => {
  const card = await newBoard(page);
  const id = idOf(page);
  await card.getByLabel("Vos envies et contraintes").fill(T3);
  await card.getByLabel("Vos envies et contraintes").blur();
  await kept(page, id, (p) => p.pending_instruction === T3);
  await page
    .getByRole("region", { name: "Espace de travail" })
    .locator(".board-item.kind-plan")
    .getByRole("button", { name: "Générer le plan" })
    .click();
  await expect(
    page
      .getByRole("region", { name: "Espace de travail" })
      .locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({ timeout: 60_000 });
  await goSpace(page, "plan");
  const banner = page.getByRole("status", { name: "Plan généré" });
  await expect(banner).toContainText("ce n'est pas un relevé");
  const labels = page.locator("text.room-label");
  await expect(labels.filter({ hasText: "Chambre 1" })).toHaveCount(1);
  await expect(labels.filter({ hasText: "Chambre 2" })).toHaveCount(1);
  await expect(labels.filter({ hasText: "Séjour et cuisine" })).toHaveCount(1);
  await expect(
    page.getByRole("status", { name: "Statut du plan" }),
  ).toContainText("Proposé — à ajuster");
  const p = await project(page, id);
  const rooms = Object.values(Object.values(p.levels)[0]!.elements).filter(
    (e) => e.type === "room",
  );
  expect(rooms.length).toBeGreaterThanOrEqual(7);
});

test("L3 sur le tableau : logement décrit → chemin automatique → vues réparties et respect de la demande", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await withAutoPath(page);
  const card = await newBoard(page);
  const id = idOf(page);
  await card.getByLabel("Vos envies et contraintes").fill(T3);
  await card.getByLabel("Vos envies et contraintes").blur();
  await kept(page, id, (p) => p.pending_instruction === T3);
  await page
    .getByRole("group", { name: "Déclencheur" })
    .getByRole("button", { name: "Générer maintenant" })
    .click();
  // T7d1 : l'arrivée cadre le cadre Rendus ; « Vos rendus » est dans le volet Rendus.
  await expect(
    page.locator(".board-item.kind-render:not(.role-inspiration)").first(),
  ).toBeVisible({ timeout: 280_000 });
  await openRendersPane(page);
  // Les cartes arrivent vue par vue ; « Vos rendus » paraît quand le chemin finit.
  const check = page.getByRole("region", { name: "Respect de votre demande" });
  await expect(check).toBeVisible({ timeout: 280_000 });
  await expect(check.locator("li", { hasText: "Chambres" })).toHaveClass(
    /req-ok/,
  );
  await expect(check.locator("li", { hasText: "Chambres" })).toContainText(
    "2 chambres, un lit dans chacune",
  );
  await expect(check.locator("li", { hasText: "coin bureau" })).toHaveClass(
    /req-ok/,
  );
  await expect(page.getByLabel("Vos rendus")).toContainText("Chambre 1");
});

test("L2 sur le tableau : questions guidées et plan DXF → Vos rendus avec le respect de la demande", async ({
  page,
}) => {
  test.skip(!existsSync(PLAN_DXF), "fixture plan_rect.dxf absente");
  test.setTimeout(300_000);
  await withAutoPath(page);
  const card = await newBoard(page);
  const id = idOf(page);
  await card.getByRole("tab", { name: "Questions guidées" }).click();
  await card.getByLabel("Chambres").fill("2");
  await card.getByLabel("Style").selectOption("industriel");
  await card.getByText("Plus d'options").click();
  await card.getByLabel("Format des images").selectOption("4:5");
  await card
    .getByRole("group", { name: "À éviter" })
    .getByRole("button", { name: "tapis" })
    .click();
  await card.getByRole("tab", { name: "Prompt libre" }).click();
  await card.getByLabel("Vos envies et contraintes").fill("avec une cheminée");
  await card.getByLabel("Vos envies et contraintes").blur();
  await expect(card).toContainText(/compris : .*2 chambres.*industriel/);
  await kept(
    page,
    id,
    (p) =>
      p.pending_instruction === "avec une cheminée" &&
      (p.pending_answers as { style?: string; aspect?: string } | null)
        ?.aspect === "4:5",
  );
  // Le plan DXF par « Importer › Photo ou plan de la pièce » (T7c, barre de gauche) : le
  // chemin automatique enchaîne.
  await page
    .getByRole("toolbar", { name: "Outils de l'espace de travail" })
    .getByRole("button", { name: "Importer" })
    .click();
  await page
    .getByRole("menuitem", { name: /Photo ou plan de la pièce/ })
    .click();
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expect(
    page.locator(".board-item.kind-render:not(.role-inspiration)").first(),
  ).toBeVisible({ timeout: 280_000 });
  await openRendersPane(page);
  await expect(page.getByLabel("Vos rendus")).toBeVisible({ timeout: 280_000 });
  const check = page.getByRole("region", { name: "Respect de votre demande" });
  await expect(check).toBeVisible();
  await expect(check.locator("li.req-ok", { hasText: "Style" })).toContainText(
    "Industriel",
  );
  await expect(check.locator("li.req-ok", { hasText: "Format" })).toContainText(
    "portrait 4:5",
  );
  await expect(check.locator("li", { hasText: "À éviter" })).toHaveClass(
    /req-ok/,
  );
  await expect(
    check.locator("li.req-missing", { hasText: "cheminée" }),
  ).toContainText("pas au catalogue");
});

test("L2 : case Gemini (galerie et note Exigences) → aperçu par Gemini, mention qui suit", async ({
  page,
}) => {
  // Le serveur de test n'a pas de clé : la page reçoit un modèle distant simulé.
  let checked = false;
  await page.route("**/api/home/settings", async (route) => {
    if (route.request().method() === "PUT")
      checked = (route.request().postDataJSON() as { gemini_text: boolean })
        .gemini_text;
    await route.fulfill({
      json: {
        gemini_text: checked,
        remote_model: true,
        destination: "generativelanguage.googleapis.com",
      },
    });
  });
  const asked: boolean[] = [];
  await page.route("**/api/instruction/preview", async (route) => {
    const body = route.request().postDataJSON() as { use_model: boolean };
    asked.push(body.use_model);
    await route.fulfill({
      json: {
        translation: {
          fields: { style: "chalet" },
          understood: ["style : chalet"],
          summary: ["chalet"],
          notes: [],
          dropped: [],
          by: "phrases",
        },
        understanding: body.use_model ? "model" : "phrases",
        notice: "",
      },
    });
  });
  // La galerie : la case dans les Réglages, la mention du pied suit.
  await gotoApp(page, "/");
  const foot = page.locator(".boards-foot");
  await expect(foot).toContainText(
    "vos photos et plans restent sur cette machine ; rien ne part",
  );
  await page.getByRole("button", { name: "Réglages" }).click();
  const box = page
    .getByRole("dialog", { name: "Réglages" })
    .getByRole("checkbox", { name: /Mieux comprendre mon texte avec Gemini/ });
  await expect(box).not.toBeChecked();
  await box.check();
  await expect(box).toBeChecked();
  await expect(foot).toContainText(
    "votre texte part chez Google (Gemini), jamais les photos",
  );
  await expect(foot).not.toContainText("rien ne part");
  // La note Exigences : même case, cochée ; le texte part au modèle, jamais une photo.
  const card = await newBoard(page);
  const inCard = card.getByRole("checkbox", {
    name: /Mieux comprendre mon texte avec Gemini/,
  });
  await expect(inCard).toBeChecked();
  // KAN-64 (décision PO) : « (texte seul) » dans le libellé visible, « jamais les photos »
  // dans l'infobulle de la case.
  await expect(card).toContainText("(texte seul)");
  await expect(card.locator(".board-composer-gemini")).toHaveAttribute(
    "title",
    /jamais les photos/,
  );
  await card
    .getByLabel("Vos envies et contraintes")
    .fill("chambre chalet bois");
  await expect(card).toContainText("chalet", { timeout: 10_000 });
  await expect.poll(() => asked.includes(true), { timeout: 10_000 }).toBe(true);
});

test("T6 : en ligne, la mention dit le serveur PlanDesign et les rapports d'erreur", async ({
  page,
}) => {
  // /api/health simulé comme sur app.plandesign.fr (API exposée, suivi des erreurs actif).
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({
      json: { ...body, exposed: true, error_reports: true },
    });
  });
  await gotoApp(page, "/");
  const foot = page.locator(".boards-foot");
  await expect(foot).toContainText(
    "vos photos et plans sont gardés sur le serveur PlanDesign ; rien n'est envoyé ailleurs",
  );
  await expect(foot).toContainText("un plantage envoie un rapport technique");
  await expect(foot).not.toContainText("cette machine");
});
