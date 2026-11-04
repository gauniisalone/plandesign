import { expect, type APIRequestContext, type Page } from "@playwright/test";

/** Une demande prise par le poste simulé (`seed` : le tirage d'une variante). */
export type Job = { request_id: string; lease: string; seed?: number };

/** Dépose une image simulée pour `job`. 120 s : la durée typique (médiane du journal, base
 *  commune) reste « ~2 min », qu'attend renders-gallery.spec.ts. */
export async function deliverFake(
  worker: APIRequestContext,
  job: Job,
  png: Buffer,
): Promise<number> {
  const done = await worker.post(`/api/worker/jobs/${job.request_id}/result`, {
    headers: { "X-Render-Lease": job.lease },
    multipart: {
      meta: JSON.stringify({
        seconds: 120,
        vram_peak_mib: 0,
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
        engine: "fake",
      }),
      image: { name: "render.png", mimeType: "image/png", buffer: png },
    },
  });
  return done.status();
}

/** KAN-103 : la file du poste est commune à toute la suite (e2e-online et parcours Gherkin) :
 *  une demande laissée par un autre fichier peut passer devant. Le poste simulé prend les
 *  demandes jusqu'à l'une de celles en attente du projet, dépose les autres (image `png`), et
 *  attend son tour quand le poste répond 429 (débit) ou 204 (rien à prendre pour l'instant). */
export async function claimOwn(
  worker: APIRequestContext,
  page: Page,
  projectId: string,
  png: Buffer,
  timeoutMs = 90_000,
): Promise<Job> {
  const own = async () =>
    new Set(
      (
        (await (
          await page.request.get(`/api/projects/${projectId}/realistic`)
        ).json()) as { requests: { id: string; state: string }[] }
      ).requests
        .filter((r) => r.state === "pending")
        .map((r) => r.id),
    );
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ids = await own();
    const claim = await worker.post("/api/worker/claim", {
      data: { state: "idle" },
    });
    if (claim.status() === 429 || claim.status() === 204) {
      const wait = Math.min(
        Number(claim.headers()["retry-after"] ?? "5") || 5,
        15,
      );
      await new Promise((r) => setTimeout(r, wait * 1000 + 500));
      continue;
    }
    expect(claim.status()).toBe(200);
    const job = (await claim.json()) as Job;
    if (ids.has(job.request_id)) return job;
    expect(
      await deliverFake(worker, job, png),
      "demande laissée par un autre fichier",
    ).toBe(200);
  }
  throw new Error(`aucune demande du projet ${projectId} prise en ${timeoutMs} ms`);
}
