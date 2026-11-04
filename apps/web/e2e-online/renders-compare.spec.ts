import fs from "node:fs";
import path from "node:path";
import {
  expect,
  request as playwrightRequest,
  test,
  type APIRequestContext,
  type Browser,
  type Locator,
  type Page,
} from "@playwright/test";
import { gotoApp } from "../e2e/app-ready";
// Le compte autorisé propre à ce fichier (3 codes de connexion par adresse et par quart d'heure).
import {
  E2E_WORKER_TOKEN,
  RENDER_ACCOUNT_KAN99 as RENDER_ACCOUNT,
} from "./render-account";
import { claimOwn } from "./worker-queue";

/** KAN-99 : comparer les rendus (maquette validée par augus le 2 nov.).
 *  1. Avant/après sur un rendu « depuis la photo » : même vue, même cadrage que la photo.
 *  2. Favoris : une étoile par rendu, le filtre « Favoris », commun au projet.
 *  3. Côte à côte : 2 à 4 rendus choisis, zoom et déplacement synchronisés, « Guidé par »
 *     sous chacun.
 *  4. Clavier : ← → d'un rendu à l'autre, Entrée l'ouvre seul, Échap ferme.
 *  Et l'ouverture des rendus aux membres (décision d'augus, 2 nov.) : une lectrice voit les
 *  rendus du projet sans rien pouvoir lancer ni étoiler ; une non-membre reçoit 404.
 *  Le poste est SIMULÉ comme dans renders-gallery.spec.ts. Chaque personne et le poste ont
 *  leur propre X-Forwarded-For (198.51.100.251 à 255, 214 et 215). Une commentatrice réagit aux rendus
 *  (décision d'augus, 3 nov.) ; la lectrice voit les pastilles, inactives. `KAN99_SHOTS` : dossier des captures. */

const IMAGE = path.resolve("../../tests/fixtures/rect_room/image.png");
const PNG = fs.readFileSync(IMAGE);
const SIZE = [PNG.readUInt32BE(16), PNG.readUInt32BE(20)] as const;
const SHOTS = process.env.KAN99_SHOTS;

async function shot(page: Page, name: string): Promise<void> {
  if (!SHOTS) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
}

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1400, height: 900 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

async function lastCode(page: Page, email: string): Promise<string> {
  const r = await page.request.get(
    `/api/test/mail/last-code?email=${encodeURIComponent(email)}`,
  );
  return r.ok() ? ((await r.json()) as { code: string }).code : "";
}

/** Connexion sur la page ouverte (accueil ou lien d'invitation). */
async function signInHere(page: Page, email: string): Promise<void> {
  // Le compte des rendus se connecte plusieurs fois dans la même base : jamais un ancien code.
  const before = await lastCode(page, email);
  await page.getByLabel("Adresse e-mail").fill(email);
  await page.getByRole("button", { name: "Recevoir un code" }).click();
  await expect(page.getByText("un code vient de partir")).toBeVisible();
  let code = "";
  await expect
    .poll(async () => {
      code = await lastCode(page, email);
      return code !== before && /^\d{6}$/.test(code);
    })
    .toBe(true);
  await page.getByLabel("Code à 6 chiffres").fill(code);
  await page.getByRole("button", { name: "Se connecter" }).click();
  const field = page.getByLabel("Votre nom (facultatif)");
  const skip = page.getByRole("button", { name: "Passer" });
  const inside = page
    .getByRole("heading", { name: "Vos espaces de travail" })
    .or(page.locator(".editor"));
  await expect(field.or(skip).or(inside).first()).toBeVisible({
    timeout: 30_000,
  });
  if (await field.isVisible()) {
    await field.fill("Kim");
    await page.getByRole("button", { name: "Continuer" }).click();
  } else if (await skip.isVisible()) {
    await skip.click();
  }
  await expect(inside.first()).toBeVisible({ timeout: 30_000 });
}

/** Un espace (nom unique), quatre murs, un point de vue libre et, sur demande, une photo et sa
 *  caméra estimée (comme renders-gallery.spec.ts). */
async function project(
  page: Page,
  { photo = false } = {},
): Promise<{ id: string; level: string; camera: string }> {
  const origin = { Origin: new URL(page.url()).origin };
  const created = await page.request.post("/api/projects", {
    data: { name: `Comparer ${Date.now().toString(36)}` },
    headers: origin,
  });
  expect(created.status()).toBe(201);
  const body = (await created.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(body.levels)[0]!;
  const ops = `/api/projects/${body.id}/levels/${level}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ];
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
  type Ops = {
    project: {
      levels: Record<
        string,
        { elements: Record<string, { type: string; name?: string }> }
      >;
    };
  };
  const camera = async (name: string, at: [number, number], yaw: number) => {
    const added = await page.request.post(ops, {
      data: { op: "add_camera", position: { x: at[0], y: at[1] }, yaw, name },
      headers: origin,
    });
    expect(added.ok()).toBe(true);
    const after = (await added.json()) as Ops;
    return Object.entries(after.project.levels[level]!.elements).find(
      ([, e]) => e.type === "camera" && e.name === name,
    )![0];
  };
  const view = await camera("Vue fenêtre", [1, 1], 0.6);
  if (photo) {
    const uploaded = await page.request.post(
      `/api/projects/${body.id}/images`,
      {
        headers: origin,
        multipart: {
          file: { name: "salon.png", mimeType: "image/png", buffer: PNG },
        },
      },
    );
    expect(uploaded.status()).toBe(201);
    const image = ((await uploaded.json()) as { image: { id: string } }).image;
    const estimated = await camera("Photo", [3, 2], 3.6);
    const set = await page.request.post(ops, {
      data: {
        op: "update_element",
        element_id: estimated,
        fields: { image_id: image.id, focal_px: 900 },
      },
      headers: origin,
    });
    expect(set.ok(), await set.text()).toBe(true);
  }
  return { id: body.id, level, camera: view };
}

type Listing = {
  requests: { id: string; state: string; mine?: boolean }[];
  favorite_ids?: string[];
};

/** Le poste simulé dépose les demandes en attente jusqu'à celles de ce projet (la base est
 *  commune aux e2e-online : une demande d'un autre fichier peut passer avant), avec ses mots. */
async function deliverProject(
  worker: APIRequestContext,
  page: Page,
  projectId: string,
  words: string[],
): Promise<void> {
  const pending = async () =>
    (
      (await (
        await page.request.get(`/api/projects/${projectId}/realistic`)
      ).json()) as Listing
    ).requests.filter((r) => r.state === "pending" || r.state === "taken");
  // La demande part de l'interface (clic du menu) : on attend qu'elle soit enregistrée, sinon
  // la boucle peut voir une liste vide, ne rien prendre, et la demande arrive après.
  await expect.poll(async () => (await pending()).length).toBeGreaterThan(0);
  // KAN-103 : le poste ne dépose ici que les demandes de CE projet (claimOwn dépose celles
  // laissées par un autre fichier sans les compter) ; les autres demandes du projet suivent.
  for (let i = 0; i < 12 && (await pending()).length > 0; i++) {
    const job = await claimOwn(worker, page, projectId, PNG);
    const done = await worker.post(`/api/worker/jobs/${job.request_id}/result`, {
      headers: { "X-Render-Lease": job.lease },
      multipart: {
        meta: JSON.stringify({
          // 120 s : la durée typique (médiane du journal, base commune) reste « ~2 min »,
          // qu'attend renders-gallery.spec.ts, lancé après ce fichier.
          seconds: 120,
          vram_peak_mib: 0,
          width: SIZE[0],
          height: SIZE[1],
          engine: "fake",
          style_words: words,
        }),
        image: { name: "render.png", mimeType: "image/png", buffer: PNG },
      },
    });
    expect(done.status()).toBe(200);
  }
  expect(await pending()).toEqual([]);
}

function transforms(dialog: Locator): Promise<string[]> {
  return dialog
    .locator("img.rcomp-img")
    .evaluateAll((imgs) => imgs.map((i) => getComputedStyle(i).transform));
}

test("avant/après, favoris, côte à côte synchronisé, clavier", async ({
  page,
  baseURL,
}) => {
  test.setTimeout(240_000);
  const failures: string[] = [];
  page.on("response", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/api/") && r.status() >= 500)
      failures.push(`${r.status()} ${p}`);
  });
  await page.setExtraHTTPHeaders({ "X-Forwarded-For": "198.51.100.251" });
  await gotoApp(page, "/");
  await signInHere(page, RENDER_ACCOUNT.email);
  const { id } = await project(page, { photo: true });
  const worker = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${E2E_WORKER_TOKEN}`,
      "X-Forwarded-For": "198.51.100.252",
    },
  });
  await gotoApp(page, `/#/projects/${id}/board`);
  const frame = page
    .getByRole("region", { name: "Espace de travail" })
    .getByRole("region", { name: "Rendus réalistes" });
  const menuButton = frame.getByRole("button", { name: "Rendre ▾" });
  await expect(menuButton).toBeEnabled({ timeout: 30_000 });
  const gallery = frame.getByRole("list", { name: "Galerie des rendus" });

  // Trois rendus : la vue libre, la photo 1 (D5), la vue libre de nouveau ; chacun ses mots.
  const asks: [RegExp, string[]][] = [
    [/Rendre la vue « Vue fenêtre »/, ["scandinavian", "bright"]],
    [/Depuis la photo 1/, ["industrial", "black accents"]],
    [/Rendre la vue « Vue fenêtre »/, ["bohemian"]],
  ];
  for (const [n, [item, words]] of asks.entries()) {
    await menuButton.click();
    await frame.getByRole("menuitem", { name: item }).click();
    await deliverProject(worker, page, id, words);
    await expect(gallery.locator("img.rgal-img")).toHaveCount(n + 1, {
      timeout: 15_000,
    });
  }
  await worker.dispose();

  // 2. Favoris : deux étoiles, le filtre, et la relecture (commun au projet, côté serveur).
  const filter = frame.getByRole("group", { name: "Filtrer les rendus" });
  await expect(filter.getByRole("button", { name: "Tous · 3" })).toBeVisible();
  const favButton = filter.getByRole("button", { name: /Favoris · 0/ });
  await expect(favButton).toBeVisible();
  const add = gallery.getByRole("button", { name: /^Ajouter aux favoris/ });
  await expect(add).toHaveCount(3);
  await add.first().click();
  await expect(
    gallery.getByRole("button", { name: /^Retirer des favoris/ }),
  ).toHaveCount(1);
  await gallery
    .getByRole("button", { name: /^Ajouter aux favoris/ })
    .last()
    .click();
  await expect(filter.getByRole("button", { name: /Favoris · 2/ })).toBeVisible();
  await filter.getByRole("button", { name: /Favoris · 2/ }).click();
  await expect(gallery.locator("img.rgal-img")).toHaveCount(2);
  await expect(gallery.getByRole("button", { name: /photo 1/ })).toHaveCount(0);
  await shot(page, "2-node-rendus-favoris");
  const listed = (await (
    await page.request.get(`/api/projects/${id}/realistic`)
  ).json()) as Listing;
  expect(listed.favorite_ids).toHaveLength(2);
  await page.reload();
  await expect(
    frame.getByRole("button", { name: /Favoris · 2/ }),
  ).toBeVisible({ timeout: 30_000 });
  await frame.getByRole("button", { name: /Favoris · 2/ }).click();
  await expect(gallery.locator("img.rgal-img")).toHaveCount(2);
  await frame.getByRole("button", { name: "Tous · 3" }).click();
  await expect(gallery.locator("img.rgal-img")).toHaveCount(3);

  // 1. Avant/après du rendu depuis la photo : même vue (note « fidèle ») et même cadrage
  // (même proportion que la photo, même boîte affichée).
  await gallery
    .getByRole("button", { name: /^Ouvrir le rendu : photo 1/ })
    .click();
  const viewer = page.getByRole("dialog", { name: "Visionneuse des rendus" });
  await expect(viewer.getByText("2 sur 3")).toBeVisible();
  await expect(
    viewer.getByText("Photo réelle (photo 1)", { exact: true }),
  ).toBeVisible();
  await expect(
    viewer.getByText(
      "Même point de vue que la photo 1 : l'avant/après est fidèle.",
    ),
  ).toBeVisible();
  const layers = viewer.locator(".rview-stage img.rview-layer");
  await expect(layers).toHaveCount(2);
  await expect
    .poll(() =>
      layers.evaluateAll((imgs) =>
        (imgs as HTMLImageElement[]).map((i) =>
          i.complete && i.naturalWidth > 0
            ? i.naturalWidth / i.naturalHeight
            : 0,
        ),
      ),
    )
    .toEqual([SIZE[0] / SIZE[1], SIZE[0] / SIZE[1]]);
  const [before, after] = [
    await layers.nth(0).boundingBox(),
    await layers.nth(1).boundingBox(),
  ];
  expect(before).toEqual(after);
  await viewer.getByRole("slider", { name: "Avant / après" }).fill("35");
  await expect
    .poll(() =>
      viewer
        .locator(".rview-stage")
        .evaluate((el) =>
          getComputedStyle(el).getPropertyValue("--rview-cut").trim(),
        ),
    )
    .toBe("35%");
  await expect(viewer.getByRole("button", { name: /Ajouter aux favoris/ })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await shot(page, "1-avant-apres");
  // 4. Clavier dans la visionneuse : ← → puis Échap (le curseur garde ses flèches : on le
  // quitte d'abord).
  await viewer.locator(".rview-title").click();
  await page.keyboard.press("ArrowRight");
  await expect(viewer.getByText("3 sur 3")).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(viewer.getByText("2 sur 3")).toBeVisible();
  // L'étoile, aussi dans la visionneuse.
  await viewer.getByRole("button", { name: /Ajouter aux favoris/ }).click();
  await expect(
    viewer.getByRole("button", { name: /Favori du projet/ }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  await expect(frame.getByRole("button", { name: /Favoris · 3/ })).toBeVisible();

  // 3. Côte à côte : « Comparer » ; un seul rendu ne suffit pas ; trois s'ouvrent.
  await frame.getByRole("button", { name: "Comparer", exact: true }).click();
  const tools = frame.getByRole("group", { name: "Comparer" });
  await expect(tools.getByText("Choisissez 2 à 4 rendus")).toBeVisible();
  const choose = gallery.getByRole("button", { name: /^Choisir pour comparer/ });
  await expect(choose).toHaveCount(3);
  await choose.nth(0).click();
  await expect(choose.nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(tools.getByRole("button", { name: "Comparer" })).toBeDisabled();
  await choose.nth(1).click();
  await choose.nth(2).click();
  await tools.getByRole("button", { name: "Comparer les 3 rendus" }).click();
  const compare = page.getByRole("dialog", { name: "Comparer les rendus" });
  await expect(compare.getByText("Comparer 3 rendus")).toBeVisible();
  const panes = compare.getByRole("group", { name: /^Rendu \d/ });
  await expect(panes).toHaveCount(3);
  // Le « Guidé par » (KAN-97) sous chacun, dans l'ordre des choix (le plus récent d'abord).
  await expect(panes.nth(0)).toContainText("Guidé par : bohème");
  await expect(panes.nth(1)).toContainText(
    "Guidé par : industriel, touches de noir",
  );
  await expect(panes.nth(2)).toContainText("Guidé par : scandinave, lumineux");
  // Mêmes cadres, même taille.
  const boxes = await compare
    .locator(".rcomp-stage")
    .evaluateAll((els) =>
      els.map((e) => [
        Math.round(e.getBoundingClientRect().width),
        Math.round(e.getBoundingClientRect().height),
      ]),
    );
  expect(new Set(boxes.map((b) => b.join("x"))).size).toBe(1);
  await expect
    .poll(() =>
      compare
        .locator("img.rcomp-img")
        .evaluateAll((imgs) =>
          (imgs as HTMLImageElement[]).every((i) => i.complete && i.naturalWidth > 0),
        ),
    )
    .toBe(true);
  // Zoom : bouton, puis molette sur un rendu ; les trois suivent.
  const zoom = compare.getByRole("group", { name: "Zoom" });
  await zoom.getByRole("button", { name: "Zoomer", exact: true }).click();
  await expect(zoom.getByLabel("Niveau de zoom")).toHaveText("125 %");
  let t = await transforms(compare);
  expect(new Set(t).size).toBe(1);
  expect(t[0]).not.toBe("none");
  const stage0 = compare.locator(".rcomp-stage").nth(0);
  const box0 = (await stage0.boundingBox())!;
  await page.mouse.move(box0.x + box0.width * 0.7, box0.y + box0.height * 0.6);
  await page.mouse.wheel(0, -300);
  await expect(zoom.getByLabel("Niveau de zoom")).not.toHaveText("125 %");
  t = await transforms(compare);
  expect(new Set(t).size).toBe(1);
  // Déplacement : glisser le deuxième rendu déplace les trois.
  const before2 = t[0];
  const stage1 = compare.locator(".rcomp-stage").nth(1);
  const box1 = (await stage1.boundingBox())!;
  await page.mouse.move(box1.x + box1.width / 2, box1.y + box1.height / 2);
  await page.mouse.down();
  await page.mouse.move(box1.x + box1.width / 2 + 40, box1.y + box1.height / 2 + 30, {
    steps: 4,
  });
  await page.mouse.up();
  t = await transforms(compare);
  expect(new Set(t).size).toBe(1);
  expect(t[0]).not.toBe(before2);
  await shot(page, "3-cote-a-cote");
  await zoom.getByRole("button", { name: "Ajuster" }).click();
  await expect(zoom.getByLabel("Niveau de zoom")).toHaveText("100 %");

  // 4. Clavier : un clic sur le deuxième le rend actif (et rend le focus à la vue, pas à
  // « Ajuster ») ; → le troisième, puis le premier (en boucle), ← revient.
  await compare.locator(".rcomp-stage").nth(1).click();
  await expect(panes.nth(1)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("ArrowRight");
  await expect(panes.nth(2)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("ArrowRight");
  await expect(panes.nth(0)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(panes.nth(2)).toHaveAttribute("aria-current", "true");
  await page.keyboard.press("ArrowLeft");
  await expect(panes.nth(1)).toHaveAttribute("aria-current", "true");
  // Entrée ouvre le rendu actif seul (le deuxième choisi : la photo 1).
  await page.keyboard.press("Enter");
  await expect(compare).toHaveCount(0);
  await expect(viewer).toBeVisible();
  await expect(viewer.getByText(/point de vue exact de la photo 1/)).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);
  // Les choix restent : on rouvre, puis Échap ferme la vue comparée.
  await tools.getByRole("button", { name: "Comparer les 3 rendus" }).click();
  await expect(compare).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(compare).toHaveCount(0);
  await tools.getByRole("button", { name: "Annuler" }).click();
  await expect(frame.getByRole("button", { name: "Comparer", exact: true })).toBeVisible();
  expect(failures).toEqual([]);
});

test("rendus ouverts aux membres : la lectrice voit, la commentatrice réagit, la non-membre reçoit 404", async ({
  browser,
  baseURL,
}) => {
  test.setTimeout(180_000);
  const launcher = await person(browser, "198.51.100.253");
  await gotoApp(launcher, "/");
  await signInHere(launcher, RENDER_ACCOUNT.email);
  const origin = { Origin: new URL(launcher.url()).origin };
  const { id, level, camera } = await project(launcher);
  const asked = await launcher.request.post(
    `/api/projects/${id}/levels/${level}/realistic`,
    { data: { camera_id: camera }, headers: origin },
  );
  expect(asked.status()).toBe(201);
  const worker = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: {
      Authorization: `Bearer ${E2E_WORKER_TOKEN}`,
      "X-Forwarded-For": "198.51.100.214",
    },
  });
  await deliverProject(worker, launcher, id, ["japandi"]);
  await worker.dispose();
  const rid = ((await asked.json()) as { id: string }).id;
  const starred = await launcher.request.put(
    `/api/projects/${id}/realistic/${rid}/favorite`,
    { headers: origin },
  );
  expect(starred.status()).toBe(200);
  const invitation = (await (
    await launcher.request.post(`/api/projects/${id}/invitations`, {
      data: { role: "viewer" },
      headers: origin,
    })
  ).json()) as { token: string };

  // Kim, lectrice (hors liste blanche) : la galerie du projet, l'étoile lue, rien à lancer.
  const run = Date.now().toString(36);
  const kim = await person(browser, "198.51.100.254");
  await gotoApp(kim, `/#/invitation/${invitation.token}`);
  await signInHere(kim, `kim-${run}.kan99@example.org`);
  await gotoApp(kim, `/#/projects/${id}/board`);
  const frame = kim
    .getByRole("region", { name: "Espace de travail" })
    .getByRole("region", { name: "Rendus réalistes" });
  const gallery = frame.getByRole("list", { name: "Galerie des rendus" });
  await expect(gallery.locator("img.rgal-img")).toHaveCount(1, {
    timeout: 30_000,
  });
  await expect
    .poll(() =>
      gallery
        .locator("img.rgal-img")
        .evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth),
    )
    .toBeGreaterThan(0);
  await expect(frame.getByText("Guidé par : japandi")).toBeVisible();
  await expect(frame.getByRole("img", { name: "Favori" })).toBeVisible();
  await expect(frame.getByRole("button", { name: "Rendre ▾" })).toHaveCount(0);
  await expect(
    frame.getByRole("button", { name: /favoris :/ }),
  ).toHaveCount(0);
  const seen = (await (
    await kim.request.get(`/api/projects/${id}/realistic`)
  ).json()) as Listing;
  expect(seen.requests.map((r) => [r.id, r.mine])).toEqual([[rid, false]]);
  expect(seen.favorite_ids).toEqual([rid]);
  const kimOrigin = { Origin: new URL(kim.url()).origin };
  const refused = await kim.request.put(
    `/api/projects/${id}/realistic/${rid}/favorite`,
    { headers: kimOrigin },
  );
  expect(refused.status()).toBe(403);
  // La visionneuse : ni couverture, ni variante (le rendu n'est pas le sien).
  await gallery.getByRole("button", { name: /^Ouvrir le rendu/ }).click();
  const viewer = kim.getByRole("dialog", { name: "Visionneuse des rendus" });
  await expect(viewer).toBeVisible();
  await expect(viewer.getByText("Favori du projet")).toBeVisible();
  await expect(
    viewer.getByRole("button", { name: /couverture du projet/ }),
  ).toHaveCount(0);
  await expect(
    viewer.getByRole("button", { name: /Nouvelle variante/ }),
  ).toHaveCount(0);
  // Lectrice : les pastilles de réaction se voient, inactives.
  await expect(viewer.getByRole("button", { name: "J'adore" })).toBeDisabled();
  await kim.keyboard.press("Escape");
  await expect(viewer).toHaveCount(0);

  // Céline, commentatrice (hors liste blanche) : elle réagit au rendu (décision du 3 nov.).
  const asCommenter = (await (
    await launcher.request.post(`/api/projects/${id}/invitations`, {
      data: { role: "commenter" },
      headers: origin,
    })
  ).json()) as { token: string };
  const celine = await person(browser, "198.51.100.215");
  await gotoApp(celine, `/#/invitation/${asCommenter.token}`);
  await signInHere(celine, `celine-${run}.kan99@example.org`);
  await gotoApp(celine, `/#/projects/${id}/board`);
  const celineGallery = celine
    .getByRole("region", { name: "Espace de travail" })
    .getByRole("region", { name: "Rendus réalistes" })
    .getByRole("list", { name: "Galerie des rendus" });
  await celineGallery
    .getByRole("button", { name: /^Ouvrir le rendu/ })
    .click({ timeout: 30_000 });
  const celineViewer = celine.getByRole("dialog", {
    name: "Visionneuse des rendus",
  });
  await celineViewer.getByRole("button", { name: "J'adore" }).click();
  await expect(
    celineViewer.getByRole("button", { name: "J'adore (1)" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    celineViewer.getByRole("button", { name: /couverture du projet/ }),
  ).toHaveCount(0);

  // Lou, connectée mais pas membre : 404 (le projet n'est pas révélé).
  const lou = await person(browser, "198.51.100.255");
  await gotoApp(lou, "/");
  await signInHere(lou, `lou-${run}.kan99@example.org`);
  expect(
    (await lou.request.get(`/api/projects/${id}/realistic`)).status(),
  ).toBe(404);
  expect(
    (await lou.request.get(`/api/projects/${id}/realistic/${rid}/image`)).status(),
  ).toBe(404);
});
