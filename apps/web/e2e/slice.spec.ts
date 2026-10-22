import { expect, test, type Page } from "@playwright/test";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
} from "node:fs";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  openRendersPane,
  showPanel,
  waitNoHeavyJob,
} from "./helpers";
import { gotoApp } from "./app-ready";

// T7d1 : isolation des e2e lourds : aucun test ne commence tant que le serveur calcule
// encore pour le précédent (même s'il a échoué).
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** Scénario de la démo (docs/STATUS.md) joué dans un vrai navigateur. */

const SHOTS = path.resolve("e2e/screenshots");
const FIXTURE = path.resolve("../../tests/fixtures/rect_room/image.png");

test.beforeAll(() => {
  // Seules les captures des scénarios sont renouvelées : le dossier responsive/ (audit) reste.
  mkdirSync(SHOTS, { recursive: true });
  for (const f of readdirSync(SHOTS))
    if (f.endsWith(".png")) rmSync(path.join(SHOTS, f), { force: true });
});

async function createProject(page: Page, name: string): Promise<void> {
  await gotoApp(page, "/");
  await expect(
    page.getByRole("heading", {
      name: "Vos espaces de travail",
    }),
  ).toBeVisible();
  await createEmptyProject(page, name);
  // Un projet neuf s'ouvre sur « Ajouter une source » ; on passe au plan.
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}\/import$/);
  await expect(
    page.getByRole("heading", { name: "Ajouter une source" }),
  ).toBeVisible();
  // « Prendre une photo » n'existe que sur un téléphone (décision PO du 29 septembre) ;
  // l'entrée reste câblée sur l'appareil.
  await expect(
    page.getByRole("button", { name: "Prendre une photo" }),
  ).toBeHidden();
  await expect(page.locator('input[capture="environment"]')).toHaveAttribute(
    "accept",
    /image/,
  );
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}$/);
  await expect(page.locator("svg.plan")).toBeVisible();
  // T7d1 : ces parcours lisent l'inspecteur ; le panneau est fermé par défaut.
  await showPanel(page);
}

const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PLAN_PDF = path.resolve("../../tests/fixtures/plan_rect.pdf");

/** Curseur d'historique du projet ouvert (lu par l'API) : un geste enregistré l'augmente. */
async function historyCursor(page: Page): Promise<number> {
  const id = /projects\/([0-9a-f-]{36})/.exec(page.url())?.[1];
  const r = await page.request.get(`/api/projects/${id}`);
  return ((await r.json()) as { history_cursor: number }).history_cursor;
}

/** Lot 2 responsive : nom de pièce, surface, étiquettes de meubles et cotes ne se
 *  recouvrent jamais. */
async function labelsDisjoint(page: Page): Promise<void> {
  const boxes: {
    name: string;
    x: number;
    y: number;
    width: number;
    height: number;
  }[] = [];
  for (const sel of [
    "text.room-label",
    "text.room-area",
    "text.furniture-label",
    "text.dim",
  ]) {
    for (const el of await page.locator(sel).all()) {
      const b = await el.boundingBox();
      if (b) boxes.push({ name: sel, ...b });
    }
  }
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i]!;
      const b = boxes[j]!;
      const overlap =
        a.x < b.x + b.width &&
        b.x < a.x + a.width &&
        a.y < b.y + b.height &&
        b.y < a.y + a.height;
      expect(overlap, `${a.name} / ${b.name} se recouvrent`).toBe(false);
    }
}

async function drawRectangle(page: Page): Promise<void> {
  const svg = page.locator("svg.plan");
  await page.keyboard.press("w");
  const corners: [number, number][] = [
    [300, 600],
    [700, 600],
    [700, 350],
    [300, 350],
  ];
  for (const [x, y] of corners) await svg.click({ position: { x, y } });
  await svg.click({ position: { x: 300, y: 600 } }); // ferme sur le premier sommet (aimantation)
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(page.locator("circle.vertex")).toHaveCount(4);
}

test("tracer, détecter la pièce, mesurer, calibrer, annuler, vue 3D", async ({
  page,
}) => {
  await createProject(page, "E2E chambre");
  await drawRectangle(page);

  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  await expect(page.locator("text.room-area")).toContainText("m²");

  // R2 : la fiche du niveau dit sa sorte et le type de chaque pièce, modifiables d'un clic ;
  // le nom par défaut suit la sorte choisie.
  await page.getByLabel("Sorte de niveau").selectOption("attic");
  await expect(
    page.getByRole("heading", { name: "Niveau : Grenier" }),
  ).toBeVisible();
  await page.getByLabel(/Type de pièce/).selectOption("bedroom");
  await expect(page.getByLabel(/Type de pièce/)).toHaveValue("bedroom");

  // Parcours d'un non-expert (E1) : tout passe par la palette et l'inspecteur contextuel ;
  // aucune analyse, aucun réglage expert n'est visible sans ouvrir Paramètres avancés.
  await expect(page.getByRole("heading", { name: "Assistant IA" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("heading", { name: "Calibration" })).toHaveCount(
    0,
  );

  // Bibliothèque de meubles (F3) : cartes (vignette, nom, pièce), filtre par pièce et
  // recherche ; choisir une carte puis cliquer dans le plan pose le meuble ; Échap annule.
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await expect(
    page.getByText("Choisissez un meuble, puis cliquez sur le plan."),
  ).toBeVisible();
  const card = (id: string) => page.locator(`button.card[data-asset="${id}"]`);
  await page
    .getByRole("group", { name: "Pièce" })
    .getByRole("button", { name: "Cuisine" })
    .click();
  await expect(card("fridge")).toBeVisible();
  await expect(card("desk_140")).toHaveCount(0);
  await page
    .getByRole("group", { name: "Pièce" })
    .getByRole("button", { name: "Toutes" })
    .click();
  await page.getByLabel("Rechercher un meuble").fill("bureau 140");
  await expect(card("desk_140")).toBeVisible();
  await expect(card("desk_140").locator("img")).toHaveJSProperty(
    "complete",
    true,
  );
  await card("desk_140").click();
  await expect(card("desk_140")).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText(/Cliquez sur le plan pour poser « Bureau 140 × 70 »/),
  ).toBeVisible();
  const planBox = (await page.locator("svg.plan").boundingBox())!;
  await page.mouse.move(planBox.x + 500, planBox.y + 475);
  await expect(page.getByTestId("placing-ghost")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("placing-ghost")).toHaveCount(0);
  await expect(card("desk_140")).toHaveAttribute("aria-pressed", "false");
  // Dans le mur : le fantôme rougit et le clic ne pose rien.
  await card("desk_140").click();
  await page.mouse.move(planBox.x + 500, planBox.y + 350);
  await expect(page.locator("polygon.furniture.placing.invalid")).toHaveCount(
    1,
  );
  await page.mouse.click(planBox.x + 500, planBox.y + 350);
  await expect(page.getByText(/Non posé :/)).toBeVisible();
  await page.mouse.move(planBox.x + 500, planBox.y + 475);
  await expect(page.locator("polygon.furniture.placing.invalid")).toHaveCount(
    0,
  );
  await page.mouse.click(planBox.x + 500, planBox.y + 475);
  await expect(page.getByTestId("placing-ghost")).toHaveCount(0);
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  // F4 : le meuble posé porte le symbole de sa famille (écran + traits), non cliquable.
  await expect(
    page.locator("g.furniture-symbols .furniture-symbol"),
  ).toHaveCount(1);
  // Le meuble est posé au centre, là où s'écrit le nom de la pièce : les étiquettes
  // s'écartent au lieu de se superposer (lot 2 responsive).
  await labelsDisjoint(page);
  await page.keyboard.press("v");
  await page.locator("polygon.furniture").click();
  await expect(
    page.getByRole("heading", { name: "Meuble : Bureau 140 × 70" }),
  ).toBeVisible();
  // Case contrôlée par le serveur : l'état change après l'aller-retour, pas au clic.
  await page.getByLabel("À conserver").click();
  await expect(page.locator("polygon.furniture.preserve")).toHaveCount(1);
  await expect(page.getByLabel("À conserver")).toBeChecked();
  await page.getByRole("button", { name: "Supprimer", exact: true }).click();
  await expect(page.locator("polygon.furniture")).toHaveCount(0);

  // Fiche « Aménager » en trois pas (E1). 1 (P5c) : la demande en phrase passe par
  // l'assistant ; ici, plus de champ libre, le brief par zones ou à la main.
  await page.getByRole("button", { name: "Aménager", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: /1\s*Votre demande/ }),
  ).toBeVisible();
  await expect(page.getByPlaceholder(/chambre d'amis/)).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Écrire à l'assistant" }),
  ).toBeVisible();

  // 2 et 3 (3.4), sans modèle : brief composé à la main → variantes avec score et
  // phrase, fantôme sur le plan, rien dans la scène avant « Appliquer », puis annulable.
  await page.getByRole("button", { name: "+ ajouter un meuble" }).click();
  await expect(
    page.getByRole("heading", { name: /2\s*Le brief, composé à la main/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Proposer des dispositions" }).click();
  await expect(
    page.getByRole("heading", { name: /3\s*Les dispositions/ }),
  ).toBeVisible();
  await expect(page.getByText(/^Variante 1/)).toBeVisible();
  await expect(
    page
      .getByText(/(Très bien|Correct|À revoir) · \d+ meubles? placés?/)
      .first(),
  ).toBeVisible();
  await expect(
    page.locator(".variants > li").first().locator(".variant-summary"),
  ).toHaveCount(1);
  await expect(
    page
      .locator(".variants > li")
      .first()
      .getByRole("button", { name: "Appliquer cette variante" }),
  ).toHaveClass(/primary/);
  // 3.7 : vignette Blender d'une variante (si Blender est là) : elle apparaît sous la
  // variante, sans rien appliquer ; sinon la fiche le dit.
  const blenderInfo = (await (
    await page.request.get("/api/blender")
  ).json()) as {
    available: boolean;
  };
  if (blenderInfo.available) {
    await page.getByRole("button", { name: "Vignette 3D" }).first().click();
    await expect(page.locator(".variant-thumb img").first()).toBeVisible({
      timeout: 180_000,
    });
    await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(0);
  } else {
    await expect(page.getByText(/Sans Blender, pas de vignette/)).toBeVisible();
  }
  // 3.5 : « Avis sur cette variante » sans modèle → remarques des règles locales (chacune
  // cite un meuble et une règle) ; rien n'entre dans la scène.
  await page
    .getByRole("button", { name: "Avis sur cette variante" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: /Remarques \(règles locales\)/ }),
  ).toBeVisible();
  await expect(page.locator(".remarks")).toContainText("par les règles seules");
  await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(0);
  await expect(page.locator("polygon.furniture.ghost")).toHaveCount(1);
  await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(0);
  // 3.6 : comparaison côte à côte (la seconde variante en vert) quand il y a plusieurs
  // variantes.
  const compareButtons = page.getByRole("button", {
    name: "comparer",
    exact: true,
  });
  if ((await compareButtons.count()) > 0) {
    await compareButtons.first().click();
    await expect(page.locator(".compare table")).toBeVisible();
    await expect(page.locator("polygon.furniture.ghost.alt")).toHaveCount(1);
    await page
      .getByRole("button", { name: "ne plus comparer" })
      .first()
      .click();
    await expect(page.locator("polygon.furniture.ghost.alt")).toHaveCount(0);
  }
  await page
    .getByRole("button", { name: "Appliquer cette variante" })
    .first()
    .click();
  await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(1);
  await expect(page.locator("polygon.furniture.ghost")).toHaveCount(0);
  // 3.6 : glisser le meuble vers le centre du plan (Alt : sans accrochage au mur). Il bouge
  // si la position est valide ; sinon le plan dit « Non posé : … » et le meuble reste.
  const piece = page.locator("polygon.furniture:not(.ghost)").first();
  const cursorBefore = await historyCursor(page);
  const box = await piece.boundingBox();
  const plan = await page.locator("svg.plan").boundingBox();
  expect(box && plan).toBeTruthy();
  if (box && plan) {
    const from = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    const to = {
      x: from.x + Math.sign(plan.x + plan.width / 2 - from.x || 1) * 25,
      y: from.y + Math.sign(plan.y + plan.height / 2 - from.y || 1) * 25,
    };
    await page.keyboard.down("Alt");
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 6 });
    await page.mouse.up();
    await page.keyboard.up("Alt");
  }
  // Le dépôt est vérifié côté serveur puis enregistré : on attend l'un ou l'autre
  // (« Non posé » affiché, ou une entrée d'historique de plus), jamais l'aperçu du glisser.
  await expect
    .poll(
      async () =>
        (await page.locator("text.hud.notice").count()) > 0 ||
        (await historyCursor(page)) > cursorBefore,
    )
    .toBe(true);
  const moved = (await historyCursor(page)) > cursorBefore;
  if (moved) await page.getByRole("button", { name: /Annuler/ }).click();
  await page.getByRole("button", { name: /Annuler/ }).click();
  await expect(page.locator("polygon.furniture")).toHaveCount(0);
  await page.getByRole("button", { name: "Fermer Aménager" }).click();

  // Sélection du premier mur, saisie de sa longueur réelle, « Calibrer le plan » dans la
  // fiche du mur (le détail — facteur, prévisualisation — est dans Paramètres avancés).
  await page.keyboard.press("v");
  await page
    .locator("polygon.wall")
    .first()
    .click({ position: { x: 200, y: 4 } });
  await expect(page.getByRole("heading", { name: "Mur" })).toBeVisible();
  await page.getByPlaceholder(/longueur réelle/).fill("3,10");
  await page.getByRole("button", { name: "Ajouter", exact: true }).click();
  await expect(page.getByText(/Ce mur a une mesure/)).toBeVisible();
  await page.getByRole("button", { name: "Calibrer le plan" }).click();
  // Le mur mesuré et son opposé (même longueur tracée) affichent tous deux 3,10 m.
  await expect(page.locator("text.dim", { hasText: "3,10 m" })).toHaveCount(2);
  await expect(page.locator("text.dim", { hasText: "≈" })).toHaveCount(
    0,
    "murs saisis : pas de ≈",
  );
  await expect(
    page.locator(".badge", { hasText: "mesuré" }).first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Paramètres avancés", exact: true })
    .click();
  // A4 : quatre groupes lisibles (réglages, assistant IA, installation, diagnostic) ; le
  // détail de la calibration est dans « Outils experts », replié et en dernier.
  await expect(
    page.getByRole("heading", { name: "Assistant IA" }),
  ).toBeVisible();
  await expect(page.getByLabel("Unités")).toHaveValue("m");
  await expect(page.getByLabel("Nord")).toBeVisible();
  await expect(page.locator("details.doctor")).toBeVisible();
  const experts = page.locator("details.experts");
  await expect(experts).not.toHaveAttribute("open", "");
  await expect(page.getByText(/Facteur ×/)).not.toBeVisible();
  await experts.locator("> summary").click();
  await expect(page.getByText(/Facteur ×/)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "01-plan-calibre.png") });
  await page.getByRole("button", { name: "Fermer Paramètres avancés" }).click();
  await expect(page.getByRole("heading", { name: "Mur" })).toBeVisible();
  // Export PDF : le lien du menu « Exporter » répond un vrai PDF.
  await page.getByRole("button", { name: "Exporter" }).click();
  const pdfHref = await page
    .getByRole("link", { name: "PDF" })
    .getAttribute("href");
  const pdf = await page.request.get(pdfHref ?? "");
  expect(pdf.headers()["content-type"]).toBe("application/pdf");
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
  const dxfHref = await page
    .getByRole("link", { name: "DXF" })
    .getAttribute("href");
  const dxf = await page.request.get(dxfHref ?? "");
  expect(dxf.headers()["content-type"]).toContain("application/dxf");
  expect(await dxf.text()).toContain("MURS");

  // Annuler la calibration : le mur redevient « saisi ».
  await page.getByRole("button", { name: /Annuler/ }).click();
  await expect(
    page.locator(".badge", { hasText: "saisi" }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: /Rétablir/ }).click();
  await expect(
    page.locator(".badge", { hasText: "mesuré" }).first(),
  ).toBeVisible();

  // Vue Plan + 3D : le viewer Three.js se charge sans bandeau d'erreur.
  await page.getByRole("button", { name: "Plan + 3D" }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await expect(page.locator(".pane-3d")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Plafonds", exact: true }),
  ).toHaveAttribute("aria-pressed", "false");
  await page.waitForTimeout(1500);
  await expect(page.locator(".error-banner")).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "02-plan-et-3d.png") });
  await page.getByRole("button", { name: "Plafonds", exact: true }).click();
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(SHOTS, "02b-plan-et-3d-plafonds.png"),
  });
});

test("première photo d'un projet neuf (tableau) : plan construit sans autre action", async ({
  page,
}) => {
  // R1 (3 octobre 2026) : la photo déposée sur l'accueil était importée sans lancer la
  // construction ; le plan n'arrivait qu'avec une seconde photo.
  test.skip(!existsSync(FIXTURE), "fixture image.png absente");
  // T5 : « Nouveau projet » ouvre le tableau ; la photo importée sur le tableau construit le
  // plan (bascule « générer au dépôt d'une photo », activée par défaut).
  await gotoApp(page, "/");
  await page.getByRole("button", { name: "Nouveau projet" }).click();
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}$/, {
    timeout: 30_000,
  });
  await expectSpace(page, "board", { timeout: 30_000 });
  // KAN-77 : plus de menu « Importer » dans la barre de gauche ; la photo est déposée sur
  // l'espace de travail (le glisser-déposer reste).
  const photo = await page.evaluateHandle(
    (bytes) => {
      const dt = new DataTransfer();
      dt.items.add(
        new File([new Uint8Array(bytes)], "image.png", { type: "image/png" }),
      );
      return dt;
    },
    [...readFileSync(FIXTURE)],
  );
  // KAN-91 (C1) : une photo ne construit le plan que déposée dans la carte « Photos de la
  // pièce » reliée à ce plan ; posée librement, elle n'est jamais utilisée.
  const space = page.getByRole("region", { name: "Espace de travail" });
  const photos = space.locator(".board-item.kind-frame.role-photos");
  const box = (await photos.boundingBox())!;
  await space.locator(".board-stage").dispatchEvent("drop", {
    dataTransfer: photo,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
  const id = /projects\/([0-9a-f-]{36})/.exec(page.url())?.[1];
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/projects/${id}`);
        const p = (await r.json()) as {
          levels: Record<
            string,
            { elements: Record<string, { type: string }> }
          >;
        };
        return Object.values(p.levels).flatMap((l) =>
          Object.values(l.elements).filter((e) => e.type === "wall"),
        ).length;
      },
      { timeout: 180_000 },
    )
    .toBe(4);
  // Le plan arrivé (sans rendu, chemin coupé), l'atelier s'ouvre sur l'éditeur (B3) ; le
  // plan est dessiné dans son espace.
  await expectSpace(page, "board");
  await goSpace(page, "plan");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  const r = await page.request.get(`/api/projects/${id}`);
  expect(
    Object.keys(((await r.json()) as { images: object }).images),
  ).toHaveLength(1);
});

test("photo : plan construit tout seul, plan estimé, recalibrer, fond, vues, panneau IA", async ({
  page,
}) => {
  test.skip(!existsSync(FIXTURE), "fixture image.png absente");
  await createProject(page, "E2E photo");

  // Directive d'augus du 29 septembre : la photo déposée construit le plan toute seule,
  // sans question ni bouton d'analyse ; à l'arrivée, un bandeau « plan estimé » et
  // « Recalibrer ». La bande de vignettes importe.
  await page.locator('input[type="file"]').setInputFiles(FIXTURE);
  await expect(page.locator(".strip-thumb")).toHaveCount(1);
  await expect(page.locator("polygon.wall")).toHaveCount(4, {
    timeout: 180_000,
  });
  await expect(page.locator("polygon.wall.uncertain")).toHaveCount(
    4,
    "tous estimés ou inférés",
  );
  await expect(page.locator("svg.plan circle.camera")).toHaveCount(1);
  const banner = page.locator(".auto-banner.estimated");
  await expect(banner).toContainText("Plan estimé");
  await expect(banner).not.toContainText(/RANSAC|@/);
  await expect(
    banner.getByRole("button", { name: "ce n'est pas une photo" }),
  ).toBeVisible();
  for (const name of [
    "Proposer un plan depuis la photo",
    "Plan depuis la profondeur",
    "Portes et fenêtres",
    "Profondeur",
    "Combles",
    "Créer les combles",
    "Vectoriser les traits épais",
  ])
    await expect(page.getByRole("button", { name, exact: true })).toHaveCount(
      0,
    );
  await expect(page.getByTitle(/Hauteur de l'appareil/)).toHaveCount(0);

  // « Recalibrer » (A4) : l'outil unique. Depuis le bandeau, la carte s'ouvre ; la hauteur
  // sous plafond suffit : tout le plan suit, le facteur est annoncé, le bandeau « plan
  // estimé » disparaît, et le geste s'annule.
  await banner.getByRole("button", { name: "Recalibrer" }).click();
  const card = page.getByRole("dialog", { name: "Recalibrer" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/Cliquez les deux bouts/)).toBeVisible();
  await card.getByLabel("Hauteur sous plafond").fill("3");
  await expect(card.getByText(/× \d,\d{3}/)).toBeVisible();
  await card.getByRole("button", { name: "Appliquer les dimensions" }).click();
  const done = page.locator(".auto-banner.done");
  await expect(done).toContainText(/Plan mis à l'échelle × \d,\d{3}/);
  await expect(done).toContainText("la hauteur sous plafond = 3,00 m");
  await expect(page.locator(".auto-banner.estimated")).toHaveCount(0);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await done.getByRole("button", { name: "annuler" }).click();
  await expect(done).toHaveCount(0);
  await expect(page.locator("polygon.wall")).toHaveCount(4);

  // R3 : longueur ET largeur redonnent les proportions de la pièce ; l'effet est annoncé
  // avant d'appliquer (« … → 8,00 × 4,20 m »), puis le geste s'annule.
  await page.getByRole("button", { name: "Recalibrer" }).first().click();
  const card2 = page.getByRole("dialog", { name: "Recalibrer" });
  await expect(card2.getByLabel("Longueur de la pièce")).toHaveAttribute(
    "placeholder",
    /^\d+,\d{2}$/,
  );
  await card2.getByLabel("Longueur de la pièce").fill("8");
  await card2.getByLabel("Largeur de la pièce").fill("4,2");
  await expect(card2.getByRole("status")).toContainText(/→ 8,00 × 4,20 m/);
  await card2.getByRole("button", { name: "Appliquer les dimensions" }).click();
  await expect(done).toContainText(/Plan recalibré : .* → 8,00 × 4,20 m/);
  await done.getByRole("button", { name: "annuler" }).click();
  await expect(done).toHaveCount(0);

  // Paramètres avancés › Photos : fond du plan, vues ; aucun bouton d'analyse ; le bloc
  // Diagnostic, replié, dit ce que la construction a fait.
  await page
    .getByRole("button", { name: "Paramètres avancés", exact: true })
    .click();
  // Les photos (fond, vues) sont dans « Outils experts », replié : on l'ouvre.
  await page.locator("details.experts > summary").click();
  const photos = page.locator(".photos");
  const photo = photos.locator(".photo").first();
  await expect(photo).toContainText("1280 × 960 px");
  await expect(photo).toContainText("EXIF retiré");
  const diagnostic = page.locator("details.diagnostic");
  await expect(diagnostic).not.toHaveAttribute("open", "");
  await diagnostic.locator("summary").click();
  await expect(diagnostic).toContainText(/Méthode gardée/);
  await expect(diagnostic).toContainText(/Lignes de fuite : fait/);

  await photo.getByRole("button", { name: "Mettre en fond du plan" }).click();
  await expect(page.locator("svg.plan g.photo image")).toHaveCount(1);
  await expect(
    photo.getByRole("button", { name: "Retirer du fond" }),
  ).toBeVisible();

  // Recalibrer par deux clics : longueur réelle, effet annoncé, alerte si la valeur est
  // aberrante, puis application : le fond ET les murs changent d'échelle ensemble.
  const scaleBefore = await page
    .locator("svg.plan g.photo")
    .getAttribute("transform");
  const wallBefore = await page
    .locator("polygon.wall")
    .first()
    .getAttribute("points");
  await page.getByRole("button", { name: "Recalibrer", exact: true }).click();
  await page.locator("svg.plan").click({ position: { x: 300, y: 500 } });
  await page.locator("svg.plan").click({ position: { x: 500, y: 500 } });
  const dialog = page.getByRole("dialog", { name: "Recalibrer" });
  await expect(dialog).toBeVisible();
  await dialog.getByPlaceholder(/distance réelle/).fill("40");
  await expect(dialog.getByText(/longueur inhabituelle/)).toBeVisible();
  await dialog.getByPlaceholder(/distance réelle/).fill("3");
  await expect(dialog.getByText(/sera multiplié par/)).toBeVisible();
  await expect(dialog.getByText(/longueur inhabituelle/)).toHaveCount(0);
  await dialog.getByRole("button", { name: "Appliquer", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator("svg.plan g.photo")).not.toHaveAttribute(
    "transform",
    scaleBefore ?? "",
  );
  await expect(page.locator("polygon.wall").first()).not.toHaveAttribute(
    "points",
    wallBefore ?? "",
  );
  await expect(page.locator(".auto-banner.done")).toContainText(
    "la distance cliquée = 3,00 m",
  );
  await page
    .locator(".auto-banner.done")
    .getByRole("button", { name: "fermer" })
    .click();

  // Une seconde photo (la même) est comparée au plan toute seule : « Photo 2 » dans le
  // plan, que l'on peut écarter (elle reste) puis réintégrer.
  await page.locator(".strip input[type=file]").setInputFiles(FIXTURE);
  await expect(page.locator("svg.plan circle.camera")).toHaveCount(2, {
    timeout: 180_000,
  });
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(photos).toContainText("Photo 2");
  await photos
    .getByRole("button", { name: "Écarter cette photo" })
    .last()
    .click();
  await expect(photos).toContainText("Photo 2 · écartée");
  await expect(page.locator("svg.plan circle.camera.excluded")).toHaveCount(1);
  await photos.getByRole("button", { name: "Réintégrer cette photo" }).click();
  await expect(page.locator("svg.plan circle.camera.excluded")).toHaveCount(0);
  // « Tourner la vue » (2.5c) : la caméra de la photo 2 change de position et de visée, les
  // murs ne bougent pas, et la vue est marquée comme orientée par l'utilisateur.
  const view2 = photos.locator(".view").last();
  const summaryBefore = await view2.textContent();
  await view2.getByRole("button", { name: "Tourner la vue" }).click();
  await expect(view2).toContainText("orientation choisie par vous");
  expect(await view2.textContent()).not.toBe(summaryBefore);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(page.locator("svg.plan circle.camera")).toHaveCount(2);
  // Navigation par photos : « Voir dans le plan » sélectionne la caméra et montre son champ
  // de vision ; « Vue suivante » passe à l'autre vue.
  await photos
    .getByRole("button", { name: "Voir dans le plan" })
    .last()
    .click();
  await expect(page.locator("svg.plan circle.camera.selected")).toHaveCount(1);
  await expect(page.locator("svg.plan polygon.camera-fov")).toHaveCount(1);
  await expect(page.getByText("vue 2 / 2")).toBeVisible();
  await page.getByRole("button", { name: "Vue suivante" }).click();
  await expect(page.getByText("vue 1 / 2")).toBeVisible();
  // Hors des Paramètres avancés, l'inspecteur montre la fiche de la caméra sélectionnée.
  await page.getByRole("button", { name: "Fermer Paramètres avancés" }).click();
  await expect(
    page.getByRole("heading", { name: "Vue « Photo »" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Recadrer" }).click();
  await page.screenshot({ path: path.join(SHOTS, "03-proposition-photo.png") });

  // Assistant IA (Paramètres avancés) : aucun modèle configuré pour ce serveur de test, et le
  // dit clairement.
  await page
    .getByRole("button", { name: "Paramètres avancés", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Assistant IA" }),
  ).toBeVisible();
  await expect(page.getByText(/Aucun modèle configuré/)).toBeVisible();

  await page.getByRole("button", { name: "Plan + 3D" }).click();
  await page.waitForTimeout(1500);
  await expect(page.locator(".error-banner")).toHaveCount(0);
  await page.screenshot({ path: path.join(SHOTS, "04-proposition-3d.png") });
});

test("photo avec les poids de profondeur : distances estimées, plafond plat, diagnostic", async ({
  page,
}) => {
  test.skip(!existsSync(FIXTURE), "fixture image.png absente");
  const models = (await (await page.request.get("/api/models")).json()) as {
    entry: { id: string };
    installed: boolean | null;
  }[];
  const depth = models.find((m) => m.entry.id.startsWith("depth_anything"));
  test.skip(
    !depth?.installed,
    "poids de profondeur absents (openplan models pull)",
  );
  await createProject(page, "E2E profondeur");
  await page.locator('input[type="file"]').setInputFiles(FIXTURE);
  // Progression lisible pendant le calcul (étape et durée annoncée), jamais un nom de modèle.
  const progress = page.locator(".auto-banner:not(.estimated)");
  if (await progress.isVisible()) {
    await expect(progress).toContainText(/Construction du plan/);
    await expect(progress).toContainText(/environ \d+ s/);
    await expect(progress).not.toContainText(/depth|RANSAC|@/i);
  }
  await expect(page.locator("polygon.wall")).toHaveCount(4, {
    timeout: 180_000,
  });
  await page
    .getByRole("button", { name: "Paramètres avancés", exact: true })
    .click();
  const diagnostic = page.locator("details.diagnostic");
  await diagnostic.locator("summary").click();
  await expect(diagnostic).toContainText(/Profondeur : fait/);
  // Pièce droite : aucune pente inventée, plafond plat.
  await expect(diagnostic).toContainText(/aucune pente : plafond plat/);
  await expect(diagnostic).toContainText(/en secours/);
  await page.getByRole("button", { name: "Recadrer" }).click();
  await page.screenshot({ path: path.join(SHOTS, "05-profondeur-plan.png") });
});

test("importer un plan DXF : murs observés tout de suite, correction des calques, bandeau", async ({
  page,
}) => {
  test.skip(!existsSync(PLAN_DXF), "fixture plan_rect.dxf absente");
  // Un rendu Blender y est attendu : 180 s, le GPU peut être partagé avec le 8000.
  test.setTimeout(180_000);
  await gotoApp(page, "/");
  await createEmptyProject(page, "E2E plan DXF");
  await expect(
    page.getByRole("heading", { name: "Ajouter une source" }),
  ).toBeVisible();
  // Le DXF de la fabrique : rectangle 4 × 3 m en double trait (murs de 15 cm) + une cote.
  // Les murs sont créés tout de suite avec les calques par défaut ; on arrive dans le plan.
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}$/);
  await expect(page.locator("text.room-label")).toHaveCount(1);
  await expectSpace(page, "plan");
  await showPanel(page);
  await page
    .locator("polygon.wall")
    .first()
    .click({ position: { x: 200, y: 4 } });
  await expect(
    page.locator(".badge", { hasText: "observé" }).first(),
  ).toBeVisible();
  // « Ajouter une source » rouvre l'écran d'import de ce projet : ce qui a été lu, et la correction
  // après coup (calques, échelle papier) dans un bloc replié.
  await page.getByRole("button", { name: "Ajouter une source" }).click();
  await expect(page).toHaveURL(/\/import$/);
  await expect(page.getByText(/Plan DXF lu/)).toBeVisible();
  await expect(page.getByText(/millimètres/)).toBeVisible();
  await expect(page.getByText(/4 mur\(s\) en double trait/)).toBeVisible();
  await expect(page.getByText(/4 murs dans le plan/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Créer les murs/ }),
  ).toHaveCount(0);
  const fix = page.locator("details.plan-fix");
  await expect(fix).not.toHaveAttribute("open", "");
  await fix.locator("summary").click();
  const cotes = page.getByLabel(/calque COTES/);
  await expect(cotes).not.toBeChecked();
  // Cocher le calque des cotes refait les murs (un trait de plus) ; décocher revient à 4.
  // Case contrôlée par le serveur : l'état change après l'aller-retour, pas au clic.
  await cotes.click();
  await expect(page.getByText(/5 murs dans le plan/)).toBeVisible();
  await page.getByLabel(/calque COTES/).click();
  await expect(page.getByText(/4 murs dans le plan/)).toBeVisible();
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  // T7d2 : le volet Rendus de l'espace de travail : points de vue, rendu, galerie.
  await openRendersPane(page);
  await expect(page.locator("canvas")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Points de vue" }),
  ).toBeVisible();
  await expect(page.getByText(/Aucun point de vue/)).toBeVisible();
  await page.getByRole("button", { name: "+ Ajouter un point de vue" }).click();
  const viewpoint = page.locator(".viewpoints li");
  await expect(viewpoint).toHaveCount(1);
  await expect(viewpoint).toContainText("Point de vue 1");
  // Sans Blender : vignette du viewer quand même, et le bouton Rendre le dit.
  await expect(viewpoint.locator(".thumb-3d img")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(/Périphérique|Blender n'est pas/)).toBeVisible();
  const blender = (await (await page.request.get("/api/blender")).json()) as {
    available: boolean;
  };
  if (blender.available) {
    // Rendu réaliste en tâche de fond : durée annoncée, progression, puis la galerie.
    await page.getByLabel("Qualité").selectOption("16");
    // Éclairage « soir » avec le nord à droite du plan : la phrase d'orientation le dit.
    await page.getByLabel("Éclairage").selectOption("soir");
    await page.getByLabel("Nord du plan").selectOption("0");
    await expect(page.getByText(/Le nord est à droite du plan/)).toBeVisible();
    await expect(page.getByText(/environ \d+ s/)).toBeVisible();
    await viewpoint.getByRole("button", { name: "Rendre" }).click();
    await expect(page.getByText(/Rendu en cours/)).toBeVisible();
    await expect(page.locator(".gallery2-card")).toHaveCount(1, {
      timeout: 180_000,
    });
    await expect(page.locator(".gallery2-card")).toContainText(
      "Point de vue 1",
    );
    await expect(page.locator(".gallery2-card")).toContainText("soir");
    await expect(page.locator(".gallery2-card img")).toBeVisible();
  } else {
    await expect(
      viewpoint.getByRole("button", { name: "Rendre" }),
    ).toBeDisabled();
  }
  await page.screenshot({ path: path.join(SHOTS, "07-plan-dxf-rendre.png") });
  // Le point de vue se place dans le plan : glisser le rond le déplace.
  await viewpoint.getByRole("button", { name: "placer dans le plan" }).click();
  await expect(page.locator("svg.plan circle.camera.free")).toHaveCount(1);
  await expect(page.locator("svg.plan .compass")).toHaveCount(1);
  await expect(
    page.getByRole("heading", { name: /Point de vue « Point de vue 1 »/ }),
  ).toBeVisible();
  const cam = page.locator("svg.plan circle.camera.free");
  const box = await cam.boundingBox();
  if (box) {
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(box.x + 120, box.y + 60, { steps: 5 });
    await page.mouse.up();
  }
  await expect
    .poll(async () => (await cam.boundingBox())?.x ?? 0)
    .toBeGreaterThan((box?.x ?? 0) + 60);
});

test("importer un plan PDF vectoriel : échelle papier lue, murs tout de suite, autre échelle", async ({
  page,
}) => {
  test.skip(!existsSync(PLAN_PDF), "fixture plan_rect.pdf absente");
  await gotoApp(page, "/");
  await createEmptyProject(page, "E2E plan PDF");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_PDF);
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Ajouter une source" }).click();
  await expect(page.getByText(/Plan PDF vectoriel lu/)).toBeVisible();
  await expect(
    page.getByText(/échelle 1:50 \(lue sur le plan\)/),
  ).toBeVisible();
  await expect(page.getByText(/emprise 4,00 × 3,35 m/)).toBeVisible();
  await page.locator("details.plan-fix summary").click();
  // Le trait fin (cote) n'est pas coché, le trait gras (murs) l'est.
  await expect(page.getByLabel(/calque trait 0\.5 pt/)).not.toBeChecked();
  await expect(page.getByLabel(/calque trait 3\.0 pt/)).toBeChecked();
  // Autre échelle papier : le même fichier est relu et les murs refaits, l'emprise double,
  // puis retour.
  await page.getByLabel("Échelle papier").selectOption("100");
  await expect(page.getByText(/emprise 8,00 × 6,7\d m/)).toBeVisible();
  await page.getByLabel("Échelle papier").selectOption("50");
  await expect(page.getByText(/emprise 4,00 × 3,35 m/)).toBeVisible();
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await expect(page.locator("text.room-label")).toHaveCount(1);
  await showPanel(page);
  await page
    .locator("polygon.wall")
    .first()
    .click({ position: { x: 200, y: 4 } });
  await expect(
    page.locator(".badge", { hasText: "observé" }).first(),
  ).toBeVisible();
});

test("assistant : phrases simples, gestes annulables, refus expliqués, dispositions", async ({
  page,
}) => {
  await createProject(page, "E2E assistant");
  await drawRectangle(page);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  // Le panneau Assistant (palette) : exemples cliquables sous le champ, sans modèle configuré.
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  // Un seul titre « Assistant » : celui de l'en-tête du volet (P5c).
  await expect(page.locator(".side-head h2")).toHaveText("Assistant");
  await expect(page.locator("aside.assistant h2")).toHaveCount(0);
  const examples = page.locator(".assistant .examples button");
  await expect(examples).toHaveCount(3);
  // Une phrase : le meuble est posé par le moteur, surligné, le message le dit, Annuler le retire.
  const input = page.getByLabel("Votre demande à l'assistant");
  await input.fill("ajoute un bureau contre le mur");
  await input.press("Enter");
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  await expect(page.locator("polygon.furniture.highlight")).toHaveCount(1);
  const last = () => page.locator(".assistant .bubble.assistant").last();
  await expect(last()).toContainText(/posé/);
  await expect(last().locator(".actions .status-done")).toHaveCount(1);
  const cursor = await historyCursor(page);
  await last().getByRole("button", { name: "Annuler" }).click();
  await expect(page.locator("polygon.furniture")).toHaveCount(0);
  await expect(last()).toContainText("annulé");
  expect(await historyCursor(page)).toBe(cursor - 1);
  // Non compris : dit, rien ne change. Introuvable : refus expliqué avec la voie manuelle.
  await input.fill("bonjour, il fait beau");
  await input.press("Enter");
  await expect(last()).toContainText(/pas compris/);
  await expect(page.locator("polygon.furniture")).toHaveCount(0);
  await examples.nth(1).click();
  await expect(last()).toContainText(/je ne trouve pas/);
  await expect(last().locator(".actions .status-refused")).toHaveCount(1);
  // Deux chevets, « enlève la table de chevet » : question « Lequel », rien ne change.
  await input.fill("il me faut deux tables de chevet");
  await input.press("Enter");
  await expect(page.locator("polygon.furniture")).toHaveCount(2);
  await input.fill("enlève la table de chevet");
  await input.press("Enter");
  await expect(last()).toContainText(/Lequel/);
  await expect(page.locator("polygon.furniture")).toHaveCount(2);
  // « de gauche » lève l'ambiguïté (B3) : le chevet posé par l'assistant est retiré.
  await input.fill("enlève la table de chevet de gauche");
  await input.press("Enter");
  await expect(last()).toContainText(/retiré/);
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  // Dispositions à la demande : rien dans la scène, la fiche Aménager les montre.
  await examples.nth(2).click();
  await expect(last()).toContainText(/disposition/);
  await expect(page.locator("polygon.furniture:not(.ghost)")).toHaveCount(1);
  await last().getByRole("button", { name: "voir les dispositions" }).click();
  await expect(
    page.getByRole("heading", { name: /3\s*Les dispositions/ }),
  ).toBeVisible();
  await expect(page.getByText(/^Variante 1/)).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "10-assistant.png") });
});

test("bibliothèque 1366 × 768 : cartes lisibles, vignettes chargées, fantôme sous le curseur", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await createProject(page, "E2E bibliothèque");
  await drawRectangle(page);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  const panel = page.locator("section.library");
  await expect(panel).toBeVisible();
  // Toutes les pièces du catalogue ont leur filtre ; 34 cartes au moins.
  for (const room of [
    "Séjour",
    "Chambre",
    "Cuisine",
    "Salle de bain",
    "Bureau",
  ])
    await expect(
      panel
        .getByRole("group", { name: "Pièce" })
        .getByRole("button", { name: room }),
    ).toBeVisible();
  expect(await panel.locator("button.card").count()).toBeGreaterThanOrEqual(34);
  // Pas de défilement horizontal dans le volet ; au moins deux cartes par rangée.
  const aside = page.locator("aside.inspector").filter({ has: panel });
  const overflow = await aside.evaluate(
    (el) => el.scrollWidth - el.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);
  const first = (await panel.locator("button.card").nth(0).boundingBox())!;
  const second = (await panel.locator("button.card").nth(1).boundingBox())!;
  expect(second.y).toBeCloseTo(first.y, 0);
  // Les vignettes visibles sont chargées (image servie par l'API, pas une icône cassée).
  const bed = panel.locator('button.card[data-asset="bed_double_160"]');
  await bed.scrollIntoViewIfNeeded();
  await expect
    .poll(() =>
      bed.locator("img").evaluate((img: HTMLImageElement) => img.naturalWidth),
    )
    .toBeGreaterThan(0);
  await bed.click();
  const planBox = (await page.locator("svg.plan").boundingBox())!;
  await page.mouse.move(planBox.x + 500, planBox.y + 475);
  await expect(page.getByTestId("placing-ghost")).toBeVisible();
  await panel.evaluate((el) => {
    for (let n: HTMLElement | null = el; n; n = n.parentElement)
      n.scrollTop = 0;
  });
  // Capture pour la revue : on laisse finir la transition des boutons de la palette.
  await page.waitForTimeout(300);
  await page.screenshot({
    path: path.join(SHOTS, "10-bibliotheque-1366x768.png"),
  });
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("placing-ghost")).toHaveCount(0);
  // D3 : dans le volet Rendus, la vue d'architecte se propose en un clic et rejoint les points de vue.
  await openRendersPane(page);
  await page.getByRole("button", { name: "Vue d'architecte" }).click();
  await expect(page.locator(".viewpoints")).toContainText("Vue d'architecte");
  await expect(
    page.getByRole("button", { name: "Vue d'architecte" }),
  ).toBeDisabled();
});

test("disposition 1366 × 768 : aucune zone flottante n'en chevauche une autre", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1366, height: 768 });
  await createProject(
    page,
    "WhatsApp Image 2026-08-27 at 12.34.56 — grenier de la maison de campagne",
  );
  await drawRectangle(page);
  const zones = [
    ".topbar",
    ".palette",
    ".viewbar-wrap",
    ".strip",
    ".bottombar",
  ];
  const boxes: Record<
    string,
    { x: number; y: number; width: number; height: number }
  > = {};
  for (const z of zones) {
    const box = await page.locator(z).boundingBox();
    expect(box, `${z} visible`).not.toBeNull();
    if (box) boxes[z] = box;
  }
  const overlap = (a: (typeof boxes)[string], b: (typeof boxes)[string]) =>
    a.x < b.x + b.width &&
    b.x < a.x + a.width &&
    a.y < b.y + b.height &&
    b.y < a.y + a.height;
  for (let i = 0; i < zones.length; i++)
    for (let j = i + 1; j < zones.length; j++)
      expect(
        overlap(boxes[zones[i]!]!, boxes[zones[j]!]!),
        `${zones[i]} / ${zones[j]}`,
      ).toBe(false);
  // Le titre long est tronqué avec son infobulle ; le bouton d'espace reste cliquable.
  await expect(page.locator(".topbar .project-name")).toHaveAttribute(
    "title",
    /grenier/,
  );
  await expect(
    page
      .getByRole("navigation", { name: "Espaces du projet" })
      .getByRole("button"),
  ).toBeVisible();
  // Recadrer place le plan hors des barres : les sommets sont tous dans la zone libre.
  await page.getByRole("button", { name: "Recadrer" }).click();
  const vertices = await page.locator("circle.vertex").all();
  for (const v of vertices) {
    const b = await v.boundingBox();
    expect(b).not.toBeNull();
    if (!b) continue;
    for (const z of zones)
      expect(overlap(b, boxes[z]!), `sommet sous ${z}`).toBe(false);
  }
  // La boussole est visible hors des barres et a une aiguille courte (jamais une droite
  // vers le coin de l'écran).
  const compass = await page.locator("svg.plan .compass").boundingBox();
  expect(compass).not.toBeNull();
  if (compass)
    for (const z of zones)
      expect(overlap(compass, boxes[z]!), `boussole sous ${z}`).toBe(false);
  const needle = page.locator("svg.plan .compass line");
  const x1 = Number(await needle.getAttribute("x1"));
  const x2 = Number(await needle.getAttribute("x2"));
  const y1 = Number(await needle.getAttribute("y1"));
  const y2 = Number(await needle.getAttribute("y2"));
  expect(Math.hypot(x2 - x1, y2 - y1)).toBeLessThan(20);
  await page.screenshot({ path: path.join(SHOTS, "08-1366x768.png") });
  // 4.10a : l'accueil dit l'état de l'installation, replié, avec la commande de chaque manque.
  await gotoApp(page, "/");
  // Lot 4 : dates françaises fixes (« 1er oct. 2026 à 10:56 »), quel que soit le navigateur.
  await expect(page.locator(".home-name").first()).toHaveAttribute(
    "title",
    /modifié le (1er|\d{1,2}) \S+ \d{4} à \d{2}:\d{2}/,
  );
  // L1a : l'état de l'installation est dans les Réglages de la colonne des projets.
  await page.getByRole("button", { name: "Réglages" }).click();
  const doctor = page.locator("details.doctor");
  await expect(doctor).toBeVisible();
  await doctor.locator("summary").click();
  await expect(doctor.getByText(/Python 3\./)).toBeVisible();
  await expect(doctor.getByText(/Dossier des projets/)).toBeVisible();
});
