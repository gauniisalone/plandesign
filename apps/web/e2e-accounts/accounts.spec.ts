import {
  expect,
  test,
  type Browser,
  type Page,
  type WebSocketRoute,
} from "@playwright/test";
import { gotoApp, reloadApp } from "../e2e/app-ready";

/** B1d-a : comptes actifs. Sans session, la page de connexion et rien d'autre ; connexion en
 *  deux temps (adresse, puis code) ; galerie ; « Me déconnecter » ; arrivée par /nouveau
 *  (compositeur prérempli, rien ne se génère) ; lecteur sans outils ni « Générer ». B1e :
 *  lien d'invitation (propriétaire → invité sans compte), révocation, rôle sur les cartes,
 *  pied de page réduit à la mention. */

const ADMIN = "admin@example.org";

/** Captures pour le compte rendu, seulement si E2E_CAPTURE_DIR est défini. */
async function shot(page: Page, name: string): Promise<void> {
  const dir = process.env.E2E_CAPTURE_DIR;
  if (dir) await page.screenshot({ path: `${dir}/${name}.png` });
}

async function signIn(page: Page, email: string): Promise<void> {
  await gotoApp(page, "/");
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  // Le code part après la réponse (tâche de fond) : on attend qu'il soit arrivé.
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
  await passNameStep(page);
}

/** B2a : un compte tout juste créé se voit proposer « Votre nom » ; on passe (ou on le donne). */
async function passNameStep(page: Page, name?: string): Promise<void> {
  const field = page.getByLabel("Votre nom (facultatif)");
  const inside = page
    .getByRole("heading", { name: "Vos espaces de travail" })
    .or(page.locator(".editor"));
  await expect(field.or(inside)).toBeVisible({ timeout: 30_000 });
  if (!(await field.isVisible())) return;
  if (name) {
    await field.fill(name);
    await page.getByRole("button", { name: "Continuer" }).click();
  } else {
    await page.getByRole("button", { name: "Passer" }).click();
  }
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
  await gotoApp(page, "/");
  await expect(
    page.getByRole("heading", { name: "Se connecter à PlanDesign" }),
  ).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(unauthorized.length).toBeLessThanOrEqual(1);
  expect(
    apiCalls.filter((p) => p !== "/api/health" && p !== "/api/auth/me"),
  ).toEqual([]);
  // Une seule lecture de /api/health avant l'affichage (hébergement et capacités partagent
  // la même réponse).
  expect(apiCalls.filter((p) => p === "/api/health")).toHaveLength(1);
  await expect(page.getByText("Vos espaces de travail")).toHaveCount(0);
  await shot(page, "1-connexion-adresse");
  // Mention en une ligne (B1e), avec le lien vers la page Confidentialité du site.
  await expect(
    page.getByText(
      "Un code vous est envoyé par e-mail. Vos données restent en France.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Confidentialité" }),
  ).toHaveAttribute("href", "https://plandesign.fr/confidentialite/");
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
  // B1e : connecté, le pied ne dit que la mention, plus « sur cette machine ».
  const foot = page.locator(".boards-foot");
  await expect(foot).toContainText("Vos données restent en France");
  await expect(foot).not.toContainText("cette machine");
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
  await gotoApp(page, `/#/nouveau?${query.toString()}`);
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
  await gotoApp(page, `/#/projects/${id}`);
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
  await reloadApp(page);
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

/** Un navigateur à part, vu par l'API depuis sa propre adresse (plage de documentation,
 *  transmise par le mandataire de Vite) : chacun a sa limite de tentatives de connexion. */
async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

test("B1e : lien d'invitation, invité sans compte en lecteur, lien révoqué refusé", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.21");
  await signIn(owner, "proprio@example.org");
  await expect(
    owner.getByRole("heading", { name: "Vos espaces de travail" }),
  ).toBeVisible();
  const made = await owner.request.post("/api/projects", {
    data: { name: "Grenier partagé" },
    headers: { Origin: new URL(owner.url()).origin },
  });
  const { id } = (await made.json()) as { id: string };
  await owner.goto(`/#/projects/${id}`);
  await owner.getByRole("button", { name: "Partager" }).click();
  const share = owner.getByRole("dialog", { name: "Partager le projet" });
  await share
    .getByRole("button", { name: "Créer un lien d'invitation" })
    .click();
  const link = await share.getByLabel("Lien d'invitation").inputValue();
  expect(link).toMatch(/\/#\/invitation\/[A-Za-z0-9_-]{43}$/);
  await shot(owner, "6-partager-lien");
  // Un second lien, révoqué aussitôt (on attend que le champ montre le nouveau lien).
  await share
    .getByRole("button", { name: "Créer un lien d'invitation" })
    .click();
  await expect(share.getByLabel("Lien d'invitation")).not.toHaveValue(link);
  const revoked = await share.getByLabel("Lien d'invitation").inputValue();
  expect(revoked).not.toBe(link);
  const pending = share.getByRole("list", { name: "Liens en attente" });
  await expect(pending.getByRole("listitem")).toHaveCount(2);
  await pending.getByRole("button", { name: "Révoquer" }).last().click();
  await expect(pending.getByRole("listitem")).toHaveCount(1);

  // L'invité, sans compte : la page de connexion seule, avec la ligne d'invitation, et rien
  // du projet avant la connexion.
  const guest = await person(browser, "198.51.100.22");
  await guest.goto(link);
  await expect(
    guest.getByText("Vous avez été invité à un projet PlanDesign"),
  ).toBeVisible();
  await expect(guest.getByText("Grenier partagé")).toHaveCount(0);
  await shot(guest, "7-invite-connexion");
  await guest.getByLabel("Adresse e-mail").fill("lea@example.org");
  await guest.getByRole("button", { name: "Recevoir un code" }).click();
  // Le code part après la réponse : on attend l'étape du code, puis qu'il soit arrivé.
  await expect(guest.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await guest.request.get(
        "/api/test/mail/last-code?email=lea@example.org",
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await guest.getByLabel("Code à 6 chiffres").fill(code);
  await guest.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(guest);
  await expect(guest).toHaveURL(new RegExp(`#/projects/${id}(/board)?$`));
  await expect(guest.locator(".editor.read-only")).toBeVisible({
    timeout: 30_000,
  });
  await expect(guest.getByRole("button", { name: "Partager" })).toHaveCount(0);
  // Le jeton ne reste pas dans l'adresse ; la galerie dit le rôle, sans Renommer ni Supprimer.
  expect(guest.url()).not.toContain("invitation");
  await guest.getByRole("button", { name: "Projets" }).click();
  const card = guest.locator(".boards-card", { hasText: "Grenier partagé" });
  await expect(card.locator(".boards-role")).toHaveText("lecteur");
  await expect(card.getByRole("button", { name: /Supprimer/ })).toHaveCount(0);
  await expect(card.getByRole("button", { name: /Renommer/ })).toHaveCount(0);
  await shot(guest, "8-galerie-lecteur");
  // Le lien révoqué : un seul message, et le projet ne s'ouvre pas.
  await guest.goto(revoked);
  await expect(guest.getByRole("alert")).toContainText(
    "Ce lien d'invitation n'est plus valable",
  );
  // Le propriétaire voit l'invitée parmi les membres.
  await owner.reload();
  await owner.getByRole("button", { name: "Partager" }).click();
  await expect(
    owner.getByRole("list", { name: "Membres" }).getByText("lea@example.org"),
  ).toBeVisible();
});

test("B2a : présence, puis coupure, reconnexion et resynchronisation", async ({
  browser,
}) => {
  // Le propriétaire passe par une route WebSocket que le test peut couper (comme un
  // redémarrage du serveur) et tenir fermée un moment avant de la rouvrir.
  const owner = await person(browser, "198.51.100.31");
  let allow = true;
  const sockets: WebSocketRoute[] = [];
  await owner.routeWebSocket(/\/ws\/projects\//, (ws) => {
    if (!allow) {
      void ws.close({ code: 1012, reason: "redémarrage" });
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
  });
  await owner.goto("/");
  await owner.getByLabel("Adresse e-mail").fill("alice@example.org");
  await owner.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(owner.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await owner.request.get(
        "/api/test/mail/last-code?email=alice@example.org",
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await owner.getByLabel("Code à 6 chiffres").fill(code);
  await owner.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(owner, "Alice");
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Salon partagé" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invite = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: origin,
  });
  const { token } = (await invite.json()) as { token: string };
  await owner.goto(`/#/projects/${id}`);

  // L'invité arrive par le lien, se nomme ; chacun voit l'avatar de l'autre.
  const guest = await person(browser, "198.51.100.32");
  await guest.goto(`/#/invitation/${token}`);
  await guest.getByLabel("Adresse e-mail").fill("bruno@example.org");
  await guest.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(guest.getByText("un code vient de partir")).toBeVisible();
  await expect
    .poll(async () => {
      const r = await guest.request.get(
        "/api/test/mail/last-code?email=bruno@example.org",
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await guest.getByLabel("Code à 6 chiffres").fill(code);
  await guest.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(guest, "Bruno");
  await expect(guest.locator(".editor")).toBeVisible({ timeout: 30_000 });
  const ownerPresence = owner.getByRole("list", {
    name: "Présents sur le projet",
  });
  await expect(ownerPresence.getByLabel("Bruno")).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    guest
      .getByRole("list", { name: "Présents sur le projet" })
      .getByLabel("Alice"),
  ).toBeVisible();
  await shot(owner, "9-presence");

  // Coupure : la connexion du propriétaire tombe et ne peut pas revenir tout de suite.
  allow = false;
  for (const ws of sockets.splice(0))
    await ws.close({ code: 1012, reason: "redémarrage" });
  await expect(
    owner.getByRole("status").filter({ hasText: "Reconnexion" }),
  ).toBeVisible();
  // Pendant la coupure, l'invité renomme le projet puis s'en va.
  const renamed = await guest.request.patch(`/api/projects/${id}`, {
    data: { name: "Salon de Bruno" },
    headers: { Origin: new URL(guest.url()).origin },
  });
  expect(renamed.ok()).toBe(true);
  await guest.context().close();
  // Retour du « serveur » : reconnexion, puis état complet relu.
  allow = true;
  await expect(
    owner.getByRole("status").filter({ hasText: "Reconnexion" }),
  ).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(owner.locator(".topbar .project-name")).toHaveText(
    "Salon de Bruno",
  );
  await expect(ownerPresence.getByLabel("Bruno")).toHaveCount(0);
});
