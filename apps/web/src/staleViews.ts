import type { RenderEntry } from "./api/client";
import type { HistoryEntry, Project } from "./api/types.gen";

/** Dernier rendu de chaque vue (pas les vignettes de variantes), par identifiant de caméra. */
export function latestRenders(
  renders: RenderEntry[],
): Map<string, RenderEntry> {
  const latest = new Map<string, RenderEntry>();
  for (const r of renders) {
    if (r.variant != null || !r.camera_id) continue;
    const known = latest.get(r.camera_id);
    if (!known || Date.parse(r.created_at) > Date.parse(known.created_at))
      latest.set(r.camera_id, r);
  }
  return latest;
}

/** Libellé du bouton qui refait les vues périmées (P2). */
export function rerenderLabel(count: number): string {
  return count === 1 ? "Re-rendre la vue" : `Re-rendre les ${count} vues`;
}

/** Champs du projet dont le changement ne change pas ce qu'on voit dans un rendu. */
const PROJECT_SETTINGS = new Set([
  "name",
  "name_auto",
  "render",
  "privacy",
  "units_display",
  "pending_instruction",
  "images",
]);
const ELEMENT = /^\/levels\/([^/]+)\/elements\/([^/]+)(\/|$)/;

interface StepScope {
  visible: boolean;
  cameras: Set<string>;
}

type PatchOp = {
  op?: unknown;
  path?: unknown;
  from?: unknown;
  value?: unknown;
};

function typesAt(ops: PatchOp[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const op of ops) {
    const value = op.value as { type?: unknown } | null | undefined;
    if (
      typeof op.path === "string" &&
      ELEMENT.test(op.path) &&
      value &&
      typeof value === "object" &&
      typeof value.type === "string"
    )
      out.set(op.path.replace(/\/$/, ""), value.type);
  }
  return out;
}

/** Ce qu'un pas d'historique touche (P2, même règle que `openplan.pipeline.stale`) : ce
 *  qu'on voit (géométrie, meubles, matières, ambiance), ou seulement des caméras ; les
 *  réglages du projet ne comptent pas. */
export function stepScope(entry: HistoryEntry, project: Project): StepScope {
  const patch = entry.patch as PatchOp[];
  const added = typesAt(patch);
  const removed = typesAt(entry.inverse_patch as PatchOp[]);
  const scope: StepScope = { visible: false, cameras: new Set() };
  for (const op of patch) {
    for (const path of [op.path, op.from]) {
      if (typeof path !== "string" || !path.startsWith("/")) continue;
      if (PROJECT_SETTINGS.has(path.split("/")[1] ?? "")) continue;
      const m = ELEMENT.exec(path);
      if (!m) {
        scope.visible = true;
        continue;
      }
      const [, levelId = "", elementId = ""] = m;
      const kind =
        project.levels[levelId]?.elements[elementId]?.type ??
        added.get(`/levels/${levelId}/elements/${elementId}`) ??
        removed.get(`/levels/${levelId}/elements/${elementId}`);
      if (kind === "camera") scope.cameras.add(elementId);
      else scope.visible = true;
    }
  }
  return scope;
}

/** Vues périmées : un pas appliqué plus récent que le dernier rendu de la vue touche ce
 *  qu'on voit, ou la caméra de cette vue. Une annulation ne rend pas une vue « à jour ». */
export function staleCameraIds(
  project: Project,
  renders: RenderEntry[],
): Set<string> {
  const stale = new Set<string>();
  const latest = latestRenders(renders);
  if (latest.size === 0) return stale;
  const existing = new Set(
    Object.values(project.levels).flatMap((l) =>
      Object.values(l.elements)
        .filter((e) => e.type === "camera")
        .map((e) => e.id),
    ),
  );
  const scoped = project.history
    .slice(0, project.history_cursor)
    .map((e) => ({
      at: Date.parse(e.timestamp ?? ""),
      scope: stepScope(e, project),
    }));
  for (const [cameraId, r] of latest) {
    if (!existing.has(cameraId)) continue;
    const renderedAt = Date.parse(r.created_at);
    if (
      scoped.some(
        ({ at, scope }) =>
          at > renderedAt && (scope.visible || scope.cameras.has(cameraId)),
      )
    )
      stale.add(cameraId);
  }
  return stale;
}
