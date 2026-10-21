import { expect, test, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";
import { expectSpace } from "./helpers";

/** KAN-78, Must have 10 : les animations des réactions (pop, chiffre qui glisse, fondu, repli,
 *  ouverture du sélecteur). En mouvement réduit (emulateMedia reducedMotion), aucune animation
 *  n'est active, aucune pastille fantôme ; l'état final est le même avec et sans animation. */

async function setup(page: Page, name: string): Promise<Locator> {
  const made = await page.request.post("/api/projects", { data: { name } });
  const { id } = (await made.json()) as { id: string };
  await page.request.get(`/api/projects/${id}/board`);
  const note = {
    id: crypto.randomUUID(),
    kind: "note",
    x: 60,
    y: 60,
    w: 240,
    h: 160,
    text: "Idée animée",
  };
  await page.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [note] },
  });
  await gotoApp(page, `/#/projects/${id}/board`);
  await expectSpace(page, "board");
  return page.getByRole("group", { name: "Réactions Idée animée" });
}

const running = (row: Locator) =>
  row.evaluate((el) => el.getAnimations({ subtree: true }).length);

/** Le même parcours : 😍, puis 👍, puis 😍 retiré (sa pastille passe à 0). Rend le nombre
 *  d'animations vues à chaque pas, et l'état final des pastilles. */
async function scenario(page: Page, row: Locator) {
  const seen: number[] = [];
  const picker = page.getByRole("menu", { name: "Choisir une réaction" });
  const choose = async (name: string) => {
    await page.locator(".board-item.kind-note").hover();
    await row.getByRole("button", { name: "Réagir", exact: true }).click();
    await expect(picker).toBeVisible();
    seen.push(await running(row)); // ouverture du sélecteur
    await picker.getByRole("menuitemcheckbox", { name }).click();
  };
  await choose("😍 Coup de cœur");
  const love = row.getByRole("button", { name: /^😍 1 : / });
  await expect(love).toHaveAttribute("aria-pressed", "true");
  seen.push(await running(row)); // pop
  await choose("👍 J'aime");
  await expect(row.getByRole("button", { name: /^👍 1 : / })).toBeVisible();
  await love.click();
  await expect(love).toHaveCount(0);
  seen.push(await running(row)); // repli de la pastille passée à 0
  const ghosts = await row.locator(".leaving, .board-reaction-float").count();
  // L'état final, une fois tout fini.
  await page.waitForTimeout(800);
  const final = await row
    .getByRole("button", { name: /:/ })
    .evaluateAll((els) =>
      els.map(
        (e) =>
          `${e.getAttribute("aria-label")}|${e.getAttribute("aria-pressed")}`,
      ),
    );
  return { seen, ghosts, final };
}

test("KAN-78 MH10 : animées par défaut, aucune en mouvement réduit, même état final", async ({
  page,
  browser,
}) => {
  const moving = await scenario(page, await setup(page, "Animations"));
  // Avec animations : quelque chose bouge à chaque pas.
  expect(moving.seen.every((n) => n > 0)).toBe(true);

  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    reducedMotion: "reduce",
  });
  const still = await context.newPage();
  await still.emulateMedia({ reducedMotion: "reduce" });
  const calm = await scenario(still, await setup(still, "Mouvement réduit"));
  expect(calm.seen).toEqual([0, 0, 0, 0]);
  expect(calm.ghosts).toBe(0);

  expect(calm.final).toEqual(moving.final);
  expect(calm.final).toEqual(["👍 1 : Vous|true"]);
  await context.close();
});
