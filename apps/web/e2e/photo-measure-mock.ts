import type { Page } from "@playwright/test";

/** KAN-66 : l'API de la mesure de référence simulée côté navigateur (le lot 1 serveur arrive
 *  en parallèle) : le projet lu porte `scale_basis` sur son niveau et `reference_segments` sur
 *  la photo ; le PUT, le DELETE et la réestimation répondent comme le contrat du 20 oct. (le
 *  projet entier, 422 `{detail}` affiché tel quel). Tout le reste passe au vrai serveur. */

export const DEFAULT_TEXT =
  "Échelle : hauteur de prise de vue supposée 1,50 m (par défaut)";
export const SEG_ID = "00000000-0000-4000-8000-0000000066a1";

export interface MockState {
  basis: Record<string, unknown> | null;
  segments: Record<string, unknown>[];
  /** Corps des PUT reçus, dans l'ordre. */
  puts: {
    p1: number[];
    p2: number[];
    length_m: number;
    label: string | null;
  }[];
  deletes: string[];
  /** Réponses 422 à rendre avant d'accepter (une par PUT). */
  refusals: string[];
  reestimates: number;
  stamp: number;
}

export function cameraBasis(imageId: string, factor: number | null = 1) {
  return {
    source: "default",
    text: DEFAULT_TEXT,
    image_id: imageId,
    segment_id: null,
    factor,
    before: null,
    previous: null,
    camera_height_m: 1.5,
  };
}

/** Une petite photo (PNG de quelques Ko) dessinée dans le navigateur : une porte sur un mur. */
export async function smallPhoto(page: Page): Promise<Buffer> {
  const data = await page.evaluate(() => {
    const c = document.createElement("canvas");
    c.width = 800;
    c.height = 600;
    const g = c.getContext("2d");
    if (!g) return "";
    g.fillStyle = "#efe9df";
    g.fillRect(0, 0, 800, 600);
    g.fillStyle = "#b88b5e";
    g.fillRect(0, 450, 800, 150);
    g.fillStyle = "#c9b79c";
    g.fillRect(200, 180, 100, 270);
    return c.toDataURL("image/png").split(",")[1] ?? "";
  });
  return Buffer.from(data, "base64");
}

export async function mockReference(
  page: Page,
  projectId: string,
  imageId: string,
  state: MockState,
): Promise<void> {
  const projectUrl = new RegExp(`/api/projects/${projectId}$`);
  const real = async () => {
    const r = await page.request.get(`/api/projects/${projectId}`);
    return (await r.json()) as {
      levels: Record<string, Record<string, unknown>>;
      images: Record<string, Record<string, unknown>>;
      updated_at: string;
    };
  };
  const injected = async () => {
    const p = await real();
    const lid = Object.keys(p.levels)[0];
    if (lid) p.levels[lid] = { ...p.levels[lid], scale_basis: state.basis };
    const img = p.images[imageId];
    if (img) p.images[imageId] = { ...img, reference_segments: state.segments };
    p.updated_at = new Date(state.stamp).toISOString();
    return p;
  };
  await page.route(projectUrl, async (route) => {
    if (route.request().method() !== "GET") return route.fallback();
    await route.fulfill({ json: await injected() });
  });
  // À l'ouverture, l'atelier lit le projet par « nom automatique » (POST) : même injection.
  await page.route(
    new RegExp(`/api/projects/${projectId}/name/auto$`),
    async (route) => {
      const response = await route.fetch();
      if (!response.ok()) return route.fulfill({ response });
      await route.fulfill({ response, json: await injected() });
    },
  );
  await page.route(
    new RegExp(
      `/api/projects/${projectId}/images/${imageId}/reference(/[^/]+)?$`,
    ),
    async (route) => {
      const req = route.request();
      if (req.method() === "PUT") {
        const body = req.postDataJSON() as MockState["puts"][number];
        state.puts.push(body);
        const refusal = state.refusals.shift();
        if (refusal)
          return route.fulfill({ status: 422, json: { detail: refusal } });
        const label = body.label ? ` (${body.label})` : "";
        const length = body.length_m.toFixed(2).replace(".", ",");
        state.segments = [
          {
            id: SEG_ID,
            p1: body.p1,
            p2: body.p2,
            length_m: body.length_m,
            label: body.label,
            author: "local",
            created_at: "2026-10-20T15:42:00Z",
          },
        ];
        state.basis = {
          ...cameraBasis(imageId),
          source: "reference",
          text: `Échelle calée sur votre mesure de ${length} m${label}`,
          segment_id: SEG_ID,
          factor: 5 / 4.06,
          before: { length_m: 4.06, width_m: 3.2, height_m: 2.4 },
        };
      } else if (req.method() === "DELETE") {
        state.deletes.push(new URL(req.url()).pathname.split("/").pop() ?? "");
        state.segments = [];
        state.basis = {
          ...cameraBasis(imageId),
          factor: 4.06 / 5,
          before: { length_m: 5, width_m: 3.94, height_m: 2.96 },
        };
      } else return route.fallback();
      state.stamp += 1000;
      await route.fulfill({ json: await injected() });
    },
  );
  await page.route(
    new RegExp(
      `/api/projects/${projectId}/levels/[^/]+/images/${imageId}/reestimate$`,
    ),
    async (route) => {
      state.reestimates += 1;
      await route.fulfill({
        status: 202,
        json: {
          id: "job-reestimate",
          kind: "reestimate",
          status: "running",
          device: "cpu",
          expected_s: 20,
          elapsed_s: 0,
          message: "Estimation des distances, environ 20 s",
          stage: "Estimation des distances…",
          result: null,
          error: null,
        },
      });
    },
  );
  await page.route(
    new RegExp(`/api/projects/${projectId}/jobs/job-reestimate$`),
    async (route) => {
      // La tâche finit : la photo a gardé sa profondeur, elle est mesurable.
      state.basis = cameraBasis(imageId, 1);
      state.stamp += 1000;
      await route.fulfill({
        json: {
          id: "job-reestimate",
          kind: "reestimate",
          status: "done",
          device: "cpu",
          expected_s: 20,
          elapsed_s: 18,
          message: "Photo prête pour la mesure",
          result: null,
          error: null,
        },
      });
    },
  );
}

export function newState(basis: Record<string, unknown> | null): MockState {
  return {
    basis,
    segments: [],
    puts: [],
    deletes: [],
    refusals: [],
    reestimates: 0,
    stamp: Date.now() + 3_600_000,
  };
}
