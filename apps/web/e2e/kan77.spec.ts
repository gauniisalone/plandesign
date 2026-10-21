import { expect, test, type Locator, type Page } from "@playwright/test";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";

/** KAN-77 : harmonisation de l'interface, mesurée dans le navigateur à 1280 × 800, en clair et
 *  en sombre. Couleurs du site (aucune requête externe, Outfit chargée), arrondis, boutons
 *  (12 × 24 px, 16 px entre voisins, même axe, secondaire visible), cartes (16 px au moins
 *  autour de l'icône de réglage de Rendus), icônes de la barre de gauche centrées à 1 px près,
 *  grille de points à 5–10 %, aucun libellé coupé ni débordant, texte des panneaux ≥ 13 px,
 *  post-it remesuré après le chargement des polices. MH10 : « Ajouter un lien » (cadre
 *  Inspirations), « Importer un plan » (cadre Plan), coller une adresse. Les gardes de
 *  index.css (couleurs interdites, arrondis, contraste) sont dans unit/kan77-styles.test.ts. */

test.use({ viewport: { width: 1280, height: 800 } });

type Box = { x: number; y: number; width: number; height: number };

/** Captures pour le compte rendu, seulement si E2E_CAPTURE_DIR est défini. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function box(l: Locator): Promise<Box> {
  const b = await l.boundingBox();
  if (!b) throw new Error("élément sans boîte");
  return b;
}

function overlap(a: Box, b: Box): boolean {
  return (
    a.x < b.x + b.width - 0.5 &&
    b.x < a.x + a.width - 0.5 &&
    a.y < b.y + b.height - 0.5 &&
    b.y < a.y + a.height - 0.5
  );
}

async function noOverlap(items: Locator): Promise<void> {
  const boxes = await Promise.all((await items.all()).map(box));
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++)
      expect(overlap(boxes[i]!, boxes[j]!), `chevauchement ${i}/${j}`).toBe(
        false,
      );
}

/** Libellés coupés sur deux lignes, débordants ou sortant de leur panneau ; texte des
 *  panneaux (hors monde de l'espace de travail, qui suit le zoom) sous 13 px. */
async function audit(page: Page) {
  return page.evaluate(() => {
    const PANELS =
      ".side, .topbar, .board-rail, .board-context, .board-zoom, .board-zone-inner, .board-bottom";
    const scope = [
      ...document.querySelectorAll(PANELS),
      ...document.querySelectorAll(
        ".board-frame-head, .board-plan-actions, .board-plan-head, .board-frame-import-row",
      ),
    ];
    const name = (el: Element) =>
      `${el.tagName.toLowerCase()}.${[...el.classList].join(".")} « ${(el.textContent ?? "").trim().slice(0, 40)} »`;
    const visible = (el: Element) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && s.visibility !== "hidden";
    };
    const texts = (el: Element) =>
      [...el.childNodes].filter(
        (n) => n.nodeType === 3 && (n.textContent ?? "").trim(),
      );
    const small: string[] = [];
    const cut: string[] = [];
    const seen = new Set<Element>();
    for (const root of scope)
      for (const el of [root, ...root.querySelectorAll("*")]) {
        if (seen.has(el) || !visible(el) || el.closest(".sr-only")) continue;
        seen.add(el);
        const own = texts(el);
        if (!own.length) continue;
        const s = getComputedStyle(el);
        if (!el.closest(".board-world") && parseFloat(s.fontSize) < 12.95)
          small.push(`${name(el)} : ${s.fontSize}`);
        if (
          s.overflow !== "visible" &&
          s.textOverflow !== "ellipsis" &&
          el.scrollWidth > el.clientWidth + 1 &&
          el.clientWidth > 1
        )
          cut.push(`${name(el)} : déborde`);
        const panel = el.closest(".side, .topbar, .board-rail, .board-context");
        if (
          panel &&
          el.getBoundingClientRect().right >
            panel.getBoundingClientRect().right + 1
        )
          cut.push(`${name(el)} : sort du panneau`);
        if (
          ["BUTTON", "LABEL", "STRONG", "A", "SPAN"].includes(el.tagName) &&
          s.whiteSpace !== "pre-wrap"
        ) {
          const lines = new Set<number>();
          for (const n of own) {
            const r = document.createRange();
            r.selectNodeContents(n);
            for (const rect of r.getClientRects())
              lines.add(Math.round(rect.top / 4));
          }
          if (lines.size > 1 && el.tagName !== "SPAN")
            cut.push(`${name(el)} : ${lines.size} lignes`);
        }
      }
    // Les boutons posés dans une carte restent dans sa boîte (aucun ne déborde du cadre).
    for (const b of document.querySelectorAll(
      ".board-frame-import-row button, .board-plan-actions button, .board-frame-actions button",
    )) {
      if (!visible(b)) continue;
      const card = b.closest(".board-item")!.getBoundingClientRect();
      const r = b.getBoundingClientRect();
      if (
        r.left < card.left - 0.5 ||
        r.right > card.right + 0.5 ||
        r.bottom > card.bottom + 0.5
      )
        cut.push(`${name(b)} : sort de sa carte`);
    }
    return { small, cut };
  });
}

async function emptyBoard(page: Page, name: string) {
  const outside: string[] = [];
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (
      !["127.0.0.1", "localhost"].includes(u.hostname) &&
      u.protocol !== "data:"
    )
      outside.push(u.hostname);
  });
  const id = await createEmptyProject(page, name);
  const layout = (await (
    await page.request.get(`/api/projects/${id}/board`)
  ).json()) as {
    items: { x: number; y: number; w: number; h: number }[];
  };
  const right = Math.max(...layout.items.map((i) => i.x + i.w));
  const top = Math.min(...layout.items.map((i) => i.y));
  await page.request.patch(`/api/projects/${id}/board/items`, {
    data: {
      set: [
        {
          id: crypto.randomUUID(),
          kind: "note",
          x: right + 60,
          y: top,
          w: 200,
          h: 200,
          text: "Garder les poutres apparentes",
          color: "yellow",
        },
      ],
    },
  });
  await goSpace(page, "board");
  const region = page.getByRole("region", { name: "Espace de travail" });
  await expect(region.locator(".board-item.kind-plan")).toBeVisible();
  await page.evaluate(() => document.fonts.ready);
  return { id, region, outside };
}

for (const scheme of ["light", "dark"] as const) {
  test(`KAN-77 (${scheme === "light" ? "clair" : "sombre"}) : mesures de l'espace de travail à 1280 × 800`, async ({
    page,
  }) => {
    await page.emulateMedia({ colorScheme: scheme });
    const { region, outside } = await emptyBoard(
      page,
      `Harmonisation ${scheme}`,
    );

    // MH1, MH9 : couleurs du site, Outfit chargée et utilisée, aucune requête externe.
    const generate = page.getByRole("button", { name: "Générer maintenant" });
    expect(
      await generate.evaluate((e) => getComputedStyle(e).backgroundColor),
    ).toBe("rgb(166, 58, 46)");
    expect(
      await page.evaluate(() => getComputedStyle(document.body).fontFamily),
    ).toMatch(/^"?Outfit/);
    expect(await page.evaluate(() => document.fonts.check("16px Outfit"))).toBe(
      true,
    );

    // MH3 : principal en 12 × 24 px, texte centré.
    const pad = await generate.evaluate((e) => {
      const s = getComputedStyle(e);
      return [
        s.paddingTop,
        s.paddingRight,
        s.paddingBottom,
        s.paddingLeft,
        s.justifyContent,
        s.alignItems,
      ];
    });
    expect(pad).toEqual(["12px", "24px", "12px", "24px", "center", "center"]);

    // MH3 : les boutons du cadre Plan, 16 px entre voisins, sur le même axe, sans
    // chevauchement, dans leur carte ; le secondaire a un container visible.
    const plan = region.locator(".board-item.kind-plan");
    const actions = plan.locator(".board-plan-actions button");
    await expect(actions).toHaveCount(3);
    await noOverlap(actions);
    const boxes = await Promise.all((await actions.all()).map(box));
    const planBox = await box(plan);
    const stacked = Math.abs(boxes[0]!.y - boxes[1]!.y) > 2;
    for (let i = 0; i + 1 < boxes.length; i++) {
      const [a, b] = [boxes[i]!, boxes[i + 1]!];
      if (stacked) {
        expect(
          Math.abs(a.x + a.width / 2 - (b.x + b.width / 2)),
        ).toBeLessThanOrEqual(1);
        expect(Math.abs(b.y - (a.y + a.height) - 16)).toBeLessThanOrEqual(1.5);
      } else {
        expect(
          Math.abs(a.y + a.height / 2 - (b.y + b.height / 2)),
        ).toBeLessThanOrEqual(1);
        expect(Math.abs(b.x - (a.x + a.width) - 16)).toBeLessThanOrEqual(1.5);
      }
    }
    for (const b of boxes) {
      expect(b.x).toBeGreaterThanOrEqual(planBox.x);
      expect(b.x + b.width).toBeLessThanOrEqual(planBox.x + planBox.width);
      expect(b.y + b.height).toBeLessThanOrEqual(planBox.y + planBox.height);
    }
    const trace = actions.filter({ hasText: "Tracer moi-même" });
    const [traceBg, cardBg, edge] = await trace.evaluate((e) => {
      const s = getComputedStyle(e);
      const card = getComputedStyle(e.closest(".board-item")!);
      return [s.backgroundColor, card.backgroundColor, s.borderTopColor];
    });
    expect(traceBg).not.toBe(cardBg);
    expect(edge).not.toBe(cardBg);

    // MH4 : l'icône de réglage de la carte Rendus et le titre restent à 16 px des bords.
    const renders = region.locator(".board-item.kind-frame.role-renders");
    const card = await box(renders);
    const icon = await box(
      renders.getByRole("button", { name: "Réglages et galerie" }),
    );
    expect(card.x + card.width - (icon.x + icon.width)).toBeGreaterThanOrEqual(
      15.5,
    );
    expect(icon.y - card.y).toBeGreaterThanOrEqual(15.5);
    const title = await box(renders.locator(".board-frame-name"));
    expect(title.x - card.x).toBeGreaterThanOrEqual(15.5);

    // MH5, MH11 : icônes au trait de la barre de gauche, centrées à 1 px près (la boîte du
    // SVG et celle du tracé), même taille et même trait partout.
    const rail = page.getByRole("toolbar", {
      name: "Outils de l'espace de travail",
    });
    const buttons = rail.getByRole("button");
    await expect(buttons).toHaveCount(7);
    await noOverlap(buttons);
    for (const b of await buttons.all()) {
      const m = await b.evaluate((el) => {
        const svg = el.querySelector("svg")!;
        const r = el.getBoundingClientRect();
        const s = svg.getBoundingClientRect();
        const g = svg.getBBox();
        const k = s.width / 24;
        return {
          emoji: (el.textContent ?? "").trim(),
          dx: s.x + s.width / 2 - (r.x + r.width / 2),
          dy: s.y + s.height / 2 - (r.y + r.height / 2),
          px: (g.x + g.width / 2 - 12) * k,
          py: (g.y + g.height / 2 - 12) * k,
          size: [Math.round(s.width), Math.round(s.height)],
          stroke: svg.getAttribute("stroke-width"),
        };
      });
      expect(m.emoji).toBe("");
      expect(Math.abs(m.dx)).toBeLessThanOrEqual(1);
      expect(Math.abs(m.dy)).toBeLessThanOrEqual(1);
      expect(Math.abs(m.px)).toBeLessThanOrEqual(1);
      expect(Math.abs(m.py)).toBeLessThanOrEqual(1);
      expect(m.size).toEqual([20, 20]);
      expect(m.stroke).toBe("1.8");
    }
    // Annuler et Rétablir à part, en bas, après le séparateur.
    const sep = await box(rail.locator(".board-sep"));
    const undo = await box(
      rail.getByRole("button", { name: "Annuler dans l'espace de travail" }),
    );
    const comment = await box(
      rail.getByRole("button", { name: "Commentaire", exact: true }),
    );
    expect(comment.y).toBeLessThan(sep.y);
    expect(undo.y).toBeGreaterThan(sep.y);

    // MH6 : grille de points à 5–10 %.
    const alpha = await page.evaluate(() => {
      const v = getComputedStyle(document.documentElement).getPropertyValue(
        "--grid-dot",
      );
      const m = /\/\s*([\d.]+)\s*\)/.exec(v);
      return m ? Number(m[1]) : NaN;
    });
    expect(alpha).toBeGreaterThanOrEqual(0.05);
    expect(alpha).toBeLessThanOrEqual(0.1);

    // MH9 : le post-it remesuré après le chargement d'Outfit : « apparentes » sur une ligne.
    const word = await region
      .locator(".board-item.kind-note .postit-text")
      .evaluate((el) => {
        const node = el.firstChild!;
        const text = node.textContent ?? "";
        const start = text.indexOf("apparentes");
        const r = document.createRange();
        r.setStart(node, start);
        r.setEnd(node, start + "apparentes".length);
        return new Set([...r.getClientRects()].map((x) => Math.round(x.top)))
          .size;
      });
    expect(word).toBe(1);

    // MH9 : aucun libellé coupé ni débordant, texte des panneaux ≥ 13 px (sans sélection,
    // puis avec un post-it choisi : la palette et les actions dans le panneau).
    expect(await audit(page)).toEqual({ small: [], cut: [] });
    const theme = scheme === "light" ? "clair" : "sombre";
    await shot(page, `kan77-1280x800-${theme}-espace`);
    await region.locator(".board-item.kind-note").click();
    const context = page.getByRole("toolbar", {
      name: "Actions de la sélection",
    });
    await expect(context).toBeVisible();
    await noOverlap(context.getByRole("button"));
    expect(await audit(page)).toEqual({ small: [], cut: [] });
    await shot(page, `kan77-1280x800-${theme}-selection`);

    expect(outside).toEqual([]);
  });
}

test("KAN-77 : captures à 1920 × 1080 (clair, sombre), sans libellé coupé", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const { region } = await emptyBoard(page, "Harmonisation 1920");
  for (const scheme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await page.waitForTimeout(300);
    expect(await audit(page)).toEqual({ small: [], cut: [] });
    await shot(
      page,
      `kan77-1920x1080-${scheme === "light" ? "clair" : "sombre"}-espace`,
    );
  }
  await region.locator(".board-item.kind-note").click();
  expect(await audit(page)).toEqual({ small: [], cut: [] });
  await shot(page, "kan77-1920x1080-sombre-selection");
});

test("KAN-77 (MH10) : Ajouter un lien dans Inspirations, Importer un plan, coller une adresse", async ({
  page,
}) => {
  const { id, region } = await emptyBoard(page, "Lien et import");
  const inspirations = region.locator(
    ".board-item.kind-frame.role-inspirations",
  );
  await inspirations.getByRole("button", { name: "Ajouter un lien" }).click();
  const form = region.getByRole("dialog", { name: "Ajouter un lien" });
  await form.getByLabel("Adresse").fill("https://www.exemple.fr/lampe");
  await form.getByLabel("Titre").fill("Lampe en lin");
  await form.getByRole("button", { name: "Ajouter le lien" }).click();
  const link = region.locator(".board-item.kind-link", {
    hasText: "Lampe en lin",
  });
  await expect(link).toBeVisible();
  // Le lien est rangé dans le cadre Inspirations (il en fait partie, côté serveur aussi).
  await expect
    .poll(async () => {
      const board = (await (
        await page.request.get(`/api/projects/${id}/board`)
      ).json()) as {
        items: {
          id: string;
          kind: string;
          role?: string | null;
          frame?: string | null;
          text: string;
        }[];
      };
      const frame = board.items.find((i) => i.role === "inspirations");
      return board.items.find((i) => i.kind === "link")?.frame === frame?.id;
    })
    .toBe(true);

  // « Importer un plan (DXF, PDF, image) » : le même chemin que l'ancien menu de la barre.
  await region
    .locator(".board-item.kind-plan")
    .getByRole("button", { name: /Importer un plan/ })
    .click();
  await expectSpace(page, "source");
  await expect(
    page.getByRole("heading", { name: "Ajouter une source" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Ajouter une source/ }).click();
  await goSpace(page, "board");

  // Coller une adresse marche toujours (sans bouton).
  await page.evaluate(() => {
    const dt = new DataTransfer();
    dt.setData("text/plain", "https://www.exemple.fr/tapis");
    window.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt }));
  });
  await expect(
    region.locator(".board-item.kind-link", { hasText: "www.exemple.fr" }),
  ).toHaveCount(2);
});
