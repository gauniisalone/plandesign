import { expect, test, type Page } from "@playwright/test";
import { createEmptyProject, goSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** KAN-51 (lot 2), mode local : commentaires de l'espace de travail, à l'identique de la
 *  prod. Poser une épingle (outil Commentaire, touche c), répondre, résoudre puis rouvrir,
 *  masquer les épingles ; une épingle posée sur un élément le suit, et devient « détachée »
 *  si l'élément disparaît. Le texte est affiché comme du texte. */

async function board(page: Page, name: string) {
  const id = await createEmptyProject(page, name);
  await goSpace(page, "board");
  const region = page.getByRole("region", { name: "Espace de travail" });
  await expect(region.locator(".board-stage")).toBeVisible();
  return { id, region, stage: region.locator(".board-stage") };
}

/** Un coin vide de l'espace de travail : on dézoome, ce qui laisse de la place autour des
 *  cadres (le centre tombe sur le cadre du plan et ses boutons). */
async function emptySpot(page: Page) {
  for (let i = 0; i < 3; i++) await page.keyboard.press("-");
  const box = (await page.locator(".board-stage").boundingBox())!;
  return { x: box.x + box.width - 140, y: box.y + box.height * 0.3 };
}

async function publish(page: Page, text: string) {
  const draft = page.getByRole("dialog", { name: "Nouveau commentaire" });
  await draft.getByLabel("Votre commentaire").fill(text);
  await draft.getByRole("button", { name: "Publier" }).click();
}

test("KAN-51 local : poser, répondre, résoudre, rouvrir, masquer", async ({
  page,
}) => {
  const { region } = await board(page, "Commentaires");
  const spot = await emptySpot(page);
  // Outil par la touche c, puis un clic n'importe où.
  await page.keyboard.press("c");
  await expect(
    region.getByRole("button", { name: "Commentaire", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.mouse.click(spot.x, spot.y);
  await publish(page, "<b>Plus</b> de lumière ici");
  const pin = region.getByRole("button", { name: "Commentaire de Vous" });
  await expect(pin).toBeVisible();
  await expect(pin).toHaveText("1");
  // Le fil : signé « Vous », le texte tel quel (pas de HTML interprété).
  await pin.click();
  // KAN-64 : le fil s'ouvre dans la zone « Commentaires » du panneau de droite.
  const panel = page.getByRole("region", { name: "Commentaires", exact: true });
  await expect(panel.getByText("<b>Plus</b> de lumière ici")).toBeVisible();
  await expect(panel.locator(".comment-author")).toHaveText("Vous");
  await panel.getByLabel("Répondre").fill("Et une lampe");
  await panel.getByRole("button", { name: "Répondre" }).click();
  await expect(panel.locator(".comment-messages li")).toHaveCount(2);
  await expect(pin).toHaveText("2");
  await page.screenshot({ path: "e2e/screenshots/kan51_local_fil.png" });
  // Résoudre masque l'épingle ; le fil reste dans « Résolus », et se rouvre.
  await panel.getByRole("button", { name: "Résoudre" }).click();
  await expect(pin).toHaveCount(0);
  await panel.getByRole("button", { name: "← Tous les commentaires" }).click();
  await panel.getByRole("tab", { name: /Résolus \(1\)/ }).click();
  await panel.getByRole("button", { name: /Plus/ }).click();
  await panel.getByRole("button", { name: "Rouvrir" }).click();
  await expect(pin).toBeVisible();
  // Masquer les épingles d'un clic (en-tête de la zone), puis les réafficher.
  await page.getByRole("button", { name: "Masquer les épingles" }).click();
  await expect(pin).toHaveCount(0);
  await page.getByRole("button", { name: "Afficher les épingles" }).click();
  await expect(pin).toBeVisible();
});

test("KAN-51 local : une épingle sur un élément le suit, puis reste « détachée »", async ({
  page,
}) => {
  const { id, region, stage } = await board(page, "Épingle sur une note");
  const box = (await stage.boundingBox())!;
  // Une note, puis une épingle posée sur elle.
  await region.getByRole("button", { name: "Post-it", exact: true }).click();
  await stage.click({ position: { x: 200, y: box.height / 2 } });
  await region
    .getByRole("textbox", { name: "Texte du post-it" })
    .fill("Canapé");
  await stage.click({ position: { x: box.width - 60, y: 40 } });
  const note = region.locator(".board-item.kind-note", { hasText: "Canapé" });
  await expect(note).toBeVisible();
  await page.keyboard.press("c");
  const at = (await note.boundingBox())!;
  await page.mouse.click(at.x + 20, at.y + 20);
  await publish(page, "Trop près du mur");
  const pin = region.getByRole("button", { name: "Commentaire de Vous" });
  await expect(pin).toBeVisible();
  const leftOf = async () =>
    pin.evaluate((el) => parseFloat((el as HTMLElement).style.left));
  const before = await leftOf();
  // La note bouge (écriture par l'API, puis relecture) : l'épingle la suit.
  type Item = {
    id: string;
    kind: string;
    x: number;
    y: number;
    w: number;
    h: number;
    text: string;
  };
  // La note part au serveur après un court délai (B2c) : on attend qu'elle y soit.
  let moved: Item | undefined;
  await expect
    .poll(async () => {
      const items = (
        (await (
          await page.request.get(`/api/projects/${id}/board`)
        ).json()) as {
          items: Item[];
        }
      ).items;
      moved = items.find((i) => i.kind === "note" && i.text === "Canapé");
      return moved !== undefined;
    })
    .toBe(true);
  if (!moved) throw new Error("note absente du serveur");
  await page.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [{ ...moved, x: moved.x + 300 }] },
  });
  await reloadApp(page);
  await goSpace(page, "board");
  await expect.poll(leftOf).toBeCloseTo(before + 300, 0);
  // La note disparaît : le fil reste, « détaché », à la dernière position connue.
  await page.request.patch(`/api/projects/${id}/board/items`, {
    data: { delete: [moved.id] },
  });
  await reloadApp(page);
  await goSpace(page, "board");
  const detached = region.getByRole("button", {
    name: "Commentaire de Vous (détaché)",
  });
  await expect(detached).toBeVisible();
  await detached.click();
  await expect(
    page
      .getByRole("region", { name: "Commentaires", exact: true })
      .getByText(/Détaché/),
  ).toBeVisible();
});
