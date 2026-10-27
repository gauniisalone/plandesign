import { expect, test, type Locator } from "@playwright/test";
import { createEmptyProject, goSpace } from "./helpers";

/** T7d1 (retour d'augus, 16 oct.) : aucun contenu ne déborde d'un cadre vide, même avec le
 *  navigateur zoomé à 150 % (1280 × 800 vus comme 853 × 533 px CSS), en thème sombre ; les
 *  états vides tiennent en une ligne courte, le plan vide n'a que ses deux boutons. */
test.use({
  viewport: { width: 853, height: 533 },
  deviceScaleFactor: 1.5,
  colorScheme: "dark",
});

async function inside(child: Locator, parent: Locator, label: string) {
  const c = (await child.boundingBox())!;
  const p = (await parent.boundingBox())!;
  expect(c, label).not.toBeNull();
  expect(c.x, `${label} : gauche`).toBeGreaterThanOrEqual(p.x - 1);
  expect(c.y, `${label} : haut`).toBeGreaterThanOrEqual(p.y - 1);
  expect(c.x + c.width, `${label} : droite`).toBeLessThanOrEqual(
    p.x + p.width + 1,
  );
  expect(c.y + c.height, `${label} : bas`).toBeLessThanOrEqual(
    p.y + p.height + 1,
  );
}

test("cadres vides à 150 % : états courts, rien ne déborde", async ({
  page,
}) => {
  await createEmptyProject(page, "Cadres à 150 %");
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });

  for (const [role, text] of [
    ["photos", "Déposez vos photos"],
    ["inspirations", "Déposez vos images d'ambiance"],
    ["renders", "Les rendus apparaîtront ici"],
  ] as const) {
    const frame = board.locator(`.board-item.kind-frame.role-${role}`);
    const hint = frame.locator(".board-frame-hint");
    await expect(hint).toHaveText(text);
    // Le texte tient dans sa boîte (rien de coupé) et la boîte dans le cadre.
    const fits = await hint.evaluate(
      (el) =>
        el.scrollHeight <= el.clientHeight + 1 &&
        el.scrollWidth <= el.clientWidth + 1,
    );
    expect(fits, `${role} : texte coupé`).toBe(true);
    await inside(hint, frame, role);
    const head = frame.locator(".board-frame-head");
    await inside(head, frame, `${role} : en-tête`);
  }

  // Le plan vide : seulement « Tracer moi-même », « Importer un plan » (KAN-77) et
  // « Générer le plan », dans le cadre.
  const plan = board.locator(".board-item.kind-plan");
  const empty = plan.locator(".board-plan-empty");
  await expect(empty.getByRole("button")).toHaveCount(3);
  await expect(empty).not.toContainText("Votre plan");
  for (const name of [
    "Tracer moi-même",
    "Importer un plan (DXF, PDF, image)",
    "Générer le plan",
  ])
    await inside(empty.getByRole("button", { name }), plan, name);
});

/** KAN-64 (MH5), qui remplace KAN-22 : le compositeur est dans le panneau de droite ; le
 *  cadrage tient compte du panneau ouvert : tous les cadres sont visibles en entier dans la
 *  partie libre de l'espace de travail, à gauche du panneau, à 150 % comme à 100 %. KAN-98 L2
 *  (PO, 27 oct.) : l'ouverture se fait au plancher (nodes de 360 px) et peut ne pas tout
 *  montrer ; c'est « Tout voir » (Maj+1) qui cadre tout. */
for (const [label, size] of [
  ["150 %", null],
  ["100 %", { width: 1280, height: 800 }],
] as const) {
  test(`Tout voir à ${label} : tout est visible à gauche du panneau ouvert`, async ({
    page,
  }) => {
    if (size) await page.setViewportSize(size);
    await createEmptyProject(page, `Cadrage à ${label}`);
    await goSpace(page, "board");
    const composer = page.getByRole("region", {
      name: "Compositeur",
      exact: true,
    });
    await expect(composer).toBeVisible();
    await page.keyboard.press("Shift+Digit1");
    const stage = (await page.locator(".board-stage").boundingBox())!;
    const side = (await page.locator(".side .side-card").boundingBox())!;
    expect(
      stage.x + stage.width,
      "le tableau s'arrête avant le panneau",
    ).toBeLessThanOrEqual(side.x + 1);
    const frames = page.locator(
      ".board-item.kind-frame, .board-item.kind-plan",
    );
    expect(await frames.count()).toBeGreaterThanOrEqual(4);
    for (const frame of await frames.all()) {
      const b = (await frame.boundingBox())!;
      expect(b.x, "cadre : gauche").toBeGreaterThanOrEqual(stage.x - 1);
      expect(b.y, "cadre : haut").toBeGreaterThanOrEqual(stage.y - 1);
      expect(b.x + b.width, "cadre : droite").toBeLessThanOrEqual(
        stage.x + stage.width + 1,
      );
      expect(b.y + b.height, "cadre : bas").toBeLessThanOrEqual(
        stage.y + stage.height + 1,
      );
    }
    // Le champ se remplit comme avant, dans le panneau.
    const field = composer.getByLabel("Vos envies et contraintes");
    await field.fill("chambre lumineuse");
    await expect(field).toHaveValue("chambre lumineuse");
    if (process.env.E2E_CAPTURE_DIR)
      await page.screenshot({
        path: `${process.env.E2E_CAPTURE_DIR}/kan64-cadrage-${size ? "100" : "150"}.png`,
      });
  });
}
