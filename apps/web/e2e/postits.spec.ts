import { expect, test, type Locator, type Page } from "@playwright/test";
import { createEmptyProject, goSpace } from "./helpers";

/** KAN-65, mode local : post-its de couleur. Outil Post-it (touche n) qui pose un carré de la
 *  dernière couleur utilisée ; palette avec les actions de la sélection (un ou plusieurs) ;
 *  le texte se réduit pour tenir ; redimensionner garde un carré, un rectangle avec Maj ; une
 *  note d'avant est jaune ; thème sombre. Captures clair et sombre à 1280 × 800 si
 *  E2E_CAPTURE_DIR est défini. */

test.use({ viewport: { width: 1280, height: 800 } });

type Item = {
  id: string;
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
  text: string;
  color?: string | null;
};

async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function serverNotes(page: Page, id: string): Promise<Item[]> {
  const board = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: Item[] };
  return board.items.filter((i) => i.kind === "note");
}

const bg = (l: Locator) =>
  l.evaluate((el) => getComputedStyle(el).backgroundColor);

test("KAN-65 local : poser, colorer un ou plusieurs post-its, texte qui tient, carré, sombre", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const id = await createEmptyProject(page, "Post-its");
  // Une note d'avant KAN-65 (sans couleur), sous la mise en page de départ.
  const layout = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: Item[] };
  const bottom = Math.max(...layout.items.map((i) => i.y + i.h));
  const left = Math.min(...layout.items.map((i) => i.x));
  const old = {
    id: crypto.randomUUID(),
    kind: "note",
    x: left,
    y: bottom + 160,
    w: 240,
    h: 150,
    text: "Note d'avant",
  };
  // Et une rangée des huit couleurs, à côté (pour les captures).
  const names = [
    "Jaune",
    "Orange",
    "Rose",
    "Violet",
    "Bleu",
    "Vert",
    "Gris",
    "Blanc",
  ];
  const row = [
    "yellow",
    "orange",
    "pink",
    "violet",
    "blue",
    "green",
    "gray",
    "white",
  ].map((color, n) => ({
    id: crypto.randomUUID(),
    kind: "note",
    x: left + 300 + n * 220,
    y: bottom + 160,
    w: 200,
    h: 200,
    text: names[n],
    color,
  }));
  expect(
    (
      await page.request.patch(`/api/projects/${id}/board/items`, {
        data: { set: [old, ...row] },
      })
    ).status(),
  ).toBe(200);
  await goSpace(page, "board");
  const region = page.getByRole("region", { name: "Espace de travail" });
  const stage = region.locator(".board-stage");
  await expect(stage).toBeVisible();
  const oldNote = region.locator(".board-item.kind-note", {
    hasText: "Note d'avant",
  });
  // Must have 5 : jaune, sans migration (le serveur n'a rien réécrit).
  await expect(oldNote).toHaveClass(/postit-yellow/);
  expect(await bg(oldNote)).toBe("rgb(255, 241, 168)");
  expect(
    (await serverNotes(page, id)).find((n) => n.id === old.id)?.color ?? null,
  ).toBeNull();

  // De la place autour des cadres (on dézoome), puis deux coins vides à droite.
  for (let i = 0; i < 3; i++) await page.keyboard.press("-");
  const box = (await stage.boundingBox())!;
  const spotA = { x: box.x + box.width - 260, y: box.y + box.height * 0.3 };
  const spotB = { x: box.x + box.width - 110, y: box.y + box.height * 0.3 };
  const away = { x: box.x + box.width - 60, y: box.y + 40 };

  // Must have 1 : l'outil Post-it, par la touche n ; un carré.
  await page.keyboard.press("n");
  const tool = region.getByRole("button", { name: "Post-it", exact: true });
  await expect(tool).toHaveAttribute("aria-pressed", "true");
  await page.mouse.click(spotA.x, spotA.y);
  await region
    .getByRole("textbox", { name: "Texte du post-it" })
    .fill("Banquette");
  await page.mouse.click(away.x, away.y); // fin de l'écriture, la sélection reste
  const first = region.locator(".board-item.kind-note", {
    hasText: "Banquette",
  });
  await expect(first).toHaveClass(/postit-yellow/);
  const firstBox = (await first.boundingBox())!;
  expect(Math.abs(firstBox.width - firstBox.height)).toBeLessThan(1);

  // Must have 2 : la palette avec les actions de la sélection ; un clic, une couleur.
  const palette = page.getByRole("group", { name: "Couleur du post-it" });
  await expect(palette.getByRole("button")).toHaveCount(8);
  await expect(
    palette.getByRole("button", { name: "Jaune", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await palette.getByRole("button", { name: "Bleu", exact: true }).click();
  await expect(first).toHaveClass(/postit-blue/);
  await expect
    .poll(
      async () =>
        (await serverNotes(page, id)).find((n) => n.text === "Banquette")
          ?.color,
    )
    .toBe("blue");

  // La couleur par défaut est la dernière utilisée.
  await page.keyboard.press("n");
  await page.mouse.click(spotB.x, spotB.y);
  await region
    .getByRole("textbox", { name: "Texte du post-it" })
    .fill(
      "Un texte bien plus long, qui doit se réduire pour tenir entier dans le carré du post-it sans déborder",
    );
  await page.mouse.click(away.x, away.y);
  const second = region.locator(".board-item.kind-note", {
    hasText: "Un texte bien plus long",
  });
  await expect(second).toHaveClass(/postit-blue/);
  // Le titre de la sélection (texte long) tient sur une ligne, dans la boîte du panneau ;
  // le texte complet est dans l'infobulle, « ← Assistant » reste dans le panneau.
  const zone = page.getByRole("region", { name: "Sélection", exact: true });
  const title = zone.locator(".board-zone-title");
  await expect(title).toHaveAttribute("title", /sans déborder »$/);
  const zoneBox = (await zone.boundingBox())!;
  const titleBox = (await title.boundingBox())!;
  expect(titleBox.x + titleBox.width).toBeLessThanOrEqual(
    zoneBox.x + zoneBox.width + 0.5,
  );
  expect(titleBox.height).toBeLessThan(30);
  const backBox = (await zone
    .getByRole("button", { name: "← Assistant" })
    .boundingBox())!;
  expect(backBox.x + backBox.width).toBeLessThanOrEqual(
    zoneBox.x + zoneBox.width + 0.5,
  );

  // Must have 3 : le texte se réduit pour tenir.
  const size = (l: Locator) =>
    l
      .locator(".postit-text")
      .evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(await size(second)).toBeLessThan(await size(first));
  const fits = await second
    .locator(".postit-box")
    .evaluate((el) => el.scrollHeight <= el.clientHeight + 1);
  expect(fits).toBe(true);

  // Plusieurs post-its d'un coup.
  await first.click();
  await second.click({ modifiers: ["Shift"] });
  await expect(
    palette.getByRole("button", { name: "Bleu", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await palette.getByRole("button", { name: "Rose", exact: true }).click();
  await expect(first).toHaveClass(/postit-pink/);
  await expect(second).toHaveClass(/postit-pink/);
  await expect
    .poll(async () =>
      (await serverNotes(page, id))
        .filter((n) => n.text === "Banquette" || n.text.startsWith("Un texte"))
        .map((n) => n.color)
        .sort(),
    )
    .toEqual(["pink", "pink"]);

  // Double-clic pour écrire, sur une note d'avant aussi (elle reste jaune).
  await oldNote.dblclick();
  await region
    .getByRole("textbox", { name: "Texte du post-it" })
    .fill("Note d'avant, relue");
  await page.mouse.click(away.x, away.y);
  await expect(oldNote).toContainText("relue");
  await expect(oldNote).toHaveClass(/postit-yellow/);

  // Must have 3 : redimensionner garde un carré ; avec Maj, un rectangle. (Un appui de plus
  // sur le même post-it en moins de 450 ms serait un double-clic.)
  await page.waitForTimeout(500);
  await first.click();
  const drag = async (dx: number, dy: number, shift: boolean) => {
    const h = (await first.locator(".board-resize").boundingBox())!;
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
    if (shift) await page.keyboard.down("Shift");
    await page.mouse.down();
    await page.mouse.move(h.x + dx, h.y + dy, { steps: 5 });
    await page.mouse.up();
    if (shift) await page.keyboard.up("Shift");
  };
  await drag(40, 10, false);
  await expect
    .poll(async () => {
      const n = (await serverNotes(page, id)).find(
        (i) => i.text === "Banquette",
      );
      return n ? Math.round(n.w) === Math.round(n.h) && n.w > 200 : false;
    })
    .toBe(true);
  await drag(40, -8, true);
  await expect
    .poll(async () => {
      const n = (await serverNotes(page, id)).find(
        (i) => i.text === "Banquette",
      );
      return n ? n.w > n.h + 20 : false;
    })
    .toBe(true);

  // Captures : tout l'espace (Ctrl+0), deux post-its choisis, la palette dans le panneau.
  await page.waitForTimeout(500);
  await second.click();
  await first.click({ modifiers: ["Shift"] });
  await page.keyboard.press("Control+0");
  await expect(palette).toBeVisible();
  await shot(page, "kan65-1-clair");

  // Must have 6 : thème sombre, fonds profonds (texte AA vérifié en test unitaire).
  await page.emulateMedia({ colorScheme: "dark" });
  await expect.poll(() => bg(first)).toBe("rgb(107, 36, 67)");
  expect(await bg(oldNote)).toBe("rgb(92, 79, 18)");
  await page.waitForTimeout(600); // fin des transitions de couleur, pour la capture
  await shot(page, "kan65-2-sombre");
});
