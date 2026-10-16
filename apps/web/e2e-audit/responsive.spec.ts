import { expect, test, type Page } from "@playwright/test";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";

/** Audit responsive : chaque écran, à chaque largeur d'ordinateur, capturé et mesuré.
 *  Les mesures sont des indices (à confirmer à l'œil sur la capture), pas des verdicts. */

const LABEL = process.env.AUDIT_LABEL ?? "avant";
const DIR = path.resolve("e2e/screenshots/responsive", LABEL);
const FIXTURE = path.resolve("../../tests/fixtures/rect_room/image.png");
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const SIZES: [number, number][] = [
  [1920, 1080],
  [1536, 864],
  [1366, 768],
  [1280, 720],
  [1024, 768],
  [768, 1024],
];
const WORK: [number, number] = [1400, 900];

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface Metrics {
  viewport: { w: number; h: number };
  horizontalScroll: number;
  verticalScroll: number;
  overlaps: { a: string; b: string; area: number }[];
  offscreen: { what: string; box: Box }[];
  containerOverflow: { what: string; by: number }[];
  truncated: { what: string; text: string; hasTitle: boolean }[];
  smallTargets: { what: string; box: Box }[];
  smallText: { what: string; px: number }[];
}

interface Row extends Metrics {
  screen: string;
  width: number;
  file: string;
}

const rows: Row[] = [];

/* Exécuté dans la page. */
function collect(): Metrics {
  const visible = (el: Element): boolean => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return (
      r.width > 0 &&
      r.height > 0 &&
      s.visibility !== "hidden" &&
      s.display !== "none" &&
      Number(s.opacity) > 0.05
    );
  };
  const box = (el: Element): Box => {
    const r = el.getBoundingClientRect();
    return {
      x: Math.round(r.x),
      y: Math.round(r.y),
      w: Math.round(r.width),
      h: Math.round(r.height),
    };
  };
  const name = (el: Element): string => {
    const cls =
      typeof el.className === "string" && el.className
        ? "." + el.className.trim().split(/\s+/).slice(0, 2).join(".")
        : "";
    const label =
      el.getAttribute("aria-label") ??
      el.getAttribute("title") ??
      (el.textContent ?? "").trim().replace(/\s+/g, " ");
    return `${el.tagName.toLowerCase()}${cls} « ${label.slice(0, 48)} »`;
  };
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const zoneSelectors = [
    ".topbar",
    ".palette",
    ".bottombar",
    ".strip",
    ".side",
    ".pane-3d .pane-head",
    ".import-card",
    ".scale-card",
    ".error-banner",
  ];
  const zones: { sel: string; b: Box }[] = [];
  for (const sel of zoneSelectors)
    for (const el of Array.from(document.querySelectorAll(sel)))
      if (visible(el)) zones.push({ sel, b: box(el) });
  const overlaps: Metrics["overlaps"] = [];
  for (let i = 0; i < zones.length; i++)
    for (let j = i + 1; j < zones.length; j++) {
      const a = zones[i]!.b;
      const b = zones[j]!.b;
      const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (w > 1 && h > 1)
        overlaps.push({ a: zones[i]!.sel, b: zones[j]!.sel, area: w * h });
    }
  const offscreen: Metrics["offscreen"] = [];
  for (const z of zones)
    if (z.b.x < -1 || z.b.x + z.b.w > vw + 1 || z.b.y < -1)
      offscreen.push({ what: z.sel, box: z.b });
  const interactive = Array.from(
    document.querySelectorAll(
      "button, a[href], select, input:not([type=hidden]):not([type=file]), summary, textarea",
    ),
  ).filter((el) => visible(el) && !el.closest("svg"));
  for (const el of interactive) {
    const b = box(el);
    if (b.x + b.w > vw + 1 || b.x < -1)
      offscreen.push({ what: name(el), box: b });
  }
  const containerOverflow: Metrics["containerOverflow"] = [];
  for (const sel of [
    ".side",
    ".side-card",
    ".inspector",
    ".import-card",
    ".topbar",
    ".bottombar",
    ".palette",
    ".hero",
    ".projects",
    "main",
  ])
    for (const el of Array.from(document.querySelectorAll(sel))) {
      if (!visible(el)) continue;
      const by = el.scrollWidth - el.clientWidth;
      if (by > 1) containerOverflow.push({ what: sel, by });
    }
  const truncated: Metrics["truncated"] = [];
  for (const el of Array.from(
    document.querySelectorAll(
      "button, h1, h2, h3, strong, span, p, label, a, summary, td, th, li, code",
    ),
  )) {
    if (!visible(el) || el.closest("svg")) continue;
    const s = getComputedStyle(el);
    const clips =
      s.overflowX === "hidden" ||
      s.overflowX === "clip" ||
      s.textOverflow === "ellipsis";
    if (clips && el.scrollWidth > el.clientWidth + 1) {
      const text = (el.textContent ?? "").trim().replace(/\s+/g, " ");
      if (text)
        truncated.push({
          what: name(el),
          text: text.slice(0, 60),
          hasTitle: el.hasAttribute("title"),
        });
    }
  }
  const smallTargets: Metrics["smallTargets"] = [];
  for (const el of interactive) {
    const b = box(el);
    if (Math.min(b.w, b.h) < 24) smallTargets.push({ what: name(el), box: b });
  }
  const smallText: Metrics["smallText"] = [];
  const seen = new Set<string>();
  for (const el of Array.from(
    document.querySelectorAll("p, span, label, button, a, li, td, th, small"),
  )) {
    if (!visible(el) || el.closest("svg")) continue;
    const own = Array.from(el.childNodes).some(
      (n) => n.nodeType === Node.TEXT_NODE && (n.textContent ?? "").trim(),
    );
    if (!own) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 12) {
      const key = name(el);
      if (!seen.has(key)) {
        seen.add(key);
        smallText.push({ what: key, px });
      }
    }
  }
  return {
    viewport: { w: vw, h: vh },
    horizontalScroll: Math.max(
      0,
      document.documentElement.scrollWidth - vw,
      document.body.scrollWidth - vw,
    ),
    verticalScroll: Math.max(0, document.documentElement.scrollHeight - vh),
    overlaps,
    offscreen,
    containerOverflow,
    truncated: truncated.slice(0, 25),
    smallTargets: smallTargets.slice(0, 40),
    smallText: smallText.slice(0, 25),
  };
}

async function capture(
  page: Page,
  screen: string,
  options: { refit?: boolean; fullPage?: boolean } = {},
): Promise<void> {
  for (const [w, h] of SIZES) {
    await page.setViewportSize({ width: w, height: h });
    await page.waitForTimeout(350);
    if (options.refit) {
      const refit = page.getByRole("button", { name: "Recadrer" });
      if ((await refit.count()) > 0 && (await refit.first().isVisible()))
        await refit.first().click();
      await page.waitForTimeout(150);
    }
    const metrics = await page.evaluate(collect);
    const file = `${screen}-${w}.png`;
    await page.screenshot({
      path: path.join(DIR, file),
      fullPage: options.fullPage ?? false,
    });
    rows.push({ screen, width: w, file, ...metrics });
  }
  await page.setViewportSize({ width: WORK[0], height: WORK[1] });
  await page.waitForTimeout(250);
}

async function newProject(page: Page, name: string): Promise<void> {
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Créez votre PlanDesign" }),
  ).toBeVisible();
  await page.getByPlaceholder("Nom du nouveau projet").fill(name);
  await page.getByRole("button", { name: "Créer" }).click();
  await expect(page).toHaveURL(/\/import$/);
}

function summary(): string {
  const lines: string[] = [
    `# Audit responsive (${LABEL})`,
    "",
    "Mesures automatiques par écran et par largeur ; chaque ligne est un indice à confirmer sur",
    "la capture du même nom.",
    "",
    "| Écran | Largeur | Défil. horiz. | Chevauchements | Hors écran | Débord. conteneur | Tronqués (sans infobulle) | Cibles < 24 px | Texte < 12 px |",
    "|---|---|---|---|---|---|---|---|---|",
  ];
  for (const r of rows) {
    const trunc = r.truncated.filter((t) => !t.hasTitle).length;
    lines.push(
      `| ${r.screen} | ${r.width} | ${r.horizontalScroll || ""} | ${r.overlaps.map((o) => `${o.a}×${o.b}`).join(", ")} | ${r.offscreen.length || ""} | ${r.containerOverflow.map((c) => `${c.what}+${c.by}`).join(", ")} | ${trunc || ""} (${r.truncated.length}) | ${r.smallTargets.length || ""} | ${r.smallText.length || ""} |`,
    );
  }
  return lines.join("\n") + "\n";
}

test.beforeAll(() => {
  mkdirSync(DIR, { recursive: true });
});

test.afterAll(() => {
  writeFileSync(path.join(DIR, "audit.json"), JSON.stringify(rows, null, 1));
  writeFileSync(path.join(DIR, "audit.md"), summary());
});

test("audit responsive : tous les écrans, six largeurs", async ({ page }) => {
  // Un premier projet avec un long nom, pour que l'accueil ait une liste et un titre à tronquer.
  await newProject(
    page,
    "WhatsApp Image 2026-08-27 at 12.34.56 — grenier de la maison de campagne",
  );
  await capture(page, "02-importer-vide", { fullPage: true });
  if (existsSync(PLAN_DXF)) {
    await page
      .locator(".import-screen .dropzone input[type=file]")
      .setInputFiles(PLAN_DXF);
    // Les murs sont créés tout de suite ; « Ajouter une source » montre ce qui a été lu.
    await expect(page.locator("polygon.wall")).toHaveCount(4);
    await page.getByRole("button", { name: "Ajouter une source" }).click();
    await expect(page.getByText(/Plan DXF lu/)).toBeVisible();
    await capture(page, "03-importer-plan-dxf", { fullPage: true });
  }

  // Projet de travail : une pièce tracée, une photo, un meuble, des variantes, un rendu.
  await newProject(page, "Audit chambre");
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
  ] as [number, number][])
    await svg.click({ position: { x, y } });
  await page.keyboard.press("Escape");
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await page.getByRole("button", { name: "Détecter les pièces" }).click();
  await expect(page.locator("text.room-label")).toHaveCount(1);
  if (existsSync(FIXTURE)) {
    // La photo est comparée au plan toute seule : on attend la fin (vue ajoutée) pour que les
    // captures montrent l'état stable, bandeau « Plan estimé » compris.
    await page.locator('input[type="file"]').first().setInputFiles(FIXTURE);
    await expect(page.locator(".strip-thumb")).toHaveCount(1);
    await expect(page.locator("svg.plan circle.camera")).toHaveCount(1, {
      timeout: 180_000,
    });
    await expect(page.locator(".auto-banner:not(.estimated)")).toHaveCount(0, {
      timeout: 30_000,
    });
  }
  await page.keyboard.press("v");
  await svg.click({ position: { x: 150, y: 300 } });
  await capture(page, "04-editer-sans-selection", { refit: true });

  await page.getByRole("button", { name: "Recadrer" }).click();
  await page
    .locator("polygon.wall")
    .first()
    .click({ position: { x: 60, y: 4 } });
  await expect(page.getByRole("heading", { name: "Mur" })).toBeVisible();
  await capture(page, "05-editer-mur-selectionne", { refit: true });

  // Recalibrer : la carte ouverte avant les clics, puis après deux clics.
  await page
    .locator(".palette")
    .getByRole("button", { name: "Recalibrer", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "Recalibrer" })).toBeVisible();
  await capture(page, "14-recalibrer-carte", { refit: true });
  await page
    .getByRole("dialog", { name: "Recalibrer" })
    .getByRole("button", { name: "annuler" })
    .click();
  await page.keyboard.press("v");

  await page.getByRole("button", { name: "Meuble", exact: true }).click();
  await page.getByTitle("Meuble du catalogue").selectOption("desk_140");
  await capture(page, "06-meuble-catalogue", { refit: true });
  await page.getByRole("button", { name: "Ajouter au plan" }).click();
  await expect(page.locator("polygon.furniture")).toHaveCount(1);
  await page.keyboard.press("v");
  await page.locator("polygon.furniture").click();
  await expect(page.getByRole("heading", { name: /^Meuble :/ })).toBeVisible();
  await capture(page, "07-meuble-fiche", { refit: true });

  await page.getByRole("button", { name: "Aménager", exact: true }).click();
  await expect(page.getByRole("button", { name: "Écrire à l'assistant" })).toBeVisible();
  await capture(page, "08-amenager-1-souhait", { refit: true });
  await page.getByRole("button", { name: "+ ajouter un meuble" }).click();
  await page.getByRole("button", { name: "+ ajouter un meuble" }).click();
  await capture(page, "09-amenager-2-brief", { refit: true });
  await page.getByRole("button", { name: "Proposer des dispositions" }).click();
  await expect(page.getByText(/^Variante 1/)).toBeVisible();
  await page
    .getByRole("button", { name: "Avis sur cette variante" })
    .first()
    .click();
  await expect(page.locator(".remarks")).toBeVisible();
  const compare = page.getByRole("button", { name: "comparer", exact: true });
  if ((await compare.count()) > 0) await compare.first().click();
  await capture(page, "10-amenager-3-dispositions", { refit: true });
  await page.getByRole("button", { name: "Fermer Aménager" }).click();

  await page
    .getByRole("button", { name: "Paramètres avancés", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Assistant IA" }),
  ).toBeVisible();
  await capture(page, "11-parametres-avances", { refit: true });
  await page.getByRole("button", { name: "Fermer Paramètres avancés" }).click();

  await page.getByRole("button", { name: "Plan + 3D" }).click();
  await expect(page.locator("canvas")).toBeVisible();
  await page.waitForTimeout(1500);
  await capture(page, "12-plan-et-3d", { refit: true });
  await page.getByRole("button", { name: "Plan", exact: true }).click();

  await page
    .getByRole("button", { name: /Rendre/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Points de vue" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "+ Ajouter un point de vue" }).click();
  const viewpoint = page.locator(".viewpoints li");
  await expect(viewpoint).toHaveCount(1);
  const blender = (await (await page.request.get("/api/blender")).json()) as {
    available: boolean;
  };
  if (blender.available) {
    await page.getByLabel("Qualité").selectOption("16");
    await viewpoint
      .getByRole("button", { name: "Rendre", exact: true })
      .first()
      .click();
    await expect(page.locator(".gallery li")).toHaveCount(1, {
      timeout: 240_000,
    });
  }
  await page.waitForTimeout(800);
  await capture(page, "13-rendre-galerie");

  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Créez votre PlanDesign" }),
  ).toBeVisible();
  await expect(
    page.locator(".project-card, .projects li").first(),
  ).toBeVisible();
  await capture(page, "01-accueil", { fullPage: true });
});
