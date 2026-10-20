import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./app-ready";
import { showPanel } from "./helpers";

/** KAN-32 : l'assistant supprime une porte déjà posée (lecteur à règles, sans modèle), le
 *  tour le dit, et « Annuler » la remet. Le plan est préparé par l'API (quatre murs, la pièce,
 *  une porte estimée : l'assistant ne supprime pas ce que l'utilisateur a posé lui-même). */

type Json = Record<string, unknown>;

async function openingsOf(page: Page, id: string): Promise<Json[]> {
  const project = (await (
    await page.request.get(`/api/projects/${id}`)
  ).json()) as { levels: Record<string, { elements: Record<string, Json> }> };
  return Object.values(Object.values(project.levels)[0]!.elements).filter(
    (e) => e.type === "opening",
  );
}

async function planWithADoor(page: Page): Promise<string> {
  const made = await page.request.post("/api/projects", {
    data: { name: "E2E assistant ouvertures" },
  });
  const project = (await made.json()) as {
    id: string;
    levels: Record<string, unknown>;
  };
  const level = Object.keys(project.levels)[0]!;
  const ops = `/api/projects/${project.id}/levels/${level}/ops`;
  const corners = [
    [0, 0],
    [4, 0],
    [4, 3],
    [0, 3],
  ] as const;
  for (let i = 0; i < 4; i++) {
    const [ax, ay] = corners[i]!;
    const [bx, by] = corners[(i + 1) % 4]!;
    const r = await page.request.post(ops, {
      data: { op: "add_wall", start: { x: ax, y: ay }, end: { x: bx, y: by } },
    });
    expect(r.ok()).toBe(true);
  }
  expect(
    (await page.request.post(ops, { data: { op: "detect_rooms" } })).ok(),
  ).toBe(true);
  const plan = (await (
    await page.request.get(`/api/projects/${project.id}`)
  ).json()) as {
    levels: Record<
      string,
      {
        elements: Record<string, Json>;
        vertices: Record<string, { position: { x: number; y: number } }>;
      }
    >;
  };
  const lvl = plan.levels[level]!;
  const south = Object.values(lvl.elements).find(
    (e) =>
      e.type === "wall" &&
      lvl.vertices[e.start_vertex as string]!.position.y === 0 &&
      lvl.vertices[e.end_vertex as string]!.position.y === 0,
  )!;
  const door = await page.request.post(ops, {
    data: {
      op: "add_opening",
      host_wall: south.id,
      kind: "door",
      offset: 1.5,
      width: 0.83,
      height: 2.04,
      swing: { hinge: "left", direction: "inward", angle: 1.5708 },
      source: "estimated",
      confidence: 0.6,
    },
  });
  expect(door.ok()).toBe(true);
  return project.id;
}

test("assistant : « supprime la porte », puis Annuler la remet", async ({
  page,
}) => {
  const id = await planWithADoor(page);
  expect(await openingsOf(page, id)).toHaveLength(1);
  await gotoApp(page, `/#/projects/${id}/import`);
  await page.getByRole("button", { name: "Continuer vers le plan" }).click();
  await expect(page.locator("polygon.wall")).toHaveCount(4);
  await showPanel(page);
  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  const input = page.getByLabel("Votre demande à l'assistant");
  await input.fill("supprime la porte");
  await input.press("Enter");
  const last = page.locator(".assistant .bubble.assistant").last();
  await expect(last.locator(".actions .status-done")).toHaveCount(1);
  await expect(last).toContainText("La porte sud supprimée");
  await expect.poll(async () => (await openingsOf(page, id)).length).toBe(0);

  await last.getByRole("button", { name: "Annuler", exact: true }).click();
  await expect(last).toContainText("annulé");
  await expect.poll(async () => (await openingsOf(page, id)).length).toBe(1);
  const [door] = await openingsOf(page, id);
  expect(door!.kind).toBe("door");
  expect(door!.offset).toBeCloseTo(1.5);
});
