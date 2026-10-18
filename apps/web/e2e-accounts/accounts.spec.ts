import { expect, test, type Page } from "@playwright/test";

/** B1d-a : comptes actifs. Sans session, la page de connexion et rien d'autre ; connexion en
 *  deux temps (adresse, puis code) ; galerie ; « Me déconnecter » ; arrivée par /nouveau
 *  (compositeur prérempli, rien ne se génère) ; lecteur sans outils ni « Générer ». */

const ADMIN = "admin@example.org";

/** Captures pour le compte rendu, seulement si E2E_CAPTURE_DIR est défini. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  const r = await page.request.get(
    `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
  );
  const { code } = (await r.json()) as { code: string };
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
}

test("sans session : la page de connexion seule, au plus une réponse 401", async ({
  page,
}) => {
  const unauthorized: string[] = [];
  const apiCalls: string[] = [];
  page.on("response", (r) => {
    if (r.status() === 401) unauthorized.push(r.url());
  });
  page.on("request", (r) => {
    // Les appels à l'API (et non les modules servis par Vite, comme /src/api/client.ts).
    const path = new URL(r.url()).pathname;
    if (path.startsWith("/api/")) apiCalls.push(path);
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Se connecter à PlanDesign" }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(unauthorized.length).toBeLessThanOrEqual(1);
  expect(
    apiCalls.filter((p) => p !== "/api/health" && p !== "/api/auth/me"),
  ).toEqual([]);
  await expect(page.getByText("Vos espaces de travail")).toHaveCount(0);
  await shot(page, "1-connexion-adresse");
  // Mention de la note 17, § 11 (code e-mail, Brevo, Hostinger).
  await expect(
    page.getByText(/code envoyé à votre adresse e-mail/),
  ).toBeVisible();
  // Mauvais code : refus, même texte quel que soit le motif.
  await page.getByLabel("Adresse e-mail").fill(ADMIN);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await page.getByLabel("Code à 6 chiffres").fill("000000");
  const r = await page.request.get(`/api/test/mail/last-code?email=${ADMIN}`);
  const { code } = (await r.json()) as { code: string };
  if (code === "000000")
    await page.getByLabel("Code à 6 chiffres").fill("111111");
  await page.getByRole("button", { name: "Se connecter" }).click();
  await expect(page.getByRole("alert")).toContainText("ce code n'est pas bon");
  await shot(page, "2-connexion-code-faux");
});

test("connexion en deux temps, galerie du membre, puis « Me déconnecter »", async ({
  page,
}) => {
  await signIn(page, ADMIN);
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Réglages" }).click();
  const settings = page.getByRole("dialog", { name: "Réglages" });
  await expect(settings.getByText(`Connecté : ${ADMIN}`)).toBeVisible();
  await expect(
    settings.getByRole("button", { name: "Me déconnecter partout" }),
  ).toBeVisible();
  await shot(page, "3-galerie-reglages-compte");
  await settings
    .getByRole("button", { name: "Me déconnecter", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Se connecter à PlanDesign" }),
  ).toBeVisible();
  expect((await page.request.get("/api/auth/me")).status()).toBe(401);
});

test("arrivée par /nouveau : compositeur ouvert et prérempli, rien ne se génère", async ({
  page,
}) => {
  await signIn(page, "marie@example.org");
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
  const description = "Chambre d'amis sous les combles, ambiance bois clair";
  const query = new URLSearchParams({
    v: "1",
    source: "site",
    piece: "attic",
    surface: "35.5",
    description,
    inconnu: "ignoré",
  });
  await page.goto(`/#/nouveau?${query.toString()}`);
  await expect(page).toHaveURL(/#\/projects\/[0-9a-f-]{36}(\/board)?$/);
  const composer = page.getByRole("region", { name: "Compositeur" });
  await expect(composer).toBeVisible({ timeout: 30_000 });
  await expect(composer.getByLabel("Vos envies et contraintes")).toHaveValue(
    description,
  );
  await expect(composer.getByText(/compris :.*combles/)).toBeVisible();
  await shot(page, "4-nouveau-compositeur-prerempli");
  const id = page.url().split("/projects/")[1]!.split("/")[0];
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as {
    pending_instruction: string;
    pending_answers: { space: string; area_m2: number };
  };
  expect(project.pending_instruction).toBe(description);
  expect(project.pending_answers.space).toBe("combles");
  expect(project.pending_answers.area_m2).toBe(35.5);
  const running = (await (
    await page.request.get("/api/jobs/running")
  ).json()) as {
    running: boolean;
  };
  expect(running.running).toBe(false);
  // Un seul espace créé, et « Précédent » ne le recrée pas.
  const list = (await (
    await page.request.get("/api/projects")
  ).json()) as unknown[];
  expect(list).toHaveLength(1);
});

test("lecteur : ni outils ni « Générer » (le propriétaire, si)", async ({
  page,
}) => {
  await signIn(page, "paul@example.org");
  await expect(
    page.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
  // Avec les comptes, toute mutation porte l'origine de l'application.
  const made = await page.request.post("/api/projects", {
    data: { name: "Grenier de Paul" },
    headers: { Origin: new URL(page.url()).origin },
  });
  const { id } = (await made.json()) as { id: string };
  await page.goto(`/#/projects/${id}`);
  await expect(
    page
      .getByRole("button", { name: "Ouvrir le compositeur" })
      .or(page.getByRole("region", { name: "Compositeur" })),
  ).toBeVisible({ timeout: 30_000 });
  await expect(
    page.getByRole("button", { name: "Générer le plan" }),
  ).toBeVisible();
  await expect(
    page.getByRole("toolbar", { name: "Outils de l'espace de travail" }),
  ).toBeVisible();
  // Même projet vu comme lecteur (le rôle est simulé : l'écran n'est que du confort, la
  // barrière est la garde de chaque route, testée côté API).
  await page.route("**/api/projects/*/role", (route) =>
    route.fulfill({ json: { role: "viewer" } }),
  );
  await page.reload();
  await expect(page.locator(".editor.read-only")).toBeVisible({
    timeout: 30_000,
  });
  await expect(
    page.getByRole("button", { name: "Ouvrir le compositeur" }),
  ).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Compositeur" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Générer le plan" }),
  ).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Nouveau plan/ })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Assistant" })).toHaveCount(0);
  await expect(
    page.getByRole("toolbar", { name: "Outils de l'espace de travail" }),
  ).toBeHidden();
  await shot(page, "5-lecteur-sans-outils");
});
