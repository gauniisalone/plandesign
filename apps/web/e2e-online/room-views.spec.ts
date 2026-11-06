import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
import { RENDER_ACCOUNT_KAN137 } from "./render-account";

/** KAN-137 : dans le node Rendus, « Proposer une vue par pièce » pose une vue calculée par
 *  pièce du plan, puis « Rendre toutes les pièces (n) » envoie une demande par pièce cochée
 *  au poste de rendu (hors ligne ici : aucun GPU), chacune décomptée du quota du jour.
 *  KAN-134 : « Réglages des rendus » (focale, tirages) et « Rendre » de l'Assistant. */

async function signIn(page: Page, email: string, ip: string): Promise<void> {
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": ip });
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByLabel("J'accepte les conditions d'utilisation").check();
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

/** Deux pièces séparées (4 × 3 m et 3 × 3 m), détectées, sans point de vue. */
async function twoRooms(page: Page): Promise<{ id: string; level: string }> {
  const origin = { Origin: new URL(page.url()).origin };
  const created = await page.request.post("/api/projects", {
    data: { name: "Deux pièces" },
    headers: origin,
  });
  expect(created.status()).toBe(201);
  const project = (await created.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(project.levels)[0]!;
  const ops = `/api/projects/${project.id}/levels/${level}/ops`;
  const rooms = [
    [
      [0, 0],
      [4, 0],
      [4, 3],
      [0, 3],
    ],
    [
      [6, 0],
      [9, 0],
      [9, 3],
      [6, 3],
    ],
  ];
  for (const corners of rooms) {
    for (let i = 0; i < 4; i++) {
      const [a, b] = [corners[i]!, corners[(i + 1) % 4]!];
      const wall = await page.request.post(ops, {
        data: {
          op: "add_wall",
          start: { x: a[0], y: a[1] },
          end: { x: b[0], y: b[1] },
        },
        headers: origin,
      });
      expect(wall.ok()).toBe(true);
    }
  }
  const detected = await page.request.post(ops, {
    data: { op: "detect_rooms" },
    headers: origin,
  });
  expect(detected.ok()).toBe(true);
  return { id: project.id, level };
}

test("vues par pièce : proposer, puis rendre toutes les pièces (une demande chacune)", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const failures: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  await signIn(page, RENDER_ACCOUNT_KAN137.email, "198.51.100.236");
  const { id, level } = await twoRooms(page);
  const usedBefore = (
    (await (await page.request.get("/api/realistic/status")).json()) as {
      used_today: number;
    }
  ).used_today;
  await gotoApp(page, `/#/projects/${id}/board`);
  const board = page.getByRole("region", { name: "Espace de travail" });
  const frame = board.getByRole("region", { name: "Rendus réalistes" });

  // Rien n'est calculé à l'ouverture : on le propose.
  const propose = frame.getByRole("button", { name: "Proposer une vue par pièce" });
  await expect(propose).toBeEnabled({ timeout: 30_000 });
  await propose.click();
  const rooms = frame.getByRole("region", { name: "Une vue par pièce" });
  await expect(rooms.getByRole("checkbox")).toHaveCount(2);
  await expect(rooms.getByRole("checkbox", { checked: true })).toHaveCount(2);
  await expect(rooms).toContainText("du sol visible");
  await expect(propose).toHaveCount(0);

  // Les vues sont des points de vue du plan, une par pièce.
  const stored = (await (await page.request.get(`/api/projects/${id}`)).json()) as {
    levels: Record<
      string,
      { elements: Record<string, { id: string; type: string; metadata?: Record<string, unknown> }> }
    >;
  };
  const cams = Object.values(stored.levels[level]!.elements).filter(
    (e) => e.type === "camera" && typeof e.metadata?.room_view === "string",
  );
  expect(cams).toHaveLength(2);

  // « Rendre toutes les pièces (2) » : deux demandes, deux rendus du quota.
  await frame.getByRole("button", { name: "Rendre toutes les pièces (2)" }).click();
  await expect
    .poll(async () => {
      const listed = (await (
        await page.request.get(`/api/projects/${id}/realistic`)
      ).json()) as { requests: { camera_id: string }[] };
      return listed.requests.map((r) => r.camera_id).sort();
    })
    .toEqual(cams.map((c) => c.id).sort());
  const usedAfter = (
    (await (await page.request.get("/api/realistic/status")).json()) as {
      used_today: number;
    }
  ).used_today;
  expect(usedAfter - usedBefore).toBe(2);

  // Une case décochée : la pièce ne part plus dans le lot.
  await rooms.getByRole("checkbox").first().uncheck();
  await expect(
    frame.getByRole("button", { name: "Rendre toutes les pièces (1)" }),
  ).toBeVisible();

  // KAN-141 : « Modifier » une vue (focale au maximum, 35 mm), puis « Recalculer ».
  const row = rooms.getByRole("listitem").first();
  await row.getByRole("button", { name: /^Modifier la vue : / }).click();
  const panel = row.getByRole("group", { name: /^Modifier la vue : / });
  await expect(panel).toContainText("vue calculée");
  await expect(panel.getByRole("button", { name: "Recalculer" })).toBeDisabled();
  const focal = panel.getByRole("slider", { name: /^Focale \(équivalent\)/ });
  await focal.focus();
  await focal.press("End");
  await expect(row).toContainText("modifiée à la main", { timeout: 15_000 });
  await expect(row).toContainText("35 mm");
  await expect(panel).toContainText("vue modifiée à la main");
  await panel.getByRole("button", { name: "Recalculer" }).click();
  await expect(row).toContainText("du sol visible", { timeout: 15_000 });
  await expect(panel.getByRole("button", { name: "Recalculer" })).toBeDisabled();

  // KAN-134 : l'icône curseurs ouvre « Réglages des rendus » dans le node (en ligne).
  const head = board.locator(".board-item.kind-frame.role-renders .board-frame-actions");
  const sliders = head.getByRole("button", { name: "Réglages des rendus" });
  await expect(sliders).toHaveAttribute("data-tip", /ambiance, focale, nombre de rendus/);
  await sliders.click();
  const settings = frame.getByRole("region", { name: "Réglages des rendus" });
  await expect(settings).toBeVisible();
  // Focale imposée : les vues calculées sont recalculées à 30 mm, leur note reste.
  await settings.getByLabel("Focale des vues de pièce").selectOption("30");
  await expect(rooms.getByRole("listitem").first()).toContainText("30 mm", {
    timeout: 15_000,
  });
  await expect(rooms.getByRole("listitem").first()).toContainText("du sol visible");
  // Deux tirages par pièce : le compteur le dit AVANT le clic (1 pièce cochée × 2).
  await settings.getByLabel("Nombre de rendus par pièce").selectOption("2");
  await expect(frame.locator(".rgal-quota")).toContainText(
    `2 rendus seront pris · ${usedAfter} sur`,
  );
  // Gardés dans le navigateur : rouverts, les réglages sont là.
  await sliders.click();
  await expect(settings).toHaveCount(0);
  await sliders.click();
  await expect(settings.getByLabel("Nombre de rendus par pièce")).toHaveValue("2");

  // L'Assistant : « Rendre » applique le texte à l'ambiance, puis 2 tirages de la pièce cochée.
  const composer = page.getByRole("region", { name: "Compositeur", exact: true });
  await expect(
    composer.getByRole("button", { name: "Nouvelle variante → assistant" }),
  ).toHaveCount(0);
  await composer.getByLabel("Vos envies et contraintes").fill("plus chaleureux, du bois clair");
  const renderBtn = composer
    .getByRole("group", { name: "Déclencheur" })
    .getByRole("button", { name: "Rendre", exact: true });
  await expect(renderBtn).toHaveAccessibleDescription(/2 rendus seront pris/);
  await renderBtn.click();
  await expect(composer.locator(".board-composer-said")).toContainText(
    "2 rendus demandés",
    { timeout: 30_000 },
  );
  await expect
    .poll(async () => {
      const s = (await (await page.request.get("/api/realistic/status")).json()) as {
        used_today: number;
      };
      return s.used_today - usedBefore;
    })
    .toBe(4);
  expect(failures).toEqual([]);
});
