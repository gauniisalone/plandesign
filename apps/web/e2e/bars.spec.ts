import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace, showPanel } from "./helpers";

/** Barres compactes (étape b) : raccourcis, infobulles, aucune fonction perdue, poignée du
 *  panneau, thème sombre sur le canevas, le plan, la 3D et les cartes. */

const SHOTS = path.resolve("e2e/screenshots");

async function createRoom(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await createEmptyProject(page, name);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  const svg = page.locator("svg.plan");
  await expect(svg).toBeVisible();
  await page.keyboard.press("w");
  for (const [x, y] of [
    [300, 600],
    [700, 600],
    [700, 350],
    [300, 350],
    [300, 600],
  ] as const)
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.keyboard.press("v");
}

const button = (page: Page, name: string) =>
  page.getByRole("button", { name, exact: true });

/** Nom accessible → raccourci annoncé dans l'infobulle. */
const SHORTCUTS: [string, string][] = [
  ["Sélection", "V"],
  ["Main", "H"],
  ["Mur", "W"],
  ["Détecter les pièces", "P"],
  ["Porte", "D"],
  ["Fenêtre", "F"],
  ["Escalier", "E"],
  ["Recalibrer", "K"],
  ["Meuble", "B"],
  ["Aménager", "A"],
  ["Assistant", "/"],
  ["Annuler", "Ctrl+Z"],
  ["Rétablir", "Ctrl+Y"],
  ["Supprimer la sélection", "Suppr"],
  ["Vue de dessus", "1"],
  ["Vue de face", "2"],
  ["Vue de côté", "3"],
  ["Vue iso", "4"],
  ["Coupe horizontale", "Maj+H"],
  ["Coupe verticale", "Maj+V"],
  ["Retourner la coupe", "Maj+R"],
  ["Masquer le toit", "R"],
  ["Rayons X", "X"],
  ["Maison de poupée", "Maj+D"],
  ["Plafonds", "C"],
  ["Matières", "M"],
  ["Zoom arrière", "-"],
  ["Zoom avant", "+"],
  ["Recadrer", "0"],
  ["Paramètres avancés", ","],
];

test("barres compactes : infobulles, raccourcis, aucune fonction perdue", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await createRoom(page, "E2E barres compactes");

  // Infobulles : chaque bouton à icône a son nom et son raccourci.
  for (const [name, key] of SHORTCUTS) {
    const b = button(page, name);
    await expect(b, name).toBeVisible();
    await expect(b, name).toHaveAttribute(
      "title",
      new RegExp(`^${name} \\(${key.replace(/[+/]/g, "\\$&")}\\)`),
    );
  }
  // Les bascules disent leur état.
  for (const name of [
    "Coupe horizontale",
    "Coupe verticale",
    "Rayons X",
    "Maison de poupée",
    "Plafonds",
    "Matières",
  ])
    await expect(button(page, name)).toHaveAttribute("aria-pressed", "false");
  await expect(button(page, "Masquer le toit")).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  // Aucune fonction perdue : photos, panneau, exports, lien, vues, boussole.
  for (const name of [
    "Ajouter une photo",
    // T7d1 : le panneau est fermé par défaut ; l'icône l'ouvre.
    "Afficher le panneau",
    "Exporter",
    "Copier le lien",
    "Plan",
    "Plan + 3D",
    "Ajouter une source",
  ])
    await expect(button(page, name), name).toBeVisible();
  // B3 : plus d'onglets ; dans le plan, « ← Tableau » et la 3D dans la barre des vues.
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(
    page
      .getByRole("navigation", { name: "Espaces du projet" })
      .getByRole("button", { name: "← Espace de travail" }),
  ).toBeVisible();
  await expect(button(page, "3D")).toBeVisible();
  await expectSpace(page, "plan");
  await button(page, "Exporter").click();
  for (const label of [
    "SVG",
    "PDF",
    "DXF",
    "GLB",
    "Archive",
    "JSON",
    "JSON à partager",
  ])
    await expect(
      page.locator(".menu-pop a", { hasText: new RegExp(`^${label}`) }).first(),
    ).toBeVisible();
  await button(page, "Exporter").click();
  await expect(page.locator("svg.plan .compass")).toBeVisible();
  await page.screenshot({ path: path.join(SHOTS, "barres_editer.png") });

  // Raccourcis de dessin et d'aménagement.
  await page.keyboard.press("p");
  await expect(page.locator("text.room-label")).toHaveCount(1);
  await page.keyboard.press("w");
  await expect(button(page, "Mur")).toHaveClass(/active/);
  await page.keyboard.press("v");
  await expect(button(page, "Sélection")).toHaveClass(/active/);
  await page.keyboard.press("b");
  await expect(page.locator(".side-head h2")).toHaveText("Meuble");
  await page.keyboard.press("e");
  await expect(
    page.getByRole("button", { name: "Circulation", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("a");
  await expect(page.locator(".side-head h2")).toHaveText("Aménager");
  await page.keyboard.press("/");
  await expect(page.locator(".side-head h2")).toHaveText("Assistant");
  await page.keyboard.press(",");
  await expect(page.locator(".side-head h2")).toHaveText("Paramètres avancés");
  await page.keyboard.press(",");
  await expect(page.locator(".side-head")).toHaveCount(0);
  await page.keyboard.press("k");
  await expect(button(page, "Recalibrer")).toHaveClass(/active/);
  await page.keyboard.press("v");
  const zoomValue = page.locator(".bottombar .zoom-value");
  const before = await zoomValue.textContent();
  await page.keyboard.press("Equal");
  await expect(zoomValue).not.toHaveText(before ?? "");
  await page.keyboard.press("0");

  // Supprimer : visible, actif dès qu'un élément est sélectionné, annulable.
  const remove = button(page, "Supprimer la sélection");
  await expect(remove).toBeDisabled();
  const wall = page.locator("polygon.wall").first();
  const box = await wall.boundingBox();
  expect(box).not.toBeNull();
  if (box)
    await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect(remove).toBeEnabled();
  await remove.click();
  await expect(page.locator("polygon.wall")).toHaveCount(3);
  await page.keyboard.press("Control+z");
  await expect(page.locator("polygon.wall")).toHaveCount(4);

  // Vues : un chiffre ouvre la 3D à côté du plan et place la caméra.
  const viewer = page.locator(".viewer3d");
  await page.keyboard.press("1");
  await expect(viewer).toBeVisible();
  await expect(button(page, "Plan + 3D")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(viewer).toHaveAttribute("data-preset", "top");
  await page.keyboard.press("3");
  await expect(viewer).toHaveAttribute("data-preset", "side");
  await page.keyboard.press("2");
  await expect(viewer).toHaveAttribute("data-preset", "front");
  await page.keyboard.press("4");
  await expect(viewer).toHaveAttribute("data-preset", "iso");
  // Coupes : Maj+H, Maj+V (axe X puis Y), Maj+R retourne.
  await page.keyboard.press("Shift+H");
  await expect(viewer).toHaveAttribute("data-section", /^z:/);
  await expect(page.getByLabel("Position de la coupe")).toBeVisible();
  await page.keyboard.press("Shift+V");
  await expect(viewer).toHaveAttribute("data-section", /^x:/);
  await expect(
    page.getByRole("link", { name: "PDF de la coupe" }),
  ).toBeVisible();
  await page.keyboard.press("Shift+R");
  await expect(viewer).toHaveAttribute("data-section", /^x:.*:flip$/);
  await page.keyboard.press("Shift+V");
  await expect(viewer).toHaveAttribute("data-section", /^y:/);
  await page.keyboard.press("Shift+V");
  await expect(viewer).toHaveAttribute("data-section", "none");
  await expect(button(page, "Retourner la coupe")).toBeDisabled();
  // Affichage : R, X, Maj+D, C, M.
  await page.keyboard.press("x");
  await expect(viewer).toHaveAttribute("data-xray", "on");
  await page.keyboard.press("x");
  await expect(viewer).toHaveAttribute("data-xray", "off");
  await page.keyboard.press("Shift+D");
  await expect(button(page, "Maison de poupée")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.keyboard.press("Shift+D");
  for (const [key, name, pressed] of [
    ["r", "Masquer le toit", "false"],
    ["c", "Plafonds", "true"],
    ["m", "Matières", "true"],
  ] as const) {
    await page.keyboard.press(key);
    await expect(button(page, name)).toHaveAttribute("aria-pressed", pressed);
  }
  await page.screenshot({ path: path.join(SHOTS, "barres_plan_3d.png") });
  // « Plan » referme la 3D.
  await button(page, "Plan").click();
  await expect(viewer).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("poignée du panneau : 300 px, 260 à 480 px, largeur retenue", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await createRoom(page, "E2E poignée du panneau");
  await showPanel(page);
  const side = page.locator(".side");
  const handle = page.getByRole("separator", { name: "Largeur du panneau" });
  const width = async () => Math.round((await side.boundingBox())!.width);
  await expect(handle).toHaveAttribute("aria-valuenow", "300");
  expect(await width()).toBe(300);

  // Glisser vers la gauche élargit ; la largeur est bornée à 480 px.
  const grip = (await handle.boundingBox())!;
  const x = grip.x + grip.width / 2;
  const y = grip.y + grip.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x - 100, y, { steps: 5 });
  await page.mouse.up();
  await expect(handle).toHaveAttribute("aria-valuenow", "400");
  expect(await width()).toBe(400);
  await page.mouse.move(x - 100, y);
  await page.mouse.down();
  await page.mouse.move(x - 400, y, { steps: 5 });
  await page.mouse.up();
  await expect(handle).toHaveAttribute("aria-valuenow", "480");

  // Retenue après rechargement (le panneau, fermé au départ, se rouvre à sa largeur).
  await page.reload();
  await showPanel(page);
  await expect(handle).toHaveAttribute("aria-valuenow", "480");
  expect(await width()).toBe(480);

  // Clavier : flèches, Début (260), double-clic : largeur par défaut.
  await handle.focus();
  await page.keyboard.press("ArrowRight");
  await expect(handle).toHaveAttribute("aria-valuenow", "470");
  await page.keyboard.press("Home");
  await expect(handle).toHaveAttribute("aria-valuenow", "260");
  await handle.dblclick();
  await expect(handle).toHaveAttribute("aria-valuenow", "300");

  // 1920 px de large : 340 px par défaut.
  await page.setViewportSize({ width: 1920, height: 1080 });
  await expect(handle).toHaveAttribute("aria-valuenow", "340");
  await page.screenshot({ path: path.join(SHOTS, "barres_1920.png") });
});

test("thème sombre : canevas, plan, 3D et cartes", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 1280, height: 800 });
  await createRoom(page, "E2E thème sombre");
  /** Clarté entre 0 et 1 d'une couleur calculée : rgb(…), color(srgb …), oklch/oklab(…). */
  const lightness = (css: string) => {
    const m = css.match(/\d*\.?\d+/g)!.map(Number);
    if (/^okl(ch|ab)/.test(css)) return m[0]! > 1 ? m[0]! / 100 : m[0]!;
    const scale = css.startsWith("color(") ? 1 : 255;
    return (m[0]! + m[1]! + m[2]!) / (3 * scale);
  };
  for (const sel of [".canvas-wrap", ".side-card", ".topbar", ".palette"]) {
    const bg = await page
      .locator(sel)
      .first()
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(lightness(bg), `${sel} : ${bg}`).toBeLessThan(0.35);
  }
  // Murs du plan clairs sur fond sombre.
  const wallFill = await page
    .locator("polygon.wall")
    .first()
    .evaluate((el) => getComputedStyle(el).fill);
  expect(lightness(wallFill)).toBeGreaterThan(0.6);
  // Fond de la 3D sombre (pixel du coin de l'image rendue).
  await page.keyboard.press("4");
  const viewer = page.locator(".viewer3d canvas");
  await expect(viewer).toBeVisible();
  await page.waitForTimeout(800);
  const pixel = await viewer.evaluate((c: HTMLCanvasElement) => {
    const copy = document.createElement("canvas");
    copy.width = c.width;
    copy.height = c.height;
    const ctx = copy.getContext("2d")!;
    ctx.drawImage(c, 0, 0);
    return Array.from(ctx.getImageData(4, 4, 1, 1).data);
  });
  expect((pixel[0]! + pixel[1]! + pixel[2]!) / 765).toBeLessThan(0.3);
  await page.screenshot({ path: path.join(SHOTS, "barres_sombre.png") });
  // Rendus : la carte du panneau et le fond des images restent sombres.
  await goSpace(page, "renders");
  for (const sel of [".side-card", ".renders-stage"]) {
    const bg = await page
      .locator(sel)
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(lightness(bg), `${sel} : ${bg}`).toBeLessThan(0.35);
  }
  await page.screenshot({ path: path.join(SHOTS, "barres_sombre_rendre.png") });
});
