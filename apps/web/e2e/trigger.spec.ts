import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import {
  canRender,
  createEmptyProject,
  goSpace,
  NO_RENDER,
  waitNoHeavyJob,
} from "./helpers";

// T7d1 : isolation des e2e lourds : aucun test ne commence tant que le serveur calcule
// encore pour le précédent (même s'il a échoué).
test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

/** T4, KAN-120 : le déclencheur du chemin sur l'espace de travail. Une photo déposée dans le
 *  node Photos ne lance plus rien (la bascule « générer au dépôt d'une photo » a disparu) ;
 *  « Générer le plan à partir de 1 photo » du node, ou « Générer maintenant », construit le
 *  plan (1 geste mesuré). Aucune requête externe. */
const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);

async function dropInto(
  page: Page,
  frame: Locator,
  name: string,
  bytes: Buffer,
) {
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
    [Array.from(bytes), name] as const,
  );
  await frame.dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });
}

async function metrics(page: Page, id: string) {
  return (await (
    await page.request.get(`/api/projects/${id}/metrics`)
  ).json()) as {
    generate_clicks: number;
    gestures_before_first_render: number;
  };
}

test("déclencheur : la photo déposée attend le bouton ; « Générer maintenant » (1 geste)", async ({
  page,
}) => {
  test.skip(!(await canRender(page)), NO_RENDER);
  test.setTimeout(300_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });

  // KAN-120 : plus de bascule ; la photo déposée ne lance rien.
  const manual = await createEmptyProject(page, "Déclencheur manuel");
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const trigger = page.getByRole("group", { name: "Déclencheur" });
  await expect(
    trigger.getByLabel("Générer automatiquement au dépôt d'une photo"),
  ).toHaveCount(0);
  const photos = board.locator(".board-item.kind-frame.role-photos");
  await dropInto(page, photos, "piece.png", PNG);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 60_000,
  });
  await page.waitForTimeout(3000);
  const plan = board.locator(".board-item.kind-plan");
  await expect(plan.locator("img.board-plan")).toHaveCount(0);
  await expect(
    photos.getByRole("button", { name: "Générer le plan à partir de 1 photo" }),
  ).toBeVisible();
  // KAN-64 : la photo déposée reste choisie, et ses actions ont pris la place de
  // l'Assistant ; on revient à l'Assistant (un clic) pour « Générer maintenant ».
  const back = page
    .locator(".side")
    .getByRole("button", { name: "← Assistant", exact: true });
  if (await back.isVisible()) await back.click();
  await trigger.getByRole("button", { name: "Générer maintenant" }).click();
  await expect(plan.locator("img.board-plan")).toBeVisible({
    timeout: 180_000,
  });
  const m1 = await metrics(page, manual);
  expect(m1.generate_clicks).toBe(1);
  expect(m1.gestures_before_first_render).toBe(1);
  expect(outside).toEqual([]);
});
