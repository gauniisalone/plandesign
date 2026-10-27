import { expect, type APIRequestContext } from "@playwright/test";

/** KAN-100 : un espace de travail posé comme avant KAN-98 L2, pour tester « Ranger ». */

export type BoardRow = {
  id: string;
  kind: string;
  role: string | null;
  frame: string | null;
  x: number;
  y: number;
  w: number;
  h: number;
};

export const readItems = async (request: APIRequestContext, id: string) =>
  (
    (await (await request.get(`/api/projects/${id}/board`)).json()) as {
      items: BoardRow[];
    }
  ).items;

/** L'espace d'un projet neuf, reposé à l'ancienne : nodes de 624, Moodboard sous Photos dans
 *  la colonne de gauche, plan à 720, Rendus collé à droite du plan. */
export async function toLegacy(
  request: APIRequestContext,
  id: string,
  headers: Record<string, string>,
): Promise<void> {
  const items = await readItems(request, id);
  const frame = (role: string) =>
    items.find((i) => i.kind === "frame" && i.role === role)!;
  const plan = items.find((i) => i.kind === "plan")!;
  const r = await request.patch(`/api/projects/${id}/board/items`, {
    data: {
      set: [
        { ...frame("photos"), x: 0, y: 0, w: 624, h: 374 },
        { ...frame("inspirations"), x: 0, y: 422, w: 624, h: 374 },
        { ...plan, x: 720, y: 0 },
        { ...frame("renders"), x: 1668, y: 0, w: 624, h: 700 },
      ],
    },
    headers,
  });
  expect(r.status()).toBe(200);
}
