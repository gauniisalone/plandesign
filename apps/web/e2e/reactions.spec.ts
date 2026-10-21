import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";
import { expectSpace } from "./helpers";

/** KAN-78 (local) : réagir à un élément de l'espace de travail. Le bouton « Réagir » au survol
 *  de l'élément et dans les actions de la sélection ouvre les six réactions ; une pastille par
 *  emoji sous l'élément, avec son nombre, la mienne mise en évidence (aria-pressed), le survol
 *  dit qui ; un clic la retire ; l'élément supprimé emporte ses réactions ; le journal le dit.
 *  Aucune vérification de couleur. */

async function projectWithNote(page: Page, text: string): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name: "Réactions KAN-78" },
  });
  const { id } = (await made.json()) as { id: string };
  await page.request.get(`/api/projects/${id}/board`);
  const note = {
    id: crypto.randomUUID(),
    kind: "note",
    x: 60,
    y: 60,
    w: 240,
    h: 160,
    text,
  };
  const set = await page.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [note] },
  });
  expect(set.status()).toBe(200);
  await gotoApp(page, `/#/projects/${id}/board`);
  await expectSpace(page, "board");
  return id;
}

test("KAN-78 : survol, sélecteur, pastille, retrait, sélection, suppression, journal", async ({
  page,
}) => {
  const id = await projectWithNote(page, "Idée canapé");
  const note = page.locator(".board-item.kind-note", {
    hasText: "Idée canapé",
  });
  const row = page.getByRole("group", { name: "Réactions Idée canapé" });

  // MH1 : au survol, le bouton « Réagir » sous l'élément ; il ouvre les six réactions.
  await expect(row).toHaveCount(0);
  await note.hover();
  await row.getByRole("button", { name: "Réagir", exact: true }).click();
  const picker = page.getByRole("menu", { name: "Choisir une réaction" });
  await expect(picker.getByRole("menuitemcheckbox")).toHaveCount(6);
  await expect(picker).toContainText("👍❤️😍🤔👎🔥");
  await picker
    .getByRole("menuitemcheckbox", { name: "😍 Coup de cœur" })
    .click();
  await expect(picker).toHaveCount(0);

  // MH2 : la pastille, son nombre, la mienne mise en évidence, le survol dit qui.
  const love = row.getByRole("button", { name: /^😍 1 : / });
  await expect(love).toHaveAttribute("aria-pressed", "true");
  await expect(love).toHaveText("😍1");
  await expect(love).toHaveAttribute("title", "Vous");
  await page.screenshot({ path: "e2e/screenshots/kan78_pastille.png" });

  // Un clic retire ma réaction.
  await love.click();
  await expect(row.getByRole("button", { name: /^😍/ })).toHaveCount(0);

  // MH1 : « Réagir » dans les actions de la sélection.
  await note.click();
  await page
    .getByRole("toolbar", { name: "Actions de la sélection" })
    .getByRole("button", { name: "Réagir", exact: true })
    .click();
  await picker.getByRole("menuitemcheckbox", { name: "👍 J'aime" }).click();
  await expect(
    row.getByRole("button", { name: /^👍 1 : Vous$/ }),
  ).toBeVisible();

  // MH5 : le journal du projet ; deux réactions de suite sur le même élément, regroupées
  // comme la cloche (« a réagi 2 fois à « Idée canapé » »).
  const activity = await page.request.get(
    `/api/projects/${id}/activity?kind=reactions`,
  );
  const lines = (
    (await activity.json()) as {
      items: { emoji: string | null; label: string; count: number }[];
    }
  ).items;
  expect(lines.map((l) => [l.label, l.count, l.emoji])).toEqual([
    ["Idée canapé", 2, null],
  ]);

  // MH3 : les pastilles suivent l'élément ; supprimé, il emporte ses réactions.
  // La note déplacée (ailleurs, puis relue) : sa rangée de pastilles est toujours sous elle.
  const board = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: { id: string; text: string; x: number }[] };
  const item = board.items.find((i) => i.text === "Idée canapé")!;
  await page.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [{ ...item, x: item.x + 400 }] },
  });
  await gotoApp(page, `/#/projects/${id}/board`);
  await expectSpace(page, "board");
  const noteBox = (await note.boundingBox())!;
  const rowBox = (await row.boundingBox())!;
  expect(Math.abs(rowBox.x - noteBox.x)).toBeLessThan(2);
  expect(rowBox.y).toBeGreaterThan(noteBox.y + noteBox.height);
  expect(rowBox.y - (noteBox.y + noteBox.height)).toBeLessThan(20);
  await note.click();
  await page
    .getByRole("toolbar", { name: "Actions de la sélection" })
    .getByRole("button", { name: "Supprimer", exact: true })
    .click();
  await expect(note).toHaveCount(0);
  await expect(row).toHaveCount(0);
  await expect
    .poll(
      async () =>
        (await (
          await page.request.get(`/api/projects/${id}/reactions`)
        ).json()) as unknown[],
    )
    .toEqual([]);
});
