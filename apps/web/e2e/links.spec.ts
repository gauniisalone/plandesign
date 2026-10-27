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
    "La photo de ce node construit Plan",
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
  // KAN-98 L2 : l'ouverture se fait au plancher ; le Moodboard (en haut à droite) peut être
  // hors de la vue : « Tout voir » (Maj+1) montre tout.
  await page.keyboard.press("Shift+Digit1");
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
      /seule une photo réelle de la pièce entre dans le node Photos de la pièce/,
    ),
  ).toBeVisible();
  await expect(inspiration).toHaveClass(/reference/);

  // La photo sortie de sa carte : phrase, bandeau avec « Annuler », pastille hors carte. Le
  // coin haut gauche de l'espace est vide (marge du cadrage d'ensemble).
  const view = (await board.boundingBox())!;
  await dragTo(page, photo, { x: view.x + 24, y: view.y + 24 }, async (p) => {
    await expect(p.locator(".board-drop-hint")).toContainText(
      "Relâcher hors node : cette photo ne servira plus au Plan",
    );
  });
  const toast = page.locator(".board-wire-toast");
  await expect(toast).toContainText("Hors node : cette photo ne servira plus");
  // Le plan ne ment pas : la photo sortie retire sa caméra, pas les murs déjà construits.
  await expect(toast).toContainText(
    "Les murs déjà construits restent ; refaites le plan depuis les photos du node pour repartir de zéro.",
  );
  await expect(photo.locator(".board-offcard")).toHaveText(
    "hors node : non utilisée",
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
  await page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Plan", exact: true })
    .click();
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

  // KAN-98 : « Photos de la pièce » dans la barre d'ajout : un node vide, sans lien.
  await page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Photos de la pièce", exact: true })
    .click();
  await expect(cards).toHaveCount(3);
  await expect(board.locator(".board-wire[data-kind=feeds_plan]")).toHaveCount(
    2,
  );
});

/** Un étage de plus, depuis l'espace de travail ; on y revient. */
async function addStorey(page: Page) {
  await page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Plan", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Nouveau plan" });
  await dialog.getByLabel("Nom").fill("Étage");
  await dialog.getByLabel("Sorte").selectOption({ label: "Nouvel étage" });
  await dialog.getByRole("button", { name: "Créer" }).click();
  await expectSpace(page, "plan");
  await goSpace(page, "board");
}

test("supprimer un plan : confirmation, sa carte part, ses photos restent hors carte, Ctrl+Z", async ({
  page,
}) => {
  test.setTimeout(180_000);
  await boardWithPlan(page, "Plan à supprimer");
  const board = page.getByRole("region", { name: "Espace de travail" });
  await addStorey(page);
  const plans = board.locator(".board-item.kind-plan");
  const cards = board.locator(".board-item.kind-frame.role-photos");
  await expect(plans).toHaveCount(2);
  const upstairs = cards.filter({ hasText: "Photos de la pièce · Étage" });
  await dropInto(page, upstairs, "etage.png");
  const photo = board.locator(".board-item.kind-photo");
  await expect(photo).toHaveCount(1, { timeout: 60_000 });

  // KAN-93 : le premier plan se supprime aussi (voir le test suivant).
  const first = plans.filter({ hasNotText: "Étage" });
  await expect(
    first.getByRole("button", { name: "Supprimer le plan" }),
  ).toBeEnabled();
  // Refuser la confirmation ne change rien ; l'accepter supprime le plan.
  const etage = plans.filter({ hasText: "Étage" });
  page.once("dialog", (d) => void d.dismiss());
  await etage.getByRole("button", { name: "Supprimer le plan" }).click();
  await expect(plans).toHaveCount(2);
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await etage.getByRole("button", { name: "Supprimer le plan" }).click();
  await expect(plans).toHaveCount(1, { timeout: 30_000 });
  expect(asked).toContain(
    "ses photos restent sur l'espace de travail, hors node",
  );
  expect(asked).toContain("Les autres plans ne changent pas.");
  await expect(cards).toHaveCount(1);
  await expect(
    board.locator(".board-item.kind-frame.role-renders"),
  ).toHaveCount(1);
  await expect(photo).toHaveCount(1);
  await expect(photo.locator(".board-offcard")).toHaveText(
    "hors node : non utilisée",
  );
  await expect(board.locator(".board-wire[data-kind=feeds_plan]")).toHaveCount(
    1,
  );
  await shot(page, "kan91-7-plan-supprime");

  // Ctrl+Z (dans le plan, comme les autres gestes du plan) : il revient, carte Photos reliée.
  await goSpace(page, "plan");
  await page.keyboard.press("Control+z");
  await goSpace(page, "board");
  await expect(plans).toHaveCount(2, { timeout: 30_000 });
  await expect(cards).toHaveCount(2);
  await expect(board.locator(".board-wire[data-kind=feeds_plan]")).toHaveCount(
    2,
  );
});

test("KAN-93 : supprimer le premier plan, le suivant devient premier avec ses nodes, Ctrl+Z", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const id = await boardWithPlan(page, "Premier plan à supprimer");
  const board = page.getByRole("region", { name: "Espace de travail" });
  // Deux mesures sur les murs du premier plan : la confirmation doit les annoncer.
  type Level = { elements: Record<string, { type: string }> };
  const before = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as { levels: Record<string, Level> };
  const walls = Object.entries(Object.values(before.levels)[0].elements)
    .filter(([, e]) => e.type === "wall")
    .map(([wid]) => wid);
  expect(walls.length).toBeGreaterThan(1);
  for (const wall of walls.slice(0, 2)) {
    const sent = await page.request.post(`/api/projects/${id}/measurements`, {
      data: { kind: "wall_length", value: 3.2, element_id: wall },
    });
    expect(sent.status()).toBe(201);
  }
  await page.reload();
  await goSpace(page, "board");
  await addStorey(page);
  const plans = board.locator(".board-item.kind-plan");
  const cards = board.locator(".board-item.kind-frame.role-photos");
  const renders = board.locator(".board-item.kind-frame.role-renders");
  const feeds = board.locator(".board-wire[data-kind=feeds_plan]");
  const guides = board.locator(".board-wire[data-kind=guides_style]");
  await expect(plans).toHaveCount(2);
  await expect(cards).toHaveCount(2);
  // Une photo dans la carte du premier plan, une dans celle de l'étage.
  const firstCard = cards.filter({ hasNotText: "Étage" });
  const upstairs = cards.filter({ hasText: "Photos de la pièce · Étage" });
  await dropInto(page, firstCard, "rdc.png");
  await dropInto(page, upstairs, "etage.png");
  const photos = board.locator(".board-item.kind-photo");
  await expect(photos).toHaveCount(2, { timeout: 60_000 });

  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await plans
    .filter({ hasNotText: "Étage" })
    .getByRole("button", { name: "Supprimer le plan" })
    .click();
  await expect(plans).toHaveCount(1, { timeout: 30_000 });
  expect(asked).toContain(
    "Le plan suivant devient le premier ; ses nodes restent les siens.",
  );
  expect(asked).toContain(
    "Ce plan porte 2 mesures : elles seront retirées avec lui (Ctrl+Z les rend).",
  );
  const measures = async () =>
    Object.keys(
      (
        (await (await page.request.get(`/api/projects/${id}`)).json()) as {
          measurements: Record<string, unknown>;
        }
      ).measurements,
    ).length;
  expect(await measures()).toBe(0);
  // L'étage garde sa carte Photos (avec sa photo) et sa carte Rendus ; aucune en double.
  await expect(plans).toContainText("Étage");
  await expect(cards).toHaveCount(1);
  await expect(renders).toHaveCount(1);
  await expect(photos).toHaveCount(2);
  await expect(cards.locator(".board-item.kind-photo")).toHaveCount(1);
  await expect(feeds).toHaveCount(1);
  // Le Moodboard de départ guide toujours le node Rendus restant.
  await expect(guides).toHaveCount(1);
  await shot(page, "kan93-1-premier-plan-supprime");

  // Ctrl+Z : le premier plan revient à sa place, avec une carte Photos et une carte Rendus.
  await goSpace(page, "plan");
  await page.keyboard.press("Control+z");
  await goSpace(page, "board");
  await expect(plans).toHaveCount(2, { timeout: 30_000 });
  await expect(cards).toHaveCount(2);
  await expect(renders).toHaveCount(2);
  await expect(feeds).toHaveCount(2);
  await expect(guides).toHaveCount(2);
  await expect(upstairs.locator(".board-item.kind-photo")).toHaveCount(1);
  expect(await measures()).toBe(2);
});

test("deux Moodboards en N-N : ils entrent par le haut des Rendus, pastilles hors des nodes", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const id = await boardWithPlan(page, "Inspirations N-N");
  const board = page.getByRole("region", { name: "Espace de travail" });
  await addStorey(page);
  const insp = board.locator(".board-item.kind-frame.role-inspirations");
  const guides = board.locator(".board-wire[data-kind=guides_style]");
  await expect(insp).toHaveCount(1);
  // La carte de départ guide les deux cartes Rendus ; la nouvelle n'est reliée à rien.
  await expect(guides).toHaveCount(2);
  await page
    .getByRole("group", { name: "Ajouter" })
    .getByRole("button", { name: "Moodboard", exact: true })
    .click();
  await expect(insp).toHaveCount(2);
  await expect(guides).toHaveCount(2);
  // On la relie aux deux cartes Rendus (comme avec sa poignée « + »).
  type Item = {
    id: string;
    kind: string;
    role: string | null;
    ref: string | null;
  };
  const items = async () =>
    (
      (await (await page.request.get(`/api/projects/${id}/board`)).json()) as {
        items: Item[];
      }
    ).items;
  await expect
    .poll(
      async () =>
        (await items()).filter((i) => i.role === "inspirations").length,
    )
    .toBe(2);
  const all = await items();
  const hand = all.find((i) => i.role === "inspirations" && i.ref)!;
  const renders = all.filter((i) => i.role === "renders" && i.kind === "frame");
  const origin = { Origin: new URL(page.url()).origin };
  const tied = await page.request.patch(`/api/projects/${id}/board/items`, {
    headers: origin,
    data: {
      links_set: renders.map((r) => ({
        id: crypto.randomUUID(),
        source: hand.id,
        target: r.id,
        kind: "guides_style",
      })),
    },
  });
  expect(tied.status()).toBe(200);
  await page.reload();
  await expect(guides).toHaveCount(4, { timeout: 30_000 });
  // KAN-98 L1b : le Moodboard guide le style par le HAUT du node Rendus.
  await expect(
    board.locator(".board-wire[data-kind=guides_style][data-entry=top]"),
  ).toHaveCount(4);
  await expect(
    board.locator(".board-wire[data-kind=renders_view][data-entry=left]"),
  ).toHaveCount(2);

  // Chaque Moodboard arrive sur le bord haut de son node Rendus, à sa propre abscisse.
  const ends = await guides.evaluateAll((gs) =>
    gs.map((g) => {
      const n = g
        .querySelector("path")!
        .getAttribute("d")!
        .match(/-?\d+(\.\d+)?/g)!;
      return [Number(n[n.length - 2]), Number(n[n.length - 1])];
    }),
  );
  const tops = await board
    .locator(".board-item.kind-frame.role-renders")
    .evaluateAll((fs) =>
      fs.map((f) => parseFloat((f as HTMLElement).style.top)),
    );
  for (const [, y] of ends)
    expect(tops.some((t) => Math.abs(t - y!) < 1)).toBe(true);
  expect(new Set(ends.map(([x, y]) => `${x}:${y}`)).size).toBe(4);
  await shot(page, "kan98-deux-moodboards");

  // Aucune pastille sur une carte (plan, Photos, Inspirations, Rendus).
  const badges = await board
    .locator(".board-wire-badge circle")
    .evaluateAll((cs) =>
      cs.map((c) => c.getBoundingClientRect().toJSON() as DOMRect),
    );
  const boxes = await board
    .locator(".board-item.kind-frame, .board-item.kind-plan")
    .evaluateAll((fs) =>
      fs.map((f) => f.getBoundingClientRect().toJSON() as DOMRect),
    );
  for (const b of badges)
    for (const c of boxes)
      expect(
        b.right <= c.left ||
          b.left >= c.right ||
          b.bottom <= c.top ||
          b.top >= c.bottom,
      ).toBe(true);
  // KAN-98 : après « Tout voir » (Maj+1), la barre d'ajout ne couvre aucun node. (L2 :
  // l'ouverture se fait au plancher et peut ne pas tout montrer.)
  await page.keyboard.press("Shift+Digit1");
  const boxes2 = await board
    .locator(".board-item.kind-frame, .board-item.kind-plan")
    .evaluateAll((fs) =>
      fs.map((f) => f.getBoundingClientRect().toJSON() as DOMRect),
    );
  const bar = (await page
    .getByRole("group", { name: "Ajouter" })
    .boundingBox())!;
  for (const c of boxes2)
    expect(
      c.bottom <= bar.y ||
        c.top >= bar.y + bar.height ||
        c.right <= bar.x ||
        c.left >= bar.x + bar.width,
    ).toBe(true);
  await shot(page, "kan91-8-inspirations-nn");
});
