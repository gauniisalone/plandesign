import { expect, test, type Locator, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, goSpace } from "./helpers";

/** T4 : le déclencheur du chemin « zéro geste » sur le tableau. Bascule « générer au dépôt
 *  d'une photo » activée par défaut : une photo déposée sur un projet sans plan construit le
 *  plan sans autre geste (0 geste mesuré) ; coupée, rien ne part, « Générer maintenant » le
 *  fait (1 geste mesuré). Aucune requête externe. */
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

test("déclencheur : au dépôt (0 geste) ou « Générer maintenant » (1 geste)", async ({
  page,
}) => {
  test.setTimeout(300_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });

  // Bascule activée (défaut) : la photo déposée construit le plan.
  const auto = await createEmptyProject(page, "Déclencheur auto");
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const trigger = page.getByRole("group", { name: "Déclencheur" });
  const toggle = trigger.getByLabel(
    "Générer automatiquement au dépôt d'une photo",
  );
  await expect(toggle).toBeChecked();
  await dropInto(
    page,
    board.locator(".board-item.kind-frame.role-photos"),
    "piece.png",
    PNG,
  );
  const plan = board.locator(".board-item.kind-plan");
  await expect(plan.locator("img.board-plan")).toBeVisible({
    timeout: 180_000,
  });
  const m0 = await metrics(page, auto);
  expect(m0.generate_clicks).toBe(0);
  expect(m0.gestures_before_first_render).toBe(0);

  // Bascule coupée : rien ne part ; « Générer maintenant » construit le plan.
  const manual = await createEmptyProject(page, "Déclencheur manuel");
  await goSpace(page, "board");
  // Case pilotée par le projet : elle change à la réponse du serveur.
  const manualToggle = trigger.getByLabel(
    "Générer automatiquement au dépôt d'une photo",
  );
  await manualToggle.click();
  await expect(manualToggle).not.toBeChecked();
  await expect
    .poll(async () => {
      const p = (await (
        await page.request.get(`/api/projects/${manual}`)
      ).json()) as {
        render: { auto_on_drop: boolean };
      };
      return p.render.auto_on_drop;
    })
    .toBe(false);
  await dropInto(
    page,
    board.locator(".board-item.kind-frame.role-photos"),
    "piece.png",
    PNG,
  );
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 60_000,
  });
  await page.waitForTimeout(3000);
  await expect(plan).toContainText("Votre plan");
  await trigger.getByRole("button", { name: "Générer maintenant" }).click();
  await expect(plan.locator("img.board-plan")).toBeVisible({
    timeout: 180_000,
  });
  const m1 = await metrics(page, manual);
  expect(m1.generate_clicks).toBe(1);
  expect(m1.gestures_before_first_render).toBe(1);
  expect(outside).toEqual([]);
});
