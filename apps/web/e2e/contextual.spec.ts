import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, expectSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** A1 : rendu contextualisé depuis le tableau. Le prompt enrichi par le plan est montré et
 *  modifiable avant toute génération ; le plan choisi comme source renvoie vers une vue 3D ;
 *  la génération réelle (poids absents sur ce serveur) échoue proprement ; la carte d'une
 *  variante montre « proportions estimées », la source, l'horodatage et le prompt. Aucune
 *  requête vers l'extérieur. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const RUN = "00000000-0000-4000-8000-00000000c0a1";

test("rendu contextualisé : source, intention, prompt enrichi modifiable, échec propre, carte", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  const id = await createEmptyProject(page, "Contextualisé");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await page.request.post(`/api/projects/${id}/images`, {
    multipart: {
      file: {
        name: "photo.png",
        mimeType: "image/png",
        buffer: readFileSync(PHOTO),
      },
    },
  });
  await reloadApp(page);
  await expectSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });

  // Le plan comme source : pas un guide d'image, une vue 3D est proposée (aucune rendue ici).
  await board.locator(".board-item.kind-plan .board-plan-head").click();
  // KAN-64 : dans le panneau (zones visibles) ou en bas au centre (un volet ouvert).
  await page.getByRole("button", { name: "Rendu contextualisé…" }).click();
  const panel = page.getByRole("region", { name: "Rendu contextualisé" });
  await expect(panel).toContainText("Le plan 2D n'est pas un guide d'image");
  await expect(panel).toContainText("Aucune vue rendue");
  await expect(
    panel.getByRole("button", { name: "Construire le prompt" }),
  ).toBeDisabled();

  // A2, KAN-91 : « Référence » suit l'appartenance. « Dans Inspirations » (barre de
  // sélection) met la photo dans la carte Inspirations, reliée aux rendus : badge sur la carte.
  const photo = board.locator(".board-item.kind-photo").first();
  await photo.click();
  await expect(panel).not.toContainText("Photo · Photo");
  const bar = page.getByRole("toolbar", { name: "Actions de la sélection" });
  await bar.getByRole("button", { name: "Dans Inspirations", exact: true }).click();
  await expect(photo.locator(".board-reference")).toHaveText("Référence");
  await expect(
    bar.getByRole("button", { name: "Dans Inspirations", exact: true }),
  ).toHaveCount(0);

  // La photo comme source (A4 : sans vue 3D rendue, la photo telle quelle est la seule
  // source, et l'écran dit où rendre une vue) : intention, budget, prompt enrichi,
  // traduction française, jetons.
  // KAN-64 : dans le panneau (zones visibles) ou en bas au centre (un volet ouvert).
  await page.getByRole("button", { name: "Rendu contextualisé…" }).click();
  await expect(panel).toContainText("Cette photo telle quelle");
  await expect(panel).toContainText("Aucune vue 3D rendue");
  await expect(
    panel.getByRole("radio", { name: /Cette photo telle quelle/ }),
  ).toBeChecked();
  await expect(panel).not.toContainText("Photo · Photo");
  await expect(
    panel.getByRole("list", { name: "Références cochées" }),
  ).toContainText("Photo");
  await panel
    .getByLabel("Intention")
    .fill("scandinave, bois clair et lin, garder le lit");
  await panel.getByLabel("Budget").selectOption("haut_de_gamme");
  await panel.getByRole("button", { name: "Construire le prompt" }).click();
  const context = panel.getByRole("list", { name: "Ce que le plan apporte" });
  await expect(context).toContainText("Pièce");
  await expect(context).toContainText(
    "Contraintes : murs, portes et fenêtres restent en place",
  );
  await expect(panel).toContainText("phrases simples");
  await expect(panel).toContainText(/compris : .*scandinave/);
  await expect(
    panel.getByLabel("Traduction française du prompt"),
  ).toContainText("photographie d'intérieur réaliste");
  const prompt = panel.getByLabel("Prompt final");
  await expect(prompt).toHaveValue(/metres/);
  await expect(prompt).toHaveValue(/premium/);
  await expect(prompt).toHaveValue(/scandinavian/);
  await expect(
    panel.getByRole("button", { name: /Générer 3 variantes avec 1 référence/ }),
  ).toBeVisible();
  await expect(panel).toContainText(/Environ \d+ jetons sur 77/);
  await expect(panel).toContainText("profondeur seule");
  // Modifiable avant la génération ; trop long, le volet prévient (le modèle coupe à 77).
  const built = await prompt.inputValue();
  await prompt.fill(
    built + ", " + Array(40).fill("golden brass lamp").join(", "),
  );
  await expect(panel.getByRole("alert")).toContainText("Trop long");
  // Une modification à longueur égale : plus d'alerte (le prompt construit tient le budget).
  await prompt.fill(built.replace("scandinavian", "nordic"));
  await expect(prompt).toHaveValue(/nordic/);
  await expect(panel.getByRole("alert")).toHaveCount(0);
  await page.screenshot({
    path: path.resolve("e2e/screenshots/a1_prompt_enrichi.png"),
  });

  // Génération refusée (poids absents, simulé ici pour ne pas solliciter le GPU : sur cette
  // machine la chaîne tourne pour de vrai) : l'écran le dit, rien n'est simulé comme réussi.
  await page.route(`**/levels/*/contextual`, (route) =>
    route.fulfill({
      status: 409,
      json: {
        detail:
          "poids du modèle d'images absents (essai) : openplan models pull sd15",
      },
    }),
  );
  await panel.getByRole("button", { name: /Générer 3 variantes/ }).click();
  await expect(panel.getByRole("alert")).toBeVisible({ timeout: 30_000 });
  await expect(board.locator(".board-item.kind-contextual")).toHaveCount(0);

  // La carte d'une variante (métadonnées réelles simulées côté client pour ce test seul).
  const entry = {
    run_id: RUN,
    variant: "creative",
    image: "creative.png",
    prompt:
      "photorealistic interior photograph of a bedroom, scandinavian, light oak",
    intention: "scandinave, bois clair",
    budget: "standard",
    source: { kind: "photo", id: "x", label: "Photo 1" },
    context: ["Pièce « R1 »"],
    estimated: true,
    strength: 0.65,
    control_scale: 1.0,
    seed: 0,
    steps: 30,
    model_ids: ["fake"],
    references: ["Photo 1"],
    reference_scale: 0.6,
    duration_s: 1,
    created_at: "2026-10-14T15:00:00Z",
    label: "Rendu contextualisé · proportions estimées",
  };
  await page.route(`**/api/projects/${id}/contextual`, (route) =>
    route.fulfill({ json: [entry] }),
  );
  await page.route(`**/runs/${RUN}/contextual/creative.png`, (route) =>
    route.fulfill({ path: PHOTO, contentType: "image/png" }),
  );
  await page.route(`**/api/projects/${id}/board`, async (route) => {
    if (route.request().method() !== "GET") return route.continue();
    const real = await route.fetch();
    const json = (await real.json()) as { items: Record<string, unknown>[] };
    json.items.push({
      id: "00000000-0000-4000-8000-00000000f001",
      kind: "frame",
      x: 0,
      y: 1200,
      w: 640,
      h: 400,
      z: -1,
      locked: false,
      frame: null,
      ref: null,
      text: "Moodboard",
      role: "moodboard",
      url: null,
    });
    json.items.push({
      id: "00000000-0000-4000-8000-00000000c001",
      kind: "contextual",
      x: 24,
      y: 1224,
      w: 320,
      h: 330,
      z: 1,
      locked: false,
      frame: "00000000-0000-4000-8000-00000000f001",
      ref: `${RUN}:creative`,
      text: "Créative · Photo 1",
      role: null,
      url: null,
    });
    await route.fulfill({ json });
  });
  await reloadApp(page);
  await expectSpace(page, "board");
  const card = board.locator(".board-item.kind-contextual");
  await expect(card).toContainText("proportions estimées");
  await expect(card).toContainText("photo · ");
  await expect(card).toContainText("force 0.65");
  await expect(card).toContainText("références : Photo 1 (poids 0.6)");
  await expect(card.locator("img")).toBeVisible();
  await card.locator("summary", { hasText: "Prompt" }).click();
  await expect(card).toContainText("scandinavian, light oak");
  await expect(card.locator("img")).toBeVisible();

  // A3 : à conserver, commenter, dupliquer, sur la carte.
  await card.getByRole("button", { name: "À conserver" }).click();
  await expect(card.getByRole("button", { name: "Conservée" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(card).toHaveClass(/kept/);
  await card.getByRole("button", { name: "Commenter" }).click();
  await card.getByLabel("Commentaire").fill("garder le lit, changer la lampe");
  await card.getByLabel("Commentaire").blur();
  await expect(card).toContainText("garder le lit, changer la lampe");
  await card.getByRole("button", { name: "Dupliquer" }).click();
  await expect(board.locator(".board-item.kind-contextual")).toHaveCount(2);
  await expect(page.getByText("Enregistré", { exact: true })).toBeVisible({
    timeout: 15_000,
  });
  expect(outside).toEqual([]);
});
