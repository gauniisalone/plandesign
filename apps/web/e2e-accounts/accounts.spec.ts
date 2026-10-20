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
 *  (compositeur prérempli, le plan se génère seul) ; lecteur sans outils ni « Générer ». B1e :
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
  // Serveur de test en inscription ouverte : aucune mention « bêta ».
  await expect(page.getByText(/en bêta, sur invitation/)).toHaveCount(0);
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

test("bêta sur invitation : annoncée avant l'adresse, réponse neutre, lien d'invitation à part", async ({
  page,
}) => {
  // Le serveur de test est en inscription ouverte : /api/health dit ici « invite ».
  await page.route("**/api/health", async (route) => {
    const response = await route.fetch();
    const health = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...health, signup: "invite" } });
  });
  const BETA =
    "PlanDesign est en bêta, sur invitation. Déjà inscrit ou invité ? Connectez-vous.";
  await gotoApp(page, "/");
  await expect(page.getByText(BETA)).toBeVisible();
  await expect(page.getByText("Vos designs et plans en un clic.")).toHaveCount(
    0,
  );
  await shot(page, "1b-connexion-beta");
  // Adresse inconnue : même réponse qu'une adresse connue, sans rien révéler.
  await page.getByLabel("Adresse e-mail").fill("inconnu@example.org");
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByRole("status")).toContainText(
    "Si cette adresse a un compte ou une invitation, un code vient de partir.",
  );
  await expect(
    page.getByText("Pas reçu ? Regardez dans vos courriers indésirables."),
  ).toBeVisible();
  // Arrivée par un lien d'invitation : la ligne d'invitation remplace la ligne « bêta ».
  await gotoApp(page, `/#/invitation/${"x".repeat(32)}`);
  await reloadApp(page);
  await expect(page.locator(".login-invite")).toContainText(
    "Vous avez été invité à un projet PlanDesign",
  );
  await expect(page.getByText(BETA)).toHaveCount(0);
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

test("arrivée par /nouveau : compositeur ouvert et prérempli, le plan se génère seul", async ({
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
  // Retour d'augus (19 oct.) : le plan se génère depuis les exigences, sans geste ; ici le
  // chemin automatique est coupé (OPENPLAN_AUTO_PATH=0), donc aucun rendu derrière.
  await expect(
    page.locator(".board-item.kind-plan img.board-plan"),
  ).toBeVisible({ timeout: 60_000 });
  await expect(composer).toBeVisible();
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

/** B2b : connexion sur la page de connexion déjà ouverte, avec un nom. */
async function signInHere(page: Page, email: string, name: string) {
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      const r = await page.request.get(
        `/api/test/mail/last-code?email=${email}`,
      );
      code = r.ok() ? ((await r.json()) as { code: string }).code : "";
      return code;
    })
    .toMatch(/^\d{6}$/);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(page, name);
}

/** Échelle appliquée au monde de l'espace de travail (la vue de la page). */
async function worldZoom(page: Page): Promise<number> {
  const transform = await page
    .locator(".board-world")
    .evaluate((el) => (el as HTMLElement).style.transform);
  return Number(/scale\(([\d.]+)\)/.exec(transform)?.[1] ?? "NaN");
}

test("B2b : curseur et sélection de l'autre, puis « Suivre » jusqu'à mon premier geste", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.41");
  await gotoApp(owner, "/");
  await signInHere(owner, "carla@example.org", "Carla");
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Atelier partagé" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  // Une note posée par l'API, que l'invité sélectionnera.
  const board = (await (
    await owner.request.get(`/api/projects/${id}/board`)
  ).json()) as { items: unknown[] };
  const noteId = "5b2b0000-0000-4000-8000-000000000001";
  const put = await owner.request.put(`/api/projects/${id}/board`, {
    data: {
      ...board,
      items: [
        ...board.items,
        {
          id: noteId,
          kind: "note",
          x: 40,
          y: 40,
          w: 220,
          h: 140,
          text: "Idée",
        },
      ],
    },
    headers: origin,
  });
  expect(put.ok()).toBe(true);
  const invite = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: origin,
  });
  const { token } = (await invite.json()) as { token: string };
  await gotoApp(owner, `/#/projects/${id}`);
  await expect(owner.locator(".board-stage")).toBeVisible({ timeout: 30_000 });

  const guest = await person(browser, "198.51.100.42");
  await gotoApp(guest, `/#/invitation/${token}`);
  await signInHere(guest, "dario@example.org", "Dario");
  await expect(guest.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  const ownerPresence = owner.getByRole("list", {
    name: "Présents sur le projet",
  });
  await expect(ownerPresence.getByLabel("Dario")).toBeVisible({
    timeout: 15_000,
  });

  // Son curseur : nommé, chez moi.
  const stage = await guest.locator(".board-stage").boundingBox();
  if (!stage) throw new Error("espace de travail absent");
  await guest.mouse.move(stage.x + stage.width / 2, stage.y + stage.height / 2);
  await guest.mouse.move(
    stage.x + stage.width / 2 + 30,
    stage.y + stage.height / 2 + 20,
  );
  await expect(
    owner.getByRole("img", { name: "Curseur de Dario" }),
  ).toBeVisible();
  // Sa sélection : contour à sa couleur, avec son nom.
  await guest.locator(`.board-item[data-kind="note"]`).click();
  await expect(guest.locator(".board-item.selected")).toHaveCount(1);
  await expect(owner.locator('.peer-select[data-peer="Dario"]')).toHaveCount(1);
  await expect(owner.locator(".peer-select .peer-label")).toHaveText("Dario");
  await shot(owner, "10-curseur-selection");
  // Il sort du canevas (vers la barre du haut) : son curseur disparaît.
  await guest.locator(".topbar .project-name").hover();
  await expect(
    owner.getByRole("img", { name: "Curseur de Dario" }),
  ).toHaveCount(0);

  // Je le suis : ma vue prend son zoom quand il zoome.
  await ownerPresence.getByRole("button", { name: "Dario" }).click();
  await expect(
    ownerPresence.getByRole("button", { name: "Dario" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    owner.getByRole("status").filter({ hasText: "Vous suivez Dario" }),
  ).toBeVisible();
  // « Zoomer » exactement : « Dézoomer » le contient aussi.
  const zoomIn = guest.getByRole("button", { name: "Zoomer", exact: true });
  await zoomIn.click();
  await zoomIn.click();
  const theirs = await worldZoom(guest);
  await expect.poll(() => worldZoom(owner)).toBeCloseTo(theirs, 2);
  await shot(owner, "11-suivre");
  // Mon premier geste (la molette) : je ne suis plus personne.
  const mine = await owner.locator(".board-stage").boundingBox();
  if (!mine) throw new Error("espace de travail absent");
  await owner.mouse.move(mine.x + 200, mine.y + 200);
  await owner.mouse.wheel(0, 120);
  await expect(owner.getByText("Vous suivez Dario")).toHaveCount(0);
  await expect(
    ownerPresence.getByRole("button", { name: "Dario" }),
  ).toHaveAttribute("aria-pressed", "false");
  // Il zoome encore : ma vue ne bouge plus.
  const before = await worldZoom(owner);
  await zoomIn.click();
  await guest.waitForTimeout(600); // au-delà d'une période d'envoi de la vue (250 ms)
  expect(await worldZoom(owner)).toBeCloseTo(before, 3);
});

test("B2c : deux éditeurs, dernière écriture par élément, annuler sans recréer", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.51");
  await gotoApp(owner, "/");
  await signInHere(owner, "fanny@example.org", "Fanny");
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Tableau à deux" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const alpha = "b2c0a000-0000-4000-8000-000000000001";
  const bravo = "b2c0b000-0000-4000-8000-000000000002";
  const note = (nid: string, text: string, x: number) => ({
    id: nid,
    kind: "note",
    x,
    y: -300,
    w: 220,
    h: 140,
    text,
  });
  const placed = await owner.request.patch(`/api/projects/${id}/board/items`, {
    data: { set: [note(alpha, "Alpha", -900), note(bravo, "Bravo", -600)] },
    headers: origin,
  });
  expect(placed.ok()).toBe(true);
  const invite = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: origin,
  });
  const { token } = (await invite.json()) as { token: string };
  await gotoApp(owner, `/#/projects/${id}`);
  const guest = await person(browser, "198.51.100.52");
  await gotoApp(guest, `/#/invitation/${token}`);
  await signInHere(guest, "gilles@example.org", "Gilles");
  for (const p of [owner, guest])
    await expect(p.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await expect(
    owner
      .getByRole("list", { name: "Présents sur le projet" })
      .getByLabel("Gilles"),
  ).toBeVisible({ timeout: 15_000 });

  type Saved = { items: { id: string; x: number; updated_by?: string }[] };
  const saved = async () =>
    (await (
      await owner.request.get(`/api/projects/${id}/board`)
    ).json()) as Saved;
  const xOf = async (nid: string) =>
    (await saved()).items.find((i) => i.id === nid)?.x;
  const card = (p: Page, text: string) =>
    p.locator(".board-item.kind-note", { hasText: text });
  const drag = async (p: Page, text: string, dx: number) => {
    const box = (await card(p, text).boundingBox())!;
    await p.mouse.move(box.x + 30, box.y + 20);
    await p.mouse.down();
    await p.mouse.move(box.x + 30 + dx / 2, box.y + 20, { steps: 4 });
    await p.mouse.move(box.x + 30 + dx, box.y + 20, { steps: 4 });
    await p.mouse.up();
  };

  // Deux cartes différentes, chacun la sienne, en même temps : les deux sont gardées.
  const [a0, b0] = [await xOf(alpha), await xOf(bravo)];
  await Promise.all([drag(owner, "Alpha", 120), drag(guest, "Bravo", 160)]);
  await expect.poll(async () => (await xOf(alpha)) !== a0).toBe(true);
  await expect.poll(async () => (await xOf(bravo)) !== b0).toBe(true);
  // Plus de « enregistré ailleurs, rechargé » : aucun des deux gestes n'est refusé.
  for (const p of [owner, guest])
    await expect(
      p.getByRole("alert").filter({ hasText: "enregistré ailleurs" }),
    ).toHaveCount(0);
  // Chacun voit le geste de l'autre (relecture à l'avis « changed »).
  const bravoX = await xOf(bravo);
  await expect
    .poll(async () =>
      card(owner, "Bravo").evaluate((el) =>
        parseFloat((el as HTMLElement).style.left),
      ),
    )
    .toBeCloseTo(bravoX!, 0);
  await shot(owner, "12-deux-editeurs");

  // La même carte, l'un après l'autre : la dernière écriture gagne, chez les deux.
  await drag(owner, "Alpha", 80);
  await expect
    .poll(
      async () => (await saved()).items.find((i) => i.id === alpha)?.updated_by,
    )
    .toMatch(/^user:/);
  const afterOwner = await xOf(alpha);
  await expect
    .poll(async () =>
      card(guest, "Alpha").evaluate((el) =>
        parseFloat((el as HTMLElement).style.left),
      ),
    )
    .toBeCloseTo(afterOwner!, 0);
  await drag(guest, "Alpha", -200);
  await expect.poll(async () => (await xOf(alpha)) !== afterOwner).toBe(true);
  const last = await xOf(alpha);
  await expect
    .poll(async () =>
      card(owner, "Alpha").evaluate((el) =>
        parseFloat((el as HTMLElement).style.left),
      ),
    )
    .toBeCloseTo(last!, 0);

  // Gilles a déplacé Bravo ; Fanny le supprime ; « Annuler » chez Gilles ne le recrée pas.
  await card(owner, "Bravo").click();
  await owner.keyboard.press("Delete");
  await expect.poll(async () => (await xOf(bravo)) === undefined).toBe(true);
  await expect(card(guest, "Bravo")).toHaveCount(0, { timeout: 15_000 });
  await guest.keyboard.press("Control+z"); // annule son dernier geste (Alpha) : rétabli
  await guest.keyboard.press("Control+z"); // celui d'avant (Bravo) : supprimé ailleurs
  await expect(guest.getByRole("alert")).toContainText(
    "supprimé par quelqu'un d'autre n'a pas été recréé",
  );
  await expect(card(guest, "Bravo")).toHaveCount(0);
  await expect.poll(async () => (await xOf(alpha)) === afterOwner).toBe(true);
  expect(await xOf(bravo)).toBeUndefined();
});

test("B2d : bail du plan, demander et rendre la main, curseur en mètres", async ({
  browser,
}) => {
  const owner = await person(browser, "198.51.100.61");
  await gotoApp(owner, "/");
  await signInHere(owner, "fanny@example.org", "Fanny");
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Plan partagé" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invite = await owner.request.post(`/api/projects/${id}/invitations`, {
    data: { role: "editor" },
    headers: origin,
  });
  const { token } = (await invite.json()) as { token: string };
  const guest = await person(browser, "198.51.100.62");
  await gotoApp(guest, `/#/invitation/${token}`);
  await signInHere(guest, "gilles@example.org", "Gilles");
  // Tous deux dans l'onglet Plan du même plan.
  // Comme avec les comptes : depuis le cadre Plan de l'espace de travail (le bouton n'apparaît
  // qu'une fois le rôle d'éditeur connu).
  for (const p of [owner, guest]) {
    await gotoApp(p, `/#/projects/${id}`);
    const enter = p.getByRole("button", { name: /Tracer moi-même/ });
    await expect(enter).toBeVisible({ timeout: 30_000 });
    await enter.click();
    await expect(p.locator("svg.plan")).toBeVisible({ timeout: 30_000 });
  }
  // Murs enregistrés, lus sur le serveur : un niveau les range dans `elements` (type « wall »).
  const walls = async () => {
    const project = (await (
      await owner.request.get(`/api/projects/${id}`)
    ).json()) as {
      levels: Record<string, { elements: Record<string, { type: string }> }>;
    };
    return Object.values(project.levels).reduce(
      (n, l) =>
        n + Object.values(l.elements).filter((e) => e.type === "wall").length,
      0,
    );
  };
  const draw = async (p: Page, points: [number, number][]) => {
    // Par la palette (comme mobile.spec) : les raccourcis dépendent du focus, qui peut être
    // resté dans un champ de l'espace de travail.
    const svg = p.locator("svg.plan");
    const tools = p.getByRole("toolbar", { name: "Outils" });
    await tools.getByRole("button", { name: "Mur", exact: true }).click();
    for (const [x, y] of points) await svg.click({ position: { x, y } });
    await tools.getByRole("button", { name: "Sélection", exact: true }).click();
  };

  // Fanny, seule à modifier : aucune différence, son premier geste prend le bail.
  // Un contour fermé (comme bars-b2.spec) : les murs ne partent qu'à la fermeture.
  await draw(owner, [
    [300, 600],
    [700, 600],
    [700, 400],
    [300, 400],
    [300, 600],
  ]);
  await expect.poll(walls).toBeGreaterThan(0);
  const afterFanny = await walls();
  // Enregistrés, et relus chez Gilles (avis « changed ») : il les voit dans sa page.
  await expect(guest.locator("svg.plan polygon.wall")).toHaveCount(afterFanny, {
    timeout: 15_000,
  });
  // Gilles voit qui modifie, ses outils sont grisés, et il ne peut rien écrire.
  await expect(
    guest.getByRole("status").filter({ hasText: "Fanny modifie ce plan" }),
  ).toBeVisible({ timeout: 15_000 });
  await expect(
    guest.getByRole("button", { name: "Mur", exact: true }),
  ).toBeDisabled();
  await expect(
    guest.getByRole("button", { name: "Prendre la main" }),
  ).toHaveCount(0);
  await shot(guest, "13-plan-tenu");
  // Son curseur, en mètres, chez Fanny.
  const plan = (await guest.locator("svg.plan").boundingBox())!;
  await guest.mouse.move(plan.x + plan.width / 2, plan.y + plan.height / 2);
  await guest.mouse.move(
    plan.x + plan.width / 2 + 40,
    plan.y + plan.height / 2,
  );
  await expect(
    owner.locator("svg.plan").getByRole("img", { name: "Curseur de Gilles" }),
  ).toBeVisible();
  // Gilles demande la main : seule Fanny est prévenue ; elle la rend.
  await guest.getByRole("button", { name: "Demander la main" }).click();
  await expect(
    owner.getByRole("status").filter({ hasText: "Gilles demande la main" }),
  ).toBeVisible();
  await expect(guest.getByText("demande la main")).toHaveCount(0);
  await shot(owner, "14-demande-la-main");
  await owner.getByRole("button", { name: "Rendre la main" }).click();
  await expect(
    guest.getByRole("status").filter({ hasText: "modifie ce plan" }),
  ).toHaveCount(0, { timeout: 15_000 });
  // Gilles modifie à son tour ; Fanny voit le bandeau.
  await draw(guest, [
    [850, 600],
    [1100, 600],
    [1100, 400],
    [850, 400],
    [850, 600],
  ]);
  await expect.poll(walls).toBeGreaterThan(afterFanny);
  await expect(
    owner.getByRole("status").filter({ hasText: "Gilles modifie ce plan" }),
  ).toBeVisible({ timeout: 15_000 });
  // Gilles quitte l'onglet Plan : le plan est libre, Fanny peut reprendre.
  await guest
    .getByRole("navigation", { name: "Espaces du projet" })
    .getByRole("button", { name: "← Espace de travail" })
    .click();
  await expect(
    owner.getByRole("status").filter({ hasText: "modifie ce plan" }),
  ).toHaveCount(0, { timeout: 15_000 });
});

test("KAN-12 : ma place affichée, un envoi trop gros refusé avant l'envoi et par le serveur", async ({
  browser,
}) => {
  // La config de ces e2e donne un Mo par compte (OPENPLAN_QUOTA_MB=1).
  const page = await person(browser, "198.51.100.71");
  await gotoApp(page, "/");
  await signInHere(page, "hugo@example.org", "Hugo");
  const origin = { Origin: new URL(page.url()).origin };
  const made = await page.request.post("/api/projects", {
    data: { name: "Projet de Hugo" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  // Réglages : « Votre espace : 0 Mo sur 1 Mo », avec la barre.
  await gotoApp(page, "/");
  await page.getByRole("button", { name: "Réglages" }).click();
  const settings = page.getByRole("dialog", { name: "Réglages" });
  await expect(
    settings.getByText(/Votre espace : \d+ Mo sur 1 Mo/),
  ).toBeVisible();
  await expect(
    settings.getByRole("progressbar", { name: "Place utilisée" }),
  ).toBeVisible();
  await shot(page, "15-place-du-compte");
  // Le serveur refuse un envoi qui dépasserait la place, avant toute écriture.
  const big = Buffer.alloc(1_200_000, 7);
  const refused = await page.request.post(`/api/projects/${id}/media`, {
    multipart: {
      file: {
        name: "gros.bin",
        mimeType: "application/octet-stream",
        buffer: big,
      },
    },
    headers: origin,
  });
  expect(refused.status()).toBe(413);
  expect(((await refused.json()) as { detail: string }).detail).toContain(
    "Votre espace est plein",
  );
  const media = (await (
    await page.request.get(`/api/projects/${id}/media`)
  ).json()) as unknown[];
  expect(media).toHaveLength(0);
  // Dans l'espace de travail : l'atelier prévient avant d'envoyer (rien ne part).
  await gotoApp(page, `/#/projects/${id}`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  const sent: string[] = [];
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      /\/(media|images)$/.test(new URL(r.url()).pathname)
    )
      sent.push(r.url());
  });
  await page
    .locator('.board-stage ~ input[type="file"], input[type="file"]')
    .first()
    .setInputFiles({
      name: "gros.pdf",
      mimeType: "application/pdf",
      buffer: big,
    });
  await expect(page.getByText(/Pas assez de place/)).toBeVisible();
  expect(sent).toHaveLength(0);
  await shot(page, "16-envoi-refuse");
});

test("KAN-51 : commenter à plusieurs, le commentateur commente sans modifier, le lecteur lit", async ({
  browser,
}) => {
  // Une adresse par personne (comme person()) : la limite des connexions reste intacte.
  const owner = await person(browser, "198.51.100.91");
  await gotoApp(owner, "/");
  await signInHere(owner, "iris@example.org", "Iris");
  const origin = { Origin: new URL(owner.url()).origin };
  const made = await owner.request.post("/api/projects", {
    data: { name: "Salon à commenter" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  const invite = async (role: string) =>
    (
      (await (
        await owner.request.post(`/api/projects/${id}/invitations`, {
          data: { role },
          headers: origin,
        })
      ).json()) as { token: string }
    ).token;
  const [asCommenter, asViewer] = [
    await invite("commenter"),
    await invite("viewer"),
  ];
  await gotoApp(owner, `/#/projects/${id}`);
  const commenter = await person(browser, "198.51.100.92");
  await gotoApp(commenter, `/#/invitation/${asCommenter}`);
  await signInHere(commenter, "jules@example.org", "Jules");
  const viewer = await person(browser, "198.51.100.93");
  await gotoApp(viewer, `/#/invitation/${asViewer}`);
  await signInHere(viewer, "kim@example.org", "Kim");
  for (const p of [owner, commenter, viewer])
    await expect(p.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  const tools = (p: Page) =>
    p.getByRole("toolbar", { name: "Outils de l'espace de travail" });

  // Le commentateur : l'outil Commentaire, aucun outil d'édition.
  await expect(
    tools(commenter).getByRole("button", { name: "Commentaire", exact: true }),
  ).toBeVisible();
  await expect(
    tools(commenter).getByRole("button", { name: "Note" }),
  ).toHaveCount(0);
  // Le lecteur : ni l'outil, ni la réponse.
  await expect(
    viewer.getByRole("button", { name: "Commentaire", exact: true }),
  ).toHaveCount(0);

  const tool = tools(commenter).getByRole("button", {
    name: "Commentaire",
    exact: true,
  });
  const draft = commenter.getByRole("dialog", { name: "Nouveau commentaire" });
  const pinAt = async (x: number, y: number, text: string) => {
    await tool.click();
    await commenter.mouse.click(x, y);
    await draft.getByLabel("Votre commentaire").fill(text);
    await draft.getByRole("button", { name: "Publier" }).click();
    await expect(draft).toHaveCount(0);
  };
  const targets = async () =>
    (
      (await (
        await commenter.request.get(`/api/projects/${id}/comments`)
      ).json()) as { target: { item_id?: string | null } }[]
    ).map((t) => t.target.item_id ?? null);

  // Échap quitte l'outil.
  await tool.click();
  await expect(tool).toHaveAttribute("aria-pressed", "true");
  await commenter.keyboard.press("Escape");
  await expect(tool).toHaveAttribute("aria-pressed", "false");

  // Jules pose une épingle au centre, sur le cadre Plan vide (et ses boutons) : elle est
  // attachée au cadre ; Iris la voit en moins de 2 s.
  const box = (await commenter.locator(".board-stage").boundingBox())!;
  await pinAt(
    box.x + box.width / 2,
    box.y + box.height / 2,
    "Et si on ouvrait ce mur ?",
  );
  const seen = owner.getByRole("button", { name: "Commentaire de Jules" });
  await expect(seen).toBeVisible({ timeout: 2_000 });
  const [onPlan] = await targets();
  expect(onPlan).not.toBeNull();
  await shot(owner, "17-commentaire-recu");

  // Puis une épingle libre, dans un coin vide (on dézoome pour en laisser un).
  for (let i = 0; i < 3; i++) await commenter.keyboard.press("-");
  await pinAt(
    box.x + box.width - 140,
    box.y + box.height * 0.3,
    "Ici, une étagère",
  );
  await expect(seen).toHaveCount(2, { timeout: 2_000 });
  expect(await targets()).toEqual([onPlan, null]);

  // Iris répond sur le premier fil ; Jules voit les deux messages, signés.
  await seen.first().click();
  const panel = owner.getByRole("dialog", { name: "Commentaires" });
  await panel.getByLabel("Répondre").fill("Bonne idée, à chiffrer");
  await panel.getByRole("button", { name: "Répondre" }).click();
  const his = commenter
    .getByRole("button", { name: "Commentaire de Jules" })
    .first();
  await expect(his).toHaveText("2", { timeout: 5_000 });
  await his.click();
  const hisPanel = commenter.getByRole("dialog", { name: "Commentaires" });
  await expect(hisPanel.locator(".comment-author")).toHaveText([
    "Jules",
    "Iris",
  ]);

  // Le lecteur lit le fil, sans pouvoir répondre.
  const kimPin = viewer
    .getByRole("button", { name: "Commentaire de Jules" })
    .first();
  await expect(kimPin).toBeVisible({ timeout: 5_000 });
  await kimPin.click();
  const kimPanel = viewer.getByRole("dialog", { name: "Commentaires" });
  await expect(kimPanel.getByText("Bonne idée, à chiffrer")).toBeVisible();
  await expect(kimPanel.getByLabel("Répondre")).toHaveCount(0);
  await expect(kimPanel.getByRole("button", { name: "Résoudre" })).toHaveCount(
    0,
  );

  // Jules résout : l'épingle disparaît chez Iris (reste l'épingle libre).
  await hisPanel.getByRole("button", { name: "Résoudre" }).click();
  await expect(seen).toHaveCount(1, { timeout: 5_000 });
  await shot(commenter, "18-fil-resolu");
});

test("B2b : session révoquée, poignée de main refusée : retour à la connexion", async ({
  browser,
}) => {
  // Derrière l'intergiciel, un refus de poignée de main n'arrive au navigateur que comme un
  // échec, sans code 4401 : le test refuse de même toute nouvelle connexion.
  const page = await person(browser, "198.51.100.43");
  let refuse = false;
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws\/projects\//, (ws) => {
    if (refuse) {
      void ws.close({ code: 1011, reason: "refusé" });
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
  });
  await gotoApp(page, "/");
  await signInHere(page, "elsa@example.org", "Elsa");
  const origin = { Origin: new URL(page.url()).origin };
  const made = await page.request.post("/api/projects", {
    data: { name: "Projet d'Elsa" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => sockets.length).toBeGreaterThan(0);

  // « Me déconnecter partout » depuis un autre appareil, puis la connexion tombe : la page
  // de connexion revient, par le premier chemin qui voit la 401 (une lecture de la page, ou
  // la connexion de présence après deux échecs). Le chemin de la présence seul, réseau /api
  // coupé, est vérifié par le test KAN-49 ci-dessous.
  const revoked = await page.request.post("/api/auth/logout-all", {
    headers: origin,
  });
  expect(revoked.ok()).toBe(true);
  refuse = true;
  for (const ws of sockets.splice(0))
    await ws.close({ code: 1012, reason: "redémarrage" });
  await expect(
    page.getByRole("heading", { name: "Se connecter à PlanDesign" }),
  ).toBeVisible({ timeout: 20_000 });
});

test("KAN-49 : session révoquée, /api coupé sauf /api/auth/me : la connexion en moins de 2 s, puis retour au projet", async ({
  browser,
}) => {
  // Sa propre adresse : deux connexions seulement (avant et après), loin de la limite ; une
  // adresse par répétition (--repeat-each), la limite des connexions étant par adresse.
  const repeat = test.info().repeatEachIndex;
  const page = await person(
    browser,
    repeat === 0 ? "198.51.100.49" : `198.51.100.${150 + repeat}`,
  );
  let refuse = false;
  const sockets: WebSocketRoute[] = [];
  await page.routeWebSocket(/\/ws\/projects\//, (ws) => {
    if (refuse) {
      void ws.close({ code: 1011, reason: "refusé" });
      return;
    }
    ws.connectToServer();
    sockets.push(ws);
  });
  // Une adresse unique par passage : les répétitions (5 passages de suite) ne partagent ni
  // compte ni session.
  const email = `noe-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}@example.org`;
  await gotoApp(page, "/");
  await signInHere(page, email, "Noé");
  const origin = { Origin: new URL(page.url()).origin };
  const made = await page.request.post("/api/projects", {
    data: { name: "Projet de Noé" },
    headers: origin,
  });
  const { id } = (await made.json()) as { id: string };
  await gotoApp(page, `/#/projects/${id}`);
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  await expect.poll(() => sockets.length).toBeGreaterThan(0);
  // Page calme avant de couper et de révoquer : en développement, React monte l'espace de
  // travail deux fois, et la seconde vague de lectures (media, contextual, home/settings),
  // déjà partie, répondrait 401 après la révocation sans passer par la coupure. On attend
  // qu'aucune requête ne soit en cours (la connexion de présence ne compte pas), puis un
  // court délai pour une éventuelle lecture tardive.
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(500);

  // Réseau /api coupé, sauf /api/auth/me : avant le correctif, la connexion attendait la 401
  // d'un rechargement du projet, qui ne venait jamais.
  const cut = (url: URL) =>
    url.pathname.startsWith("/api/") && url.pathname !== "/api/auth/me";
  await page.route(cut, (route) => route.abort("failed"));
  let me401 = 0;
  page.on("response", (r) => {
    if (new URL(r.url()).pathname === "/api/auth/me" && r.status() === 401)
      me401 ||= Date.now();
  });
  const revoked = await page.request.post("/api/auth/logout-all", {
    headers: origin,
  });
  expect(revoked.ok()).toBe(true);
  refuse = true;
  for (const ws of sockets.splice(0))
    await ws.close({ code: 1012, reason: "redémarrage" });
  const login = page.getByRole("heading", {
    name: "Se connecter à PlanDesign",
  });
  await expect.poll(() => me401, { timeout: 20_000 }).toBeGreaterThan(0);
  // Must have 1 : moins de 2 s après la 401 de /api/auth/me.
  await expect(login).toBeVisible({ timeout: 2_000 });
  expect(Date.now() - me401).toBeLessThan(2_000);
  // Must have 3 : pas de boucle de requêtes une fois la connexion affichée.
  const after: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/api/")) after.push(r.url());
  });
  await page.waitForTimeout(3_000);
  expect(after.length).toBeLessThanOrEqual(2);
  // L'adresse du projet est gardée.
  expect(page.url()).toContain(`#/projects/${id}`);

  // Must have 2 : réseau rétabli, reconnexion, retour sur le projet ouvert.
  await page.unroute(cut);
  refuse = false;
  await signInHere(page, email, "Noé");
  await expect(page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
  expect(page.url()).toContain(`#/projects/${id}`);
});
