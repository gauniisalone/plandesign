import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { canRender, createEmptyProject, goSpace, NO_RENDER } from "./helpers";

/** File d'attente : une photo déposée pendant un calcul attend son tour au lieu d'être
 *  refusée ; le cadre Rendus le dit en une ligne, avec « Annuler ». Le serveur est simulé
 *  pour la seule tâche (aucun calcul lourd lancé) : l'ordre, le démarrage seul et le doublon
 *  sont couverts par les tests du serveur (tests/unit/test_job_queue.py). */
const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);
const QUEUED = "En attente : démarre dès que le calcul en cours se termine";

test("file d'attente : « En attente » dans le cadre Rendus, puis Annuler", async ({
  page,
}) => {
  // Sans rendus, le cadre Rendus dit « Blender n'est pas installé » au lieu de la file.
  test.skip(!(await canRender(page)), NO_RENDER);
  const jobId = "00000000-0000-4000-8000-0000000000f1";
  let status: "queued" | "cancelled" = "queued";
  const job = () => ({
    id: jobId,
    kind: "autoplan",
    status,
    queue_position: status === "queued" ? 1 : null,
    device: "cpu",
    expected_s: 60,
    elapsed_s: 0,
    message: "Construction du plan, environ 60 s",
    stage: "",
    result: null,
    error: null,
  });
  let autoplanCalls = 0;
  await page.route("**/api/projects/*/levels/*/images/*/autoplan*", (route) => {
    autoplanCalls += 1;
    return route.fulfill({ status: 202, json: job() });
  });
  await page.route(`**/api/projects/*/jobs/${jobId}`, (route) => {
    if (route.request().method() === "DELETE") status = "cancelled";
    return route.fulfill({ json: job() });
  });

  await createEmptyProject(page, "File d'attente");
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const box = (await photos.boundingBox())!;
  const dt = await page.evaluateHandle((data) => {
    const t = new DataTransfer();
    t.items.add(
      new File([new Uint8Array(data)], "piece.png", { type: "image/png" }),
    );
    return t;
  }, Array.from(PNG));
  await photos.dispatchEvent("drop", {
    dataTransfer: dt,
    clientX: box.x + box.width / 2,
    clientY: box.y + box.height / 2,
  });

  // Aucun refus, aucune erreur : la ligne d'attente dans le cadre Rendus, sans autre texte.
  const renders = board.locator(".board-item.kind-frame.role-renders");
  const waiting = renders.getByRole("status");
  await expect(waiting).toHaveText(`${QUEUED}Annuler`, { timeout: 30_000 });
  await expect(renders.getByText("Les rendus apparaîtront ici")).toHaveCount(0);
  await expect(
    board
      .locator(".board-item.kind-plan")
      .getByText("En attente", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(autoplanCalls).toBe(1);

  await waiting.getByRole("button", { name: "Annuler" }).click();
  await expect(renders.getByText(QUEUED)).toHaveCount(0);
  await expect(renders.getByText("Les rendus apparaîtront ici")).toBeVisible();
  await expect(
    board.locator(".board-item.kind-plan").getByRole("button", {
      name: "Générer le plan",
    }),
  ).toBeVisible();
});
