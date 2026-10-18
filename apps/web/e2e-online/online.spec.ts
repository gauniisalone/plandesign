import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { goSpace } from "../e2e/helpers";

/** MVP en ligne sans GPU (décision d'augus, 19 oct.) : ni Blender ni poids. Ce qui marche
 *  marche (plan depuis les exigences, import DXF, 3D, export), ce qui manque le dit en une
 *  ligne (« bientôt en ligne »), et AUCUN bouton ne mène à une erreur de calcul : pas de 409
 *  ni de 5xx, et jamais de chemin automatique ni de plan depuis une photo lancés. */

const PHOTO = path.resolve("../../tests/fixtures/rect_room/image.png");
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const skip = page.getByRole("button", { name: "Passer" });
  const home = page.getByRole("heading", { name: "Vos espaces de travail" });
  await expect(skip.or(home)).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible()) await skip.click();
  await expect(home).toBeVisible();
}

test("en ligne sans GPU : ce qui marche marche, rien ne mène à une erreur de calcul", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const failures: string[] = [];
  const compute: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (!p.startsWith("/api/")) return;
    if (r.status() === 409 || r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (
      /\/(autopilot|autoplan|render|inspire|finish|catalogue)(\/|$)/.test(p) &&
      r.method() === "POST"
    )
      compute.push(p);
  });
  await signIn(page, "marie@example.org");
  const origin = { Origin: new URL(page.url()).origin };

  // 1. Un espace neuf : les manques se disent en une ligne, pas de bouton voué à l'erreur.
  const a = (await (
    await page.request.post("/api/projects", {
      data: { name: "Chambre" },
      headers: origin,
    })
  ).json()) as { id: string };
  await page.goto(`/#/projects/${a.id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  await expect(
    board.getByText("Rendus réalistes : bientôt en ligne"),
  ).toBeVisible({
    timeout: 30_000,
  });
  await expect(
    board.getByText("Plan depuis photo : bientôt en ligne"),
  ).toBeVisible();
  const trigger = page.getByRole("group", { name: "Déclencheur" });
  await expect(
    trigger.getByRole("button", { name: "Générer maintenant" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Générer automatiquement au dépôt d'une photo"),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /Rendu contextualisé/ }),
  ).toHaveCount(0);

  // 2. « Générer le plan » depuis les exigences (règles, sans GPU) : le plan arrive, et aucun
  // chemin automatique ne part derrière.
  const text = page.getByLabel("Vos envies et contraintes");
  await text.fill("Une chambre de 12 m², lumineuse");
  await text.blur();
  await expect
    .poll(async () => {
      const p = (await (
        await page.request.get(`/api/projects/${a.id}`)
      ).json()) as {
        pending_instruction: string | null;
      };
      return p.pending_instruction ?? "";
    })
    .toContain("12 m²");
  await trigger.getByRole("button", { name: "Générer le plan" }).click();
  await expect(
    board.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({
    timeout: 60_000,
  });
  // Le plan est là : plus de bouton de génération (les rendus manquent).
  await expect(trigger.getByRole("button")).toHaveCount(0);

  // 3. Une photo déposée : rangée dans Photos, le plan ne se construit pas depuis elle.
  const photos = board.locator(".board-item.kind-frame.role-photos");
  const chooser = page.waitForEvent("filechooser");
  await photos.getByRole("button", { name: "Importer" }).click();
  await (await chooser).setFiles(PHOTO);
  await expect(board.locator(".board-item.kind-photo")).toHaveCount(1, {
    timeout: 30_000,
  });

  // 4. Un plan DXF importé dans un second espace : lu, sans chemin automatique derrière.
  const b = (await (
    await page.request.post("/api/projects", {
      data: { name: "Salon" },
      headers: origin,
    })
  ).json()) as { id: string };
  await page.goto(`/#/projects/${b.id}/board`);
  const photosB = page
    .getByRole("region", { name: "Espace de travail" })
    .locator(".board-item.kind-frame.role-photos");
  const chooserB = page.waitForEvent("filechooser");
  await photosB.getByRole("button", { name: "Importer" }).click();
  await (await chooserB).setFiles(PLAN_DXF);
  await expect(page.locator("polygon.wall")).toHaveCount(4, {
    timeout: 30_000,
  });

  // 5. La 3D du navigateur et l'export du plan.
  await goSpace(page, "3d");
  await expect(page.locator("canvas").first()).toBeVisible();
  await goSpace(page, "board");
  const plan = page.locator(".board-item.kind-plan img.board-plan").first();
  await expect(plan).toBeVisible({ timeout: 30_000 });
  const svg = await page.request.get((await plan.getAttribute("src")) ?? "");
  expect(svg.ok()).toBe(true);
  await page.waitForLoadState("networkidle");

  expect(failures).toEqual([]);
  expect(compute).toEqual([]);
});
