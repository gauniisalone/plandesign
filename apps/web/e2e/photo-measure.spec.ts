import { expect, test, type Page } from "@playwright/test";
import { expectSpace } from "./helpers";
import { gotoApp } from "./app-ready";
import {
  cameraBasis,
  DEFAULT_TEXT,
  mockReference,
  newState,
  SEG_ID,
  smallPhoto,
  type MockState,
} from "./photo-measure-mock";

/** KAN-66 (lot 2, front) : « Mesurer sur la photo » depuis les actions de la sélection.
 *  MH1 : deux clics sur la photo en grand, loupe ×4 qui suit le curseur, longueur saisie
 *  (« 2,04 »), PUT avec les points en pixels de l'image d'origine, refus 422 affiché tel quel.
 *  MH7 : la mesure est gardée (rouverte, elle est là), modifiable (PUT qui la remplace) et
 *  supprimable (DELETE) ; le lecteur est dans e2e-accounts/photo-measure-viewer.spec.ts.
 *  L'API de la mesure est simulée (contrat du 20 oct.) : le lot 1 serveur arrive à part. */

const REFUSAL =
  "les deux points ne sont pas sur une même surface : choisissez-les sur un même objet (le bord d'une porte, d'un meuble)";

async function photoProject(
  page: Page,
  name: string,
  basis: (imageId: string) => Record<string, unknown> | null,
): Promise<{
  id: string;
  imageId: string;
  w: number;
  h: number;
  state: MockState;
}> {
  await page.goto("about:blank");
  const png = await smallPhoto(page);
  const made = await page.request.post("/api/projects", { data: { name } });
  const { id } = (await made.json()) as { id: string };
  await page.request.post(`/api/projects/${id}/images`, {
    multipart: {
      file: { name: "porte.png", mimeType: "image/png", buffer: png },
    },
  });
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    images: Record<string, { width_px: number; height_px: number }>;
  };
  const [imageId, image] = Object.entries(project.images)[0] ?? [];
  expect(imageId).toBeTruthy();
  const state = newState(basis(imageId ?? ""));
  await mockReference(page, id, imageId ?? "", state);
  await gotoApp(page, `/#/projects/${id}/board`);
  await expectSpace(page, "board");
  return {
    id,
    imageId: imageId ?? "",
    w: image?.width_px ?? 0,
    h: image?.height_px ?? 0,
    state,
  };
}

async function openMeasure(page: Page, button = "Mesurer sur la photo") {
  const board = page.getByRole("region", { name: "Espace de travail" });
  await board.locator(".board-item.kind-photo").first().click();
  await page.getByRole("button", { name: button, exact: true }).click();
}

test("KAN-66 MH1 + MH7 : deux clics, loupe ×4, longueur, 422 tel quel, gardée, modifiée, supprimée", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const { w, h, state } = await photoProject(page, "Mesure KAN-66", (img) =>
    cameraBasis(img),
  );
  state.refusals.push(REFUSAL);
  await openMeasure(page);
  const dialog = page.getByRole("dialog", { name: "Mesurer sur la photo" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText(DEFAULT_TEXT);
  await expect(dialog).toContainText("Point 1 sur 2");
  await expect(dialog).toContainText("Aucune mesure sur cette photo.");

  // MH1 : la loupe ×4 suit le curseur pendant le choix d'un point.
  const photo = dialog.locator(".pm-photo-box img");
  const box = await photo.boundingBox();
  if (!box) throw new Error("photo absente du calque");
  const at = (fx: number, fy: number) =>
    [box.x + box.width * fx, box.y + box.height * fy] as const;
  const loupe = page.getByRole("img", { name: "Loupe ×4" });
  await expect(loupe).toHaveCount(0);
  await page.mouse.move(...at(0.25, 0.75));
  await expect(loupe).toBeVisible();
  const first = await loupe.boundingBox();
  await page.mouse.click(...at(0.25, 0.75));
  await expect(dialog).toContainText("Point 2 sur 2");
  await page.mouse.move(...at(0.6, 0.3));
  await expect(loupe).toBeVisible();
  const second = await loupe.boundingBox();
  expect(second?.x).not.toBe(first?.x);
  await page.mouse.click(...at(0.25, 0.3));
  await expect(loupe).toHaveCount(0);

  // MH1 : la longueur, saisie avec une virgule ; une saisie illisible est refusée avant
  // l'envoi.
  const form = page.getByRole("dialog", { name: "Longueur du segment" });
  await expect(form).toBeVisible();
  const length = form.getByLabel("Longueur", { exact: true });
  const send = form.getByRole("button", { name: "Recaler l'estimation" });
  await length.fill("porte");
  await expect(form).toContainText("« porte » n'est pas une longueur");
  await expect(send).toBeDisabled();
  await length.fill("2,04");
  await expect(form).toContainText("= 2,04 m");
  await form.getByLabel("Libellé (facultatif)").fill("porte");
  await page.screenshot({ path: "e2e/screenshots/kan66_saisie.png" });

  // Le refus du serveur est affiché tel quel, la saisie reste.
  await send.click();
  await expect(form.getByRole("alert")).toContainText("Mesure refusée");
  await expect(form.getByRole("alert")).toContainText(REFUSAL);
  await expect(length).toHaveValue("2,04");
  expect(state.puts).toHaveLength(1);

  // Accepté : points en pixels de l'image d'origine, longueur en mètres, libellé.
  await send.click();
  await expect(form).toHaveCount(0);
  expect(state.puts).toHaveLength(2);
  const put = state.puts[1];
  expect(put?.length_m).toBe(2.04);
  expect(put?.label).toBe("porte");
  expect(Math.abs((put?.p1[0] ?? 0) - 0.25 * w)).toBeLessThan(w * 0.01);
  expect(Math.abs((put?.p1[1] ?? 0) - 0.75 * h)).toBeLessThan(h * 0.01);
  expect(Math.abs((put?.p2[1] ?? 0) - 0.3 * h)).toBeLessThan(h * 0.01);

  // Segment posé avec sa cote ; l'échelle dite par le serveur, avant → après.
  await expect(dialog.locator(".pm-cote")).toHaveText("2,04 m · porte");
  await expect(dialog.getByLabel("Échelle de l'estimation")).toContainText(
    "Échelle calée sur votre mesure de 2,04 m (porte)",
  );
  await expect(dialog).toContainText("mesurée");
  await expect(dialog).toContainText("Longueur 4,06 → 5,00 m");
  await expect(dialog.getByRole("status")).toContainText("Estimation recalée");
  await page.screenshot({ path: "e2e/screenshots/kan66_posee.png" });

  // MH7 : gardée. Fermé puis rouvert, la mesure est là.
  await dialog.getByRole("button", { name: "Fermer", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await openMeasure(page);
  await expect(dialog.locator(".pm-cote")).toHaveText("2,04 m · porte");
  const list = dialog.getByRole("list", { name: "Mesures de référence" });
  await expect(list).toContainText("2,04 m");

  // MH7 : modifiable. Le PUT remplace la mesure.
  await list.getByRole("button", { name: "Modifier", exact: true }).click();
  await expect(length).toHaveValue("2,04 m");
  await length.fill("204 cm");
  await expect(form).toContainText("= 2,04 m");
  await length.fill("1,85 m");
  await send.click();
  await expect(form).toHaveCount(0);
  expect(state.puts).toHaveLength(3);
  expect(state.puts[2]?.length_m).toBe(1.85);
  // Mêmes points qu'avant : seule la longueur a changé.
  expect(state.puts[2]?.p1).toEqual(put?.p1);
  await expect(dialog.locator(".pm-cote")).toHaveText("1,85 m · porte");

  // MH7 : supprimable. Retour à l'échelle de la hauteur de prise de vue.
  await list.getByRole("button", { name: "Supprimer", exact: true }).click();
  await expect(dialog).toContainText("Aucune mesure sur cette photo.");
  expect(state.deletes).toEqual([SEG_ID]);
  await expect(dialog.locator(".pm-cote")).toHaveCount(0);
  await expect(dialog.getByRole("status")).toContainText("Mesure retirée");
  await expect(dialog.getByLabel("Échelle de l'estimation")).toContainText(
    DEFAULT_TEXT,
  );

  // Échap ferme le calque sans toucher l'espace de travail (la photo reste là).
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.locator(".board-item.kind-photo")).toHaveCount(1);
});

test("KAN-66 : photo d'avant, « Réestimer la photo pour pouvoir mesurer » puis mesure possible", async ({
  page,
}) => {
  const { state } = await photoProject(page, "Réestimer KAN-66", (img) =>
    cameraBasis(img, null),
  );
  await openMeasure(page);
  const dialog = page.getByRole("dialog", { name: "Mesurer sur la photo" });
  await expect(dialog).toContainText("n'a pas été gardée");
  await expect(dialog).not.toContainText("Point 1 sur 2");
  await dialog
    .getByRole("button", { name: "Réestimer la photo pour pouvoir mesurer" })
    .click();
  await expect(dialog).toContainText("Point 1 sur 2", { timeout: 15_000 });
  await expect(dialog.getByRole("status")).toContainText(
    "Photo prête pour la mesure",
  );
  expect(state.reestimates).toBe(1);
});
