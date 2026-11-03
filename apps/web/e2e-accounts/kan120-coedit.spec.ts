import { expect, test, type Browser, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { gotoApp } from "../e2e/app-ready";

/** KAN-120, avec les comptes : le plan régénéré par un éditeur (une seule écriture, à la fin
 *  de la tâche) arrive chez un autre éditeur sans recharger. On attend la VALEUR du serveur
 *  (le curseur d'historique après la tâche, que porte l'image du plan), pas un simple avis :
 *  sans l'annonce de fin de tâche, l'autre éditeur garderait l'ancien plan et l'écraserait.
 *  Chaque personne a sa propre adresse (limite des connexions par adresse). */

const PNG = readFileSync(
  path.resolve("../../tests/fixtures/rect_room/image.png"),
);

async function person(browser: Browser, ip: string): Promise<Page> {
  const context = await browser.newContext({
    baseURL: test.info().project.use.baseURL,
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: { "X-Forwarded-For": ip },
  });
  return context.newPage();
}

/** Connexion par code, sur l'accueil ou sur une invitation. */
async function signIn(page: Page, email: string, at = "/"): Promise<void> {
  await gotoApp(page, at);
  await page.getByLabel("Adresse e-mail").fill(email);
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
  const name = page.getByLabel("Votre nom (facultatif)");
  await expect(name.or(page.locator(".editor, .boards"))).toBeVisible({
    timeout: 30_000,
  });
  if (await name.isVisible()) {
    await name.fill(email.split(".")[0]!.replace(/^./, (c) => c.toUpperCase()));
    await page.getByRole("button", { name: "Continuer" }).click();
  }
}

/** La photo de la fixture réduite à 640 px en JPEG : le serveur de ces tests a 1 Mo de place
 *  par propriétaire (OPENPLAN_QUOTA_MB=1). */
async function smallPhoto(page: Page): Promise<Buffer> {
  // Une page vierge : la CSP de l'application n'y joue pas (image en data:).
  const blank = await page.context().newPage();
  await blank.goto("about:blank");
  const b64 = await blank.evaluate(async (src) => {
    const img = new Image();
    img.src = `data:image/png;base64,${src}`;
    await img.decode();
    const canvas = document.createElement("canvas");
    canvas.width = 640;
    canvas.height = Math.round((640 * img.height) / img.width);
    canvas.getContext("2d")!.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/jpeg", 0.8).split(",")[1]!;
  }, PNG.toString("base64"));
  await blank.close();
  return Buffer.from(b64, "base64");
}

type Job = { id: string; status: string; error: string | null };

async function finished(page: Page, pid: string, job: Job): Promise<Job> {
  let now = job;
  await expect
    .poll(
      async () => {
        now = (await (
          await page.request.get(`/api/projects/${pid}/jobs/${job.id}`)
        ).json()) as Job;
        return now.status;
      },
      { timeout: 180_000, intervals: [500] },
    )
    .not.toMatch(/^(queued|running)$/);
  return now;
}

async function cursorOf(page: Page, pid: string): Promise<number> {
  const p = (await (await page.request.get(`/api/projects/${pid}`)).json()) as {
    history_cursor: number;
  };
  return p.history_cursor;
}

test("KAN-120 : le plan régénéré par A arrive chez B sans recharger", async ({
  browser,
}) => {
  test.setTimeout(360_000);
  const gilles = await person(browser, "198.51.100.168");
  await signIn(gilles, "gilles.kan120@example.org");
  const origin = { Origin: new URL(gilles.url()).origin };
  const made = await gilles.request.post("/api/projects", {
    data: { name: "Régénérer à deux" },
    headers: origin,
  });
  const { id, levels } = (await made.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const lid = Object.keys(levels)[0]!;
  // Une photo dans le node Photos du plan, puis un premier plan (comme le bouton).
  const upload = await gilles.request.post(
    `/api/projects/${id}/images?level_id=${lid}`,
    {
      multipart: {
        file: { name: "piece.jpg", mimeType: "image/jpeg", buffer: await smallPhoto(gilles) },
      },
      headers: origin,
    },
  );
  expect(upload.status()).toBe(201);
  const { image, kind } = (await upload.json()) as {
    image: { id: string };
    kind: string;
  };
  expect(kind).toBe("photo");
  const autoplan = `/api/projects/${id}/levels/${lid}/images/${image.id}/autoplan`;
  const first = await gilles.request.post(autoplan, { headers: origin });
  expect(first.status()).toBe(202);
  expect((await finished(gilles, id, (await first.json()) as Job)).status).toBe("done");

  const token = (
    (await (
      await gilles.request.post(`/api/projects/${id}/invitations`, {
        data: { role: "editor" },
        headers: origin,
      })
    ).json()) as { token: string }
  ).token;
  const fanny = await person(browser, "198.51.100.169");
  await signIn(fanny, "fanny.kan120@example.org", `/#/invitation/${token}`);
  await gotoApp(fanny, `/#/projects/${id}`);
  const plan = fanny
    .getByRole("region", { name: "Espace de travail" })
    .locator(".board-item.kind-plan img.board-plan");
  const before = await cursorOf(gilles, id);
  await expect(plan).toHaveAttribute("src", new RegExp(`[?&]v=${before}$`), {
    timeout: 30_000,
  });

  // Gilles régénère ; Fanny ne recharge rien.
  const again = await gilles.request.post(`${autoplan}?replace=true`, { headers: origin });
  expect(again.status()).toBe(202);
  expect((await finished(gilles, id, (await again.json()) as Job)).status).toBe("done");
  const after = await cursorOf(gilles, id);
  expect(after).toBe(before + 1); // une seule étape d'annulation
  await expect(plan).toHaveAttribute("src", new RegExp(`[?&]v=${after}$`), {
    timeout: 10_000,
  });
});
