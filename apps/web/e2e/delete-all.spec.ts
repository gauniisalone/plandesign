import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";

// KAN-119 : « Supprimer tous mes projets » (menu « Plus » de l'accueil). La liste et les
// DELETE sont simulés : aucun projet réel du serveur de test n'est supprimé.

const summary = (id: string, name: string, role?: string) => ({
  id,
  name,
  role,
  updated_at: "2026-11-04T10:00:00Z",
  created_at: "2026-11-04T09:00:00Z",
  path: null,
  image_count: 0,
  wall_count: 0,
  has_thumbnail: false,
});

/** Simule la galerie ; `failing` : ids dont le DELETE échoue. Rend les ids supprimés. */
async function fakeGallery(
  page: Page,
  list: ReturnType<typeof summary>[],
  failing: string[] = [],
): Promise<string[]> {
  const deleted: string[] = [];
  await page.route(/\/api\/projects$/, (route) =>
    route.request().method() === "GET"
      ? route.fulfill({
          json: list.filter((p) => !deleted.includes(p.id)),
        })
      : route.fallback(),
  );
  await page.route(/\/api\/projects\/[^/]+$/, (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    const id = route.request().url().split("/").pop() ?? "";
    if (failing.includes(id))
      return route.fulfill({ status: 500, json: { detail: "panne" } });
    deleted.push(id);
    return route.fulfill({ status: 204 });
  });
  return deleted;
}

async function openDeleteAll(page: Page) {
  await page.getByRole("button", { name: "⋯ Plus" }).click();
  await page
    .getByRole("menuitem", { name: "Supprimer tous mes projets…" })
    .click();
  return page.getByRole("alertdialog");
}

test("tout supprimer : SUPPRIMER à saisir, seuls les projets à soi partent", async ({
  page,
}) => {
  const deleted = await fakeGallery(page, [
    summary("a", "Séjour", "owner"),
    summary("b", "Combles", "owner"),
    summary("c", "Chambre", "editor"),
  ]);
  await gotoApp(page, "/");
  const dialog = await openDeleteAll(page);
  await expect(dialog).toContainText("Supprimer vos 2 projets ?");
  await expect(dialog).toContainText("« Séjour » et « Combles »");
  await expect(dialog).toContainText("les projets partagés avec vous (1)");
  const go = dialog.getByRole("button", { name: "Supprimer mes 2 projets" });
  const field = dialog.getByLabel(/Pour confirmer, saisissez SUPPRIMER/);
  await expect(go).toBeDisabled();
  await field.fill("SUPPRIME");
  await field.press("Enter"); // Entrée ne valide pas avant le mot exact
  await expect(go).toBeDisabled();
  expect(deleted).toEqual([]);
  await field.fill("  supprimer ");
  await expect(go).toBeEnabled();
  await go.click();
  await expect(page.locator(".boards-notice")).toHaveText(
    "2 projets supprimés. La place est libérée.",
  );
  await expect(dialog).toBeHidden();
  expect(deleted).toEqual(["a", "b"]);
  await expect(page.locator(".boards-cards")).toContainText("Chambre");
});

test("tout supprimer : Échap vaut Annuler, rien ne part", async ({ page }) => {
  const deleted = await fakeGallery(page, [summary("a", "Séjour", "owner")]);
  await gotoApp(page, "/");
  const dialog = await openDeleteAll(page);
  await expect(dialog).toContainText("Supprimer votre projet ?");
  await dialog.getByLabel(/Pour confirmer/).fill("SUPPRIMER");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  expect(deleted).toEqual([]);
});

test("tout supprimer : un échec n'arrête pas les autres et le message le nomme", async ({
  page,
}) => {
  const deleted = await fakeGallery(
    page,
    [summary("a", "Séjour", "owner"), summary("b", "Combles", "owner")],
    ["b"],
  );
  await gotoApp(page, "/");
  const dialog = await openDeleteAll(page);
  await dialog.getByLabel(/Pour confirmer/).fill("SUPPRIMER");
  await dialog.getByRole("button", { name: "Supprimer mes 2 projets" }).click();
  await expect(page.locator(".boards-notice")).toHaveText(
    "1 supprimé, 1 en échec : « Combles » reste, réessayez.",
  );
  expect(deleted).toEqual(["a"]);
  await expect(page.locator(".boards-cards")).toContainText("Combles");
});

test("tout supprimer : au-delà de 10 projets, la liste se replie", async ({
  page,
}) => {
  await fakeGallery(
    page,
    Array.from({ length: 12 }, (_, i) => summary(`p${i}`, `Projet ${i}`)),
  );
  await gotoApp(page, "/");
  const dialog = await openDeleteAll(page);
  await expect(dialog).toContainText("Supprimer vos 12 projets ?");
  const more = dialog.locator("details");
  await expect(more.locator("summary")).toHaveText("et 2 autres…");
  await expect(more).not.toHaveAttribute("open");
});

test("tout supprimer : sans projet à soi, l'entrée est grisée et dit pourquoi", async ({
  page,
}) => {
  await fakeGallery(page, [summary("c", "Chambre", "viewer")]);
  await gotoApp(page, "/");
  await page.getByRole("button", { name: "⋯ Plus" }).click();
  const item = page.getByRole("menuitem", {
    name: "Supprimer tous mes projets…",
  });
  await expect(item).toHaveAttribute("aria-disabled", "true");
  await expect(item).toHaveAttribute(
    "title",
    "Vous n'êtes propriétaire d'aucun projet",
  );
  await item.click();
  await expect(page.getByRole("alertdialog")).toBeHidden();
});
