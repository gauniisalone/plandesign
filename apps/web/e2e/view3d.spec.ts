import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, goSpace, openRendersPane } from "./helpers";

/** E3 (aperçu 3D) : plan de coupe déplaçable, maison de poupée, rayons X, état mémorisé. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 650],
    [800, 650],
    [800, 350],
    [300, 350],
    [300, 650],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
}

test("aperçu 3D : coupe horizontale et verticale, maison de poupée, rayons X, mémorisés", async ({
  page,
}) => {
  // Aucune erreur dans la page (un uniforme manquant de three.js vidait la vue 3D).
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await createRoom(page, "E2E coupe 3D");
  await page.getByRole("button", { name: "Plan + 3D" }).click();
  const viewer = page.locator(".viewer3d");
  await expect(viewer.locator("canvas")).toBeVisible();
  await page.waitForTimeout(1500); // le GLB se charge
  const before = await viewer.screenshot();

  // Coupe horizontale, déplacée au curseur : l'image change.
  await page
    .getByRole("button", { name: "Coupe horizontale", exact: true })
    .click();
  await expect(viewer).toHaveAttribute("data-section", "z:1.20");
  await page.getByLabel("Position de la coupe").fill("0.6");
  await expect(viewer).toHaveAttribute("data-section", "z:0.60");
  await page.waitForTimeout(300);
  const cut = await viewer.screenshot();
  expect(cut.equals(before)).toBe(false);
  await page.screenshot({ path: path.join(SHOTS, "e3_coupe_horizontale.png") });

  // Coupe verticale.
  await page
    .getByRole("button", { name: "Coupe verticale", exact: true })
    .click();
  await expect(viewer).toHaveAttribute("data-section", /^x:/);
  await page.screenshot({ path: path.join(SHOTS, "e3_coupe_verticale.png") });
  // PDF de la coupe verticale cotée, même plan que l'aperçu.
  const pdfLink = page.getByRole("link", { name: "PDF de la coupe" });
  await expect(pdfLink).toBeVisible();
  const href = await pdfLink.getAttribute("href");
  expect(href).toContain("axis=x");
  const pdf = await page.request.get(href!);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toBe("application/pdf");

  // Maison de poupée : des murs tournés vers la caméra sont masqués.
  await page
    .getByRole("button", { name: "Maison de poupée", exact: true })
    .click();
  await expect
    .poll(async () => Number(await viewer.getAttribute("data-cutaway")))
    .toBeGreaterThanOrEqual(1);
  // Rayons X.
  await page.getByRole("button", { name: "Rayons X", exact: true }).click();
  await expect(viewer).toHaveAttribute("data-xray", "on");
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(SHOTS, "e3_rayons_x.png") });

  // Rechargé (l'atelier s'ouvre sur l'éditeur, B3) : les réglages de ce projet reviennent.
  await page.reload();
  await goSpace(page, "plan");
  await page.getByRole("button", { name: "Plan + 3D" }).click();
  await expect(
    page.getByRole("button", { name: "Coupe verticale", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Maison de poupée", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: "Rayons X", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".viewer3d")).toHaveAttribute("data-xray", "on");
  // Le rendu Blender suit le même plan de coupe.
  await openRendersPane(page);
  await expect(
    page.getByText(/Rendus en coupe \(verticale x = /),
  ).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "e3_rendre_en_coupe.png") });
  // E3b : la perspective de coupe s'ajoute aux points de vue, une seule fois.
  const sectionView = page.getByRole("button", { name: "Vue de coupe" });
  await sectionView.click();
  // Dans la liste des points de vue (le plan, caché sous l'espace de travail, porte aussi
  // ce nom dans son SVG).
  await expect(
    page
      .locator(".viewpoints")
      .getByText(/Coupe x = \d/)
      .first(),
  ).toBeVisible();
  await expect(sectionView).toBeDisabled();
  await page.screenshot({ path: path.join(SHOTS, "e3b_vue_de_coupe.png") });
  expect(errors).toEqual([]);
});
