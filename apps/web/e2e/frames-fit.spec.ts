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

  // Le plan vide : seulement « Tracer moi-même » et « Générer le plan », dans le cadre.
  const plan = board.locator(".board-item.kind-plan");
  const empty = plan.locator(".board-plan-empty");
  await expect(empty.getByRole("button")).toHaveCount(2);
  await expect(empty).not.toContainText("Votre plan");
  for (const name of ["Tracer moi-même", "Générer le plan"])
    await inside(empty.getByRole("button", { name }), plan, name);
});
