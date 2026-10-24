import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  createEmptyProject,
  expectSpace,
  goSpace,
  waitNoHeavyJob,
} from "./helpers";

// T7d1 : aucun test ne commence tant que le serveur calcule encore pour le précédent.
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** KAN-91 : l'espace de travail en cartes reliées (maquette validée par augus le 22 oct.).
 *  Liens posés tout seuls et bandeau unique ; survol (la phrase vient de ce que la génération
 *  lira) ; lien choisi, « Couper », « Annuler », coupure gardée à la relecture ; glisser une
 *  image dans une carte, hors d'une carte (phrase, « Annuler »), refus expliqué d'une
 *  inspiration dans la carte Photos ; pastille « hors carte : non utilisée ». */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function dropInto(page: Page, frame: Locator, name: string) {
  const box = (await frame.boundingBox())!;
  const dt = await page.evaluateHandle(
    ([data, fileName]) => {
      const dt = new DataTransfer();
      dt.items.add(
        new File([new Uint8Array(data as number[])], fileName as string, {
          type: "image/png",
        }),
      );
      return dt;
    },
    [Array.from(PNG), name] as const,
  );
  await frame.dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
}

/** Un glisser à la souris, en plusieurs pas (seuil de 5 px dépassé). */
async function dragTo(
  page: Page,
  item: Locator,
  to: { x: number; y: number },
  hold?: (page: Page) => Promise<void>,
) {
  const box = (await item.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + 20);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 12, box.y + 30, { steps: 3 });
  await page.mouse.move(to.x, to.y, { steps: 12 });
  if (hold) await hold(page);
  await page.mouse.up();
}

async function boardWithPlan(page: Page, name: string): Promise<string> {
  const id = await createEmptyProject(page, name);
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  return id;
}

test("liens posés tout seuls : bandeau, survol, couper puis annuler, coupure gardée", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const id = await boardWithPlan(page, "Cartes reliées");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const wire = (kind: string) =>
    board.locator(`.board-wire[data-kind=${kind}]`);
  // Zéro geste : photos → plan, plan → rendus, inspirations → rendus, et le bandeau unique.
  await expect(wire("feeds_plan")).toHaveCount(1);
  await expect(wire("renders_view")).toHaveCount(1);
  await expect(wire("guides_style")).toHaveCount(1);
  const banner = page.getByText(
    "PlanDesign a relié vos photos aux plans et vos inspirations aux rendus.",
  );
  await expect(banner).toBeVisible();
  await shot(page, "kan91-1-liens-auto");
  await page.getByRole("button", { name: "Compris" }).click();
  await expect(banner).toHaveCount(0);

  // Une photo dans la carte Photos : le survol du lien la compte (phrase du serveur).
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await dropInto(page, photos, "piece.png");
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 60_000,
  });
  const badge = wire("feeds_plan").locator(".board-wire-badge");
  await badge.hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "La photo de cette carte construit Plan",
  );
  await expect(page.getByRole("tooltip")).toContainText(
    "modifiable par tous les éditeurs du projet",
  );
  await shot(page, "kan91-2-survol");

  // Clic : la barre du lien, « Couper », puis « Annuler ».
  await badge.dispatchEvent("pointerdown");
  const bar = page.getByRole("toolbar", { name: "Lien choisi" });
  await expect(bar).toContainText("Photo → plan");
  await bar.getByRole("button", { name: "Couper" }).click();
  await expect(wire("feeds_plan")).toHaveCount(0);
  const toast = page.locator(".board-wire-toast");
  await expect(toast).toContainText("Lien coupé : les photos de");
  await shot(page, "kan91-3-coupe");
  await toast.getByRole("button", { name: "Annuler" }).click();
  await expect(wire("feeds_plan")).toHaveCount(1);

  // Suppr coupe le lien choisi ; la coupure tient à la relecture (le serveur la retient).
  await wire("guides_style")
    .locator(".board-wire-badge")
    .dispatchEvent("pointerdown");
  await page.keyboard.press("Delete");
  await expect(wire("guides_style")).toHaveCount(0);
  await expect
    .poll(async () => {
      const b = (await (
        await page.request.get(`/api/projects/${id}/board`)
      ).json()) as { links: { kind: string }[]; cut_links: string[] };
      return [
        b.links.some((l) => l.kind === "guides_style"),
        b.cut_links.length,
      ];
    })
    .toEqual([false, 1]);
  await page.reload();
  await expect(wire("renders_view")).toHaveCount(1, { timeout: 30_000 });
  await expect(wire("guides_style")).toHaveCount(0);
});

test("glisser dans une carte, hors d'une carte, refus expliqué, pastille hors carte", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await boardWithPlan(page, "Cartes métier");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const inspirations = board.locator(
    ".board-item.kind-frame.role-inspirations",
  );
  await dropInto(page, photos, "piece.png");
  await dropInto(page, inspirations, "chalet.png");
  const photo = board.locator(".board-item.kind-photo");
  const inspiration = board.locator(".board-item.kind-file");
  await expect(photo).toHaveCount(1, { timeout: 60_000 });
  await expect(inspiration).toHaveCount(1);
  await expect(board.locator(".board-offcard")).toHaveCount(0);

  // Une inspiration vers la carte Photos : la carte se refuse, la raison est dite.
  const target = (await photos.boundingBox())!;
  await dragTo(
    page,
    inspiration,
    { x: target.x + target.width / 2, y: target.y + target.height - 30 },
    async (p) => {
      await expect(photos).toHaveClass(/board-drop-refused/);
      await expect(p.locator(".board-drop-hint")).toContainText(
        "Une inspiration ne construit pas le plan",
      );
      await shot(p, "kan91-4-refus");
    },
  );
  await expect(
    page.getByText(
      /seule une photo réelle de la pièce entre dans la carte Photos/,
    ),
  ).toBeVisible();
  await expect(inspiration).toHaveClass(/reference/);

  // La photo sortie de sa carte : phrase, bandeau avec « Annuler », pastille hors carte. Le
  // coin haut gauche de l'espace est vide (marge du cadrage d'ensemble).
  const view = (await board.boundingBox())!;
  await dragTo(page, photo, { x: view.x + 24, y: view.y + 24 }, async (p) => {
    await expect(p.locator(".board-drop-hint")).toContainText(
      "Relâcher hors carte : cette photo ne servira plus au Plan",
    );
  });
  const toast = page.locator(".board-wire-toast");
  await expect(toast).toContainText("Hors carte : cette photo ne servira plus");
  // Le plan ne ment pas : la photo sortie retire sa caméra, pas les murs déjà construits.
  await expect(toast).toContainText(
    "Les murs déjà construits restent ; refaites le plan depuis les photos de la carte pour repartir de zéro.",
  );
  await expect(photo.locator(".board-offcard")).toHaveText(
    "hors carte : non utilisée",
  );
  await shot(page, "kan91-5-hors-carte");
  await toast.getByRole("button", { name: "Annuler" }).click();
  await expect(photo.locator(".board-offcard")).toHaveCount(0);
});

test("une carte Photos par plan : l'étage a la sienne, elle n'alimente que l'étage", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const id = await boardWithPlan(page, "Deux niveaux");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const cards = board.locator(".board-item.kind-frame.role-photos");
  await expect(cards).toHaveCount(1);
  await board.getByRole("button", { name: "Nouveau plan" }).click();
  const dialog = page.getByRole("dialog", { name: "Nouveau plan" });
  await dialog.getByLabel("Nom").fill("Étage");
  await dialog.getByLabel("Sorte").selectOption({ label: "Nouvel étage" });
  await dialog.getByRole("button", { name: "Créer" }).click();
  await expectSpace(page, "plan");
  await goSpace(page, "board");

  // Sa carte apparaît, titrée du nom du niveau, reliée à son plan.
  const upstairs = cards.filter({ hasText: "Photos de la pièce · Étage" });
  await expect(cards).toHaveCount(2);
  await expect(upstairs).toHaveCount(1);
  await expect(board.locator(".board-wire[data-kind=feeds_plan]")).toHaveCount(
    2,
  );
  await shot(page, "kan91-6-carte-etage");

  // Une photo glissée dans la carte de l'étage n'alimente que l'étage.
  await dropInto(page, upstairs, "etage.png");
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 60_000,
  });
  type Item = { id: string; kind: string; ref: string | null; text: string };
  const read = async () =>
    (await (await page.request.get(`/api/projects/${id}/board`)).json()) as {
      items: Item[];
    };
  const plans = (await read()).items.filter((i) => i.kind === "plan");
  expect(plans).toHaveLength(2);
  const photosOf = async (plan: Item) =>
    (
      (await (
        await page.request.get(`/api/projects/${id}/board/inputs/${plan.id}`)
      ).json()) as { photos: string[] }
    ).photos.length;
  await expect
    .poll(async () =>
      Promise.all(plans.map(async (p) => [p.text, await photosOf(p)])),
    )
    .toEqual(
      expect.arrayContaining([
        [expect.stringContaining("Étage"), 1],
        [expect.not.stringContaining("Étage"), 0],
      ]),
    );

  // « Nouvelle carte Photos » : une carte vide, sans lien.
  await board.getByRole("button", { name: "Nouvelle carte Photos" }).click();
  await expect(cards).toHaveCount(3);
  await expect(board.locator(".board-wire[data-kind=feeds_plan]")).toHaveCount(
    2,
  );
});
