import { randomUUID } from "node:crypto";
import { expect } from "@playwright/test";
import { gotoApp } from "../../e2e/app-ready";
import { origin, passNameStep, person, requestCode, signIn } from "./comptes";
import { Given, Then, When, type Ctx } from "./fixtures";

/** KAN-103, pilote (b) : les rôles d'un projet partagé (éditeur, lecteur, non-membre), avec de
 *  vrais liens d'invitation. Une adresse IP par personne, propre à ce fichier. */

const IPS: Record<string, string> = {
  Alice: "198.51.100.141",
  Bruno: "198.51.100.142",
  Chloé: "198.51.100.143",
  Denis: "198.51.100.144",
};
const NOTICE_IMPORT = /Importer/;

function who(ctx: Ctx, name: string) {
  const p = ctx.people.get(name);
  if (!p) throw new Error(`${name} n'est pas encore là dans ce scénario`);
  return p;
}

function projectId(ctx: Ctx): string {
  if (!ctx.project) throw new Error("aucun projet : « Alice a créé le projet » manque");
  return ctx.project.id;
}

/** Une note posée par l'API du tableau, comme le fait l'espace de travail. */
function note(text: string) {
  return { id: randomUUID(), kind: "note", x: 40, y: 40, w: 200, h: 120, text };
}

Given("{word} a créé le projet {string}", async ({ browser, baseURL, ctx }, name: string, title: string) => {
  const owner = await person(browser, baseURL, ctx, name, IPS[name]!);
  await signIn(owner, name);
  const made = await owner.page.request.post("/api/projects", {
    data: { name: title },
    headers: origin(owner.page),
  });
  expect(made.status()).toBe(201);
  const body = (await made.json()) as { id: string; levels: Record<string, unknown> };
  ctx.project = { id: body.id, level: Object.keys(body.levels)[0]! };
});

// « Et qu' » est un mot-clé du Gherkin français : le pas commence à « elle ».
Given("elle a invité {word} comme {rôle}", async ({ ctx }, guest: string, role: string) => {
  const owner = who(ctx, "Alice");
  const invite = await owner.page.request.post(`/api/projects/${projectId(ctx)}/invitations`, {
    data: { role },
    headers: origin(owner.page),
  });
  expect(invite.status()).toBe(201);
  ctx.invitations.set(guest, ((await invite.json()) as { token: string }).token);
});

When("{word} ouvre le projet par son invitation", async ({ browser, baseURL, ctx }, name: string) => {
  const token = ctx.invitations.get(name);
  if (!token) throw new Error(`${name} n'a pas d'invitation`);
  const guest = await person(browser, baseURL, ctx, name, IPS[name]!);
  await guest.page.goto(`/#/invitation/${token}`);
  await guest.page.getByLabel("Code à 6 chiffres").fill(await requestCode(guest.page, guest.email));
  await guest.page.getByRole("button", { name: "Se connecter" }).click();
  await passNameStep(guest.page, name);
  await expect(guest.page.locator(".editor")).toBeVisible({ timeout: 30_000 });
  await gotoApp(guest.page, `/#/projects/${projectId(ctx)}/board`);
  await expect(guest.page.locator(".board-stage")).toBeVisible({ timeout: 30_000 });
});

Then("il peut importer des photos", async ({ ctx }) => {
  const page = who(ctx, "Bruno").page;
  await expect(page.locator(".editor.read-only")).toHaveCount(0);
  await expect(
    page.locator(".board-item.kind-frame.role-photos").getByRole("button", { name: NOTICE_IMPORT }),
  ).toBeVisible();
});

Then("la note qu'il ajoute est enregistrée", async ({ ctx }) => {
  const page = who(ctx, "Bruno").page;
  const mine = note("Note de Bruno");
  const out = await page.request.patch(`/api/projects/${projectId(ctx)}/board/items`, {
    data: { set: [mine] },
    headers: origin(page),
  });
  expect(out.status()).toBe(200);
  const board = (await (await page.request.get(`/api/projects/${projectId(ctx)}/board`)).json()) as {
    items: { id: string }[];
  };
  expect(board.items.map((i) => i.id)).toContain(mine.id);
});

When("elle colle un texte dans l'espace de travail", async ({ ctx }) => {
  const page = who(ctx, "Chloé").page;
  ctx.before = await page.locator(".board-item").count();
  // Comme KAN-50 (4) : un collage, que l'écran d'un lecteur refuse avec un message court.
  await page.evaluate(() => {
    const data = new DataTransfer();
    data.setData("text/plain", "Une note collée");
    window.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data }));
  });
});

Then("elle voit {string}", async ({ ctx }, text: string) => {
  await expect(who(ctx, "Chloé").page.getByText(text)).toBeVisible();
});

Then("rien n'est ajouté à l'espace de travail", async ({ ctx }) => {
  const page = who(ctx, "Chloé").page;
  await page.waitForTimeout(1_000);
  await expect(page.locator(".board-item")).toHaveCount(ctx.before ?? -1);
});

Then("elle ne peut rien importer", async ({ ctx }) => {
  const page = who(ctx, "Chloé").page;
  await expect(page.locator(".editor.read-only")).toBeVisible({ timeout: 30_000 });
  for (const role of ["photos", "inspirations"])
    await expect(
      page.locator(`.board-item.kind-frame.role-${role}`).getByRole("button", { name: NOTICE_IMPORT }),
    ).toHaveCount(0);
});

Then("le serveur refuse la note qu'elle tente d'ajouter", async ({ ctx }) => {
  const page = who(ctx, "Chloé").page;
  const out = await page.request.patch(`/api/projects/${projectId(ctx)}/board/items`, {
    data: { set: [note("Note de Chloé")] },
    headers: origin(page),
  });
  expect(out.status()).toBe(403);
});

When("{word}, qui n'est pas membre, ouvre l'adresse du projet", async ({ browser, baseURL, ctx }, name: string) => {
  const outsider = await person(browser, baseURL, ctx, name, IPS[name]!);
  await signIn(outsider, name);
  await gotoApp(outsider.page, `/#/projects/${projectId(ctx)}/board`);
});

Then("le serveur lui refuse le projet", async ({ ctx }) => {
  const page = who(ctx, "Denis").page;
  const status = (await page.request.get(`/api/projects/${projectId(ctx)}`)).status();
  expect([403, 404]).toContain(status);
  const board = await page.request.get(`/api/projects/${projectId(ctx)}/board`);
  expect([403, 404]).toContain(board.status());
});

Then("l'espace de travail ne s'ouvre pas pour lui", async ({ ctx }) => {
  const page = who(ctx, "Denis").page;
  await page.waitForTimeout(2_000);
  await expect(page.locator(".board-item")).toHaveCount(0);
  await expect(page.getByText("Salon du pilote")).toHaveCount(0);
});
