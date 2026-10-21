import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { expectSpace, waitNoHeavyJob } from "./helpers";
import { gotoApp } from "./app-ready";

/** KAN-66, contrôle réel de bout en bout (sans simulation, poids de profondeur chargés) :
 *  une photo estimée, la mesure posée par « Mesurer sur la photo », le plan recalé, puis la
 *  mesure retirée et l'échelle d'avant rendue. Photo : la fixture « pièce rectangulaire »
 *  (4,00 × 3,00 m, caméra connue) ; le segment est un trait de 2,00 m sur le mur y = 3, à
 *  0,50 m du sol, sous la fenêtre, projeté avec la caméra de la fixture. Les cotes avant et
 *  après sont écrites dans e2e/screenshots/kan66_reel.json avec les captures. */

test.afterEach(async ({ page }) => {
  await waitNoHeavyJob(page);
});

const FIXTURE = path.resolve("../../tests/fixtures/rect_room");
const SHOTS = path.resolve("e2e/screenshots");
const TRUTH = { length_m: 4.0, width_m: 3.0, segment_m: 2.0 };

type Basis = {
  source: string;
  text: string;
  factor: number | null;
  segment_id: string | null;
  confidence: { label: string; reason: string } | null;
};
type Level = {
  scale_basis: Basis | null;
  vertices: Record<string, { position: { x: number; y: number } }>;
};
type Project = {
  levels: Record<string, Level>;
  images: Record<string, { width_px: number; height_px: number }>;
};

/** Pixel d'un point du monde vu par la caméra de la fixture (RenderCamera : lacet depuis +x,
 *  tangage négatif vers le bas, point principal au centre). */
function pixel(p: [number, number, number]): [number, number] {
  const cam = JSON.parse(
    readFileSync(path.join(FIXTURE, "camera.json"), "utf-8"),
  ) as {
    position: { x: number; y: number; z: number };
    yaw: number;
    pitch: number;
    focal_px: number;
    width_px: number;
    height_px: number;
  };
  const { yaw, pitch, focal_px: f } = cam;
  const fw = [
    Math.cos(pitch) * Math.cos(yaw),
    Math.cos(pitch) * Math.sin(yaw),
    Math.sin(pitch),
  ];
  const rt = [Math.sin(yaw), -Math.cos(yaw), 0];
  const up = [
    -Math.sin(pitch) * Math.cos(yaw),
    -Math.sin(pitch) * Math.sin(yaw),
    Math.cos(pitch),
  ];
  const d = [
    p[0] - cam.position.x,
    p[1] - cam.position.y,
    p[2] - cam.position.z,
  ];
  const dot = (a: number[]) => a.reduce((s, v, i) => s + v * (d[i] ?? 0), 0);
  const z = dot(fw);
  return [
    cam.width_px / 2 + (f * dot(rt)) / z,
    cam.height_px / 2 - (f * dot(up)) / z,
  ];
}

function extent(level: Level): { x: number; y: number } {
  const pts = Object.values(level.vertices).map((v) => v.position);
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  return {
    x: Math.max(...xs) - Math.min(...xs),
    y: Math.max(...ys) - Math.min(...ys),
  };
}

async function project(
  request: APIRequestContext,
  id: string,
): Promise<Project> {
  return (await (await request.get(`/api/projects/${id}`)).json()) as Project;
}

async function estimated(
  page: Page,
): Promise<{ id: string; levelId: string; imageId: string }> {
  const made = await page.request.post("/api/projects", {
    data: { name: "Mesure réelle KAN-66" },
  });
  const { id } = (await made.json()) as { id: string };
  const up = await page.request.post(`/api/projects/${id}/images`, {
    multipart: {
      file: {
        name: "piece.png",
        mimeType: "image/png",
        buffer: readFileSync(path.join(FIXTURE, "image.png")),
      },
    },
  });
  expect(up.ok(), await up.text()).toBe(true);
  const first = await project(page.request, id);
  const levelId = Object.keys(first.levels)[0] ?? "";
  const imageId = Object.keys(first.images)[0] ?? "";
  const started = await page.request.post(
    `/api/projects/${id}/levels/${levelId}/images/${imageId}/autoplan?kind=photo`,
  );
  expect(started.status(), await started.text()).toBe(202);
  const job = (await started.json()) as { id: string };
  let last = "";
  await expect
    .poll(
      async () => {
        const r = await page.request.get(`/api/projects/${id}/jobs/${job.id}`);
        const s = (await r.json()) as { status: string; error?: string | null };
        last = s.error ?? "";
        return s.status;
      },
      { timeout: 300_000, intervals: [1000, 2000, 5000] },
    )
    .toMatch(/^(done|failed|cancelled)$/);
  expect(last, "estimation de la photo en échec").toBe("");
  return { id, levelId, imageId };
}

test("KAN-66 réel : photo estimée, mesure posée, plan recalé, mesure retirée", async ({
  page,
}) => {
  test.setTimeout(420_000);
  mkdirSync(SHOTS, { recursive: true });
  const { id, levelId } = await estimated(page);
  const before = (await project(page.request, id)).levels[levelId] as Level;
  const camera = before.scale_basis;
  expect(camera, "l'estimation note l'origine de son échelle").not.toBeNull();
  expect(
    camera?.factor,
    "profondeur gardée (poids de profondeur chargés ?)",
  ).not.toBeNull();
  const size0 = extent(before);

  await gotoApp(page, `/#/projects/${id}/board`);
  await expectSpace(page, "board");
  const board = page.getByRole("region", { name: "Espace de travail" });
  await board.locator(".board-item.kind-photo").first().click();
  await page
    .getByRole("button", { name: "Mesurer sur la photo", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "Mesurer sur la photo" });
  await expect(dialog).toContainText("Point 1 sur 2");
  const box = await dialog.locator(".pm-photo-box img").boundingBox();
  if (!box) throw new Error("photo absente du calque");
  const [a, b] = [pixel([1.5, 3.0, 0.5]), pixel([3.5, 3.0, 0.5])];
  const at = ([u, v]: [number, number]) =>
    [box.x + (box.width * u) / 1280, box.y + (box.height * v) / 960] as const;
  await page.mouse.click(...at(a));
  await expect(dialog).toContainText("Point 2 sur 2");
  await page.mouse.click(...at(b));
  const form = page.getByRole("dialog", { name: "Longueur du segment" });
  await form.getByLabel("Longueur", { exact: true }).fill("2,00");
  await form.getByLabel("Libellé (facultatif)").fill("mur");
  await form.getByRole("button", { name: "Recaler l'estimation" }).click();
  // Un refus du serveur (deux surfaces, facteur hors de 0,3 à 3) s'afficherait ici.
  await expect(form.getByRole("alert")).toHaveCount(0);
  await expect(form).toHaveCount(0);
  await expect(dialog.getByLabel("Échelle de l'estimation")).toContainText(
    "Échelle calée sur votre mesure de 2,00 m (mur)",
  );
  await page.screenshot({ path: path.join(SHOTS, "kan66_reel_posee.png") });

  const measured = (await project(page.request, id)).levels[levelId] as Level;
  const basis = measured.scale_basis;
  expect(basis?.source).toBe("reference");
  expect(basis?.confidence?.reason ?? "").toContain(
    "échelle calée sur votre mesure",
  );
  const size1 = extent(measured);
  const relative = (basis?.factor ?? 0) / (camera?.factor ?? 1);
  // Le plan est recalé du facteur de la mesure, plan et hauteurs (KAN-66, lot 1).
  expect(size1.x / size0.x).toBeCloseTo(relative, 4);

  const list = dialog.getByRole("list", { name: "Mesures de référence" });
  await list.getByRole("button", { name: "Supprimer", exact: true }).click();
  await expect(dialog.getByRole("status")).toContainText("Mesure retirée");
  await page.screenshot({ path: path.join(SHOTS, "kan66_reel_retiree.png") });
  const back = (await project(page.request, id)).levels[levelId] as Level;
  expect(back.scale_basis?.source).toBe(camera?.source);
  const size2 = extent(back);
  expect(size2.x).toBeCloseTo(size0.x, 4);
  expect(size2.y).toBeCloseTo(size0.y, 4);

  const fr = (v: number) => v.toFixed(2).replace(".", ",");
  writeFileSync(
    path.join(SHOTS, "kan66_reel.json"),
    JSON.stringify(
      {
        verite: TRUTH,
        segment_px: [a, b],
        avant: { texte: camera?.text, emprise_m: [fr(size0.x), fr(size0.y)] },
        apres: {
          texte: basis?.text,
          confiance: basis?.confidence,
          emprise_m: [fr(size1.x), fr(size1.y)],
          facteur: relative,
        },
        retiree: {
          texte: back.scale_basis?.text,
          emprise_m: [fr(size2.x), fr(size2.y)],
        },
      },
      null,
      2,
    ),
  );
});
