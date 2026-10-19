import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { createEmptyProject, expectSpace, goSpace } from "./helpers";
import { reloadApp } from "./app-ready";

/** C1 : vidéo, PDF et liens sur le tableau, sans aucune requête vers l'extérieur ; un fichier
 *  joint par l'API entre dans le cadre Fichiers ; la vignette du plan suit le thème. */
const PLAN_DXF = path.resolve("../../tests/fixtures/plan_rect.dxf");
const PDF = path.resolve("../../tests/fixtures/plan_rect.pdf");
const MP4 = Buffer.concat([
  Buffer.from([0, 0, 0, 0x18]),
  Buffer.from("ftypmp42"),
  Buffer.alloc(64),
]);

test("médias : vidéo, PDF avec vignette, lien jamais récupéré, fichiers de l'API, plan au thème", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const outside: string[] = [];
  page.on("request", (r) => {
    const host = new URL(r.url()).hostname;
    if (!["127.0.0.1", "localhost"].includes(host)) outside.push(host);
  });
  const id = await createEmptyProject(page, "Médias");
  await page
    .locator(".import-screen .dropzone input[type=file]")
    .setInputFiles(PLAN_DXF);
  await expectSpace(page, "plan", { timeout: 60_000 });
  await goSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });

  // Vidéo et PDF importés depuis le tableau.
  await board.locator(".board-rail input[type=file]").setInputFiles([
    { name: "visite.mp4", mimeType: "video/mp4", buffer: MP4 },
    {
      name: "plan.pdf",
      mimeType: "application/pdf",
      buffer: readFileSync(PDF),
    },
  ]);
  await expect(board.getByLabel("Vidéo visite.mp4")).toBeVisible();
  const pdf = board.locator(".board-item.kind-pdf");
  await expect(pdf.locator("img.board-pdf")).toBeVisible();
  await expect(pdf.getByRole("link", { name: "Ouvrir" })).toHaveAttribute(
    "rel",
    /noopener/,
  );

  // Un lien : adresse et titre saisis ; refusé s'il n'est pas http(s).
  await board.getByRole("button", { name: "Lien", exact: true }).click();
  const form = board.getByRole("dialog", { name: "Ajouter un lien" });
  await form.getByLabel("Adresse").fill("javascript:alert(1)");
  await form.getByRole("button", { name: "Ajouter le lien" }).click();
  await expect(board.getByRole("alert")).toContainText("http:// ou https://");
  await form.getByLabel("Adresse").fill("https://www.exemple.fr/canape?ref=12");
  await form.getByLabel("Titre").fill("Canapé lin");
  await form.getByRole("button", { name: "Ajouter le lien" }).click();
  const link = board.locator(".board-item.kind-link");
  await expect(link).toContainText("www.exemple.fr");
  await expect(link).toContainText("Canapé lin");
  await expect(link.getByRole("link", { name: "Ouvrir ↗" })).toHaveAttribute(
    "rel",
    "noopener noreferrer",
  );

  await expect(
    page.getByRole("status", { name: "Enregistrement", exact: true }),
  ).toHaveText("Enregistré");

  // Un fichier joint par l'API entre dans le cadre Fichiers à la synchronisation.
  await page.request.post(`/api/projects/${id}/media`, {
    multipart: {
      file: {
        name: "notes.txt",
        mimeType: "text/plain",
        buffer: Buffer.from("mesures"),
      },
    },
  });
  await reloadApp(page);
  await expectSpace(page, "board");
  await expect(
    board.locator(".board-item.kind-file", { hasText: "notes.txt" }),
  ).toBeVisible();
  await expect(board.locator(".board-item.kind-link")).toContainText(
    "Canapé lin",
  );

  // Vignette du plan : l'export aux couleurs du thème affiché, sans filtre.
  const thumb = board.locator("img.board-plan");
  await page.emulateMedia({ colorScheme: "dark" });
  await expect(thumb).toHaveAttribute("src", /theme=dark/);
  await page.emulateMedia({ colorScheme: "light" });
  await expect(thumb).not.toHaveAttribute("src", /theme=dark/);
  expect(outside).toEqual([]);
});
