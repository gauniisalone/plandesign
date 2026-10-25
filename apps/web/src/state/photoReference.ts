/** KAN-66 : la mesure de référence d'une photo (deux points, une longueur connue) et l'origine
 *  de l'échelle du plan estimé depuis cette photo (`Level.scale_basis`), lues dans le projet.
 *  Schéma 0.16.0 et suivants (types.gen.ts) ; p1/p2 y sortent en `[unknown, unknown]` : ils sont lus ici
 *  en `[number, number]` (pixels de l'image d'origine). */

import type { Level, Project, ScaleBasis } from "../api/types.gen";
import { formatMeters } from "./lengthInput.ts";

export type Point = [number, number];
export type PhotoScaleBasis = ScaleBasis;

export interface PhotoReference {
  id: string;
  p1: Point;
  p2: Point;
  length_m: number;
  label: string | null;
  author: string;
  created_at: string;
}

function asPoint(v: unknown): Point | null {
  return Array.isArray(v) &&
    v.length === 2 &&
    typeof v[0] === "number" &&
    typeof v[1] === "number"
    ? [v[0], v[1]]
    : null;
}

/** La mesure posée sur cette photo (une seule en V1), sinon null. */
export function referenceOf(
  project: Project,
  imageId: string,
): PhotoReference | null {
  const s = project.images[imageId]?.reference_segments?.[0];
  if (!s) return null;
  const p1 = asPoint(s.p1);
  const p2 = asPoint(s.p2);
  if (!p1 || !p2 || typeof s.length_m !== "number") return null;
  return {
    id: s.id,
    p1,
    p2,
    length_m: s.length_m,
    label: s.label || null,
    author: s.author,
    created_at: s.created_at,
  };
}

export function basisOf(level: Level): PhotoScaleBasis | null {
  const b = level.scale_basis;
  return b && typeof b.image_id === "string" ? b : null;
}

/** KAN-95 : contribution d'une photo aux combles recoupés (métadonnées de sa caméra). */
export interface FusionPhoto {
  imageId: string;
  kept: boolean;
  reason: string;
  weight: number;
  ridgeM: number | null;
  pitchDeg: number | null;
  factor: number | null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** KAN-95 : « N photos concordent à ±X % » et la contribution de chaque photo, lus sur les
 *  caméras des photos recoupées du niveau ; null quand le plan vient d'une seule photo. */
export function fusionOf(
  level: Level,
): { summary: string; photos: FusionPhoto[] } | null {
  let summary = "";
  const photos: FusionPhoto[] = [];
  for (const e of Object.values(level.elements)) {
    if (e.type !== "camera" || !("image_id" in e) || !e.image_id) continue;
    const meta = (e.metadata ?? {}) as Record<string, unknown>;
    const factor = num(meta.fusion_factor);
    const info = meta.fusion as Record<string, unknown> | undefined;
    if (factor == null && !info) continue;
    if (typeof meta.fusion_summary === "string") summary = meta.fusion_summary;
    photos.push({
      imageId: e.image_id,
      kept: Boolean(info?.kept),
      reason: typeof info?.reason === "string" ? info.reason : "",
      weight: num(info?.weight) ?? 0,
      ridgeM: num(info?.ridge_m),
      pitchDeg: num(info?.pitch_deg),
      factor,
    });
  }
  return photos.length ? { summary, photos } : null;
}

/** Survol de la phrase de concordance : une ligne par photo (sa part, ce qu'elle a lu seule,
 *  ou pourquoi elle est écartée). */
export function fusionTooltip(fusion: { photos: FusionPhoto[] }): string {
  return fusion.photos
    .map((p, i) => {
      const read = [
        p.ridgeM != null ? `faîtage ${formatMeters(p.ridgeM)}` : "",
        p.pitchDeg != null ? `pente ${Math.round(p.pitchDeg)}°` : "",
      ]
        .filter(Boolean)
        .join(", ");
      const share = p.kept ? ` · ${Math.round(p.weight * 100)} %` : "";
      return `Photo ${i + 1} : ${p.reason}${share}${read ? ` (${read})` : ""}`;
    })
    .join("\n");
}

/** Le plan estimé depuis cette photo : un étage avant une variante (comme le serveur).
 *  KAN-95 : une photo recoupée porte aussi l'échelle de son plan, avec son propre facteur. */
export function basisLevelOf(
  project: Project,
  imageId: string,
): { levelId: string; basis: PhotoScaleBasis } | null {
  const found = Object.entries(project.levels)
    .map(([levelId, level]) => {
      const basis = basisOf(level);
      const fused =
        basis && basis.image_id !== imageId
          ? fusionOf(level)?.photos.find((p) => p.imageId === imageId)
          : undefined;
      return {
        levelId,
        basis:
          basis && fused
            ? { ...basis, image_id: imageId, factor: fused.factor }
            : basis,
        variant: level.variant_of != null,
      };
    })
    .filter(
      (f): f is { levelId: string; basis: PhotoScaleBasis; variant: boolean } =>
        f.basis?.image_id === imageId,
    )
    .sort((a, b) => Number(a.variant) - Number(b.variant));
  return found[0] ?? null;
}

export type Measurability =
  | { kind: "ready"; levelId: string; basis: PhotoScaleBasis }
  /** Profondeur non gardée (photo d'avant KAN-66) : « Réestimer la photo pour pouvoir
   *  mesurer » sur ce plan. */
  | { kind: "reestimate"; levelId: string }
  /** La photo n'a construit aucun plan. */
  | { kind: "none" };

/** Une photo est mesurable si un plan a été estimé depuis elle avec un facteur d'échelle ;
 *  sinon, le plan qui la porte (une caméra de cette photo) peut la réestimer. */
export function measurability(
  project: Project,
  imageId: string,
): Measurability {
  const found = basisLevelOf(project, imageId);
  if (found && found.basis.factor != null)
    return { kind: "ready", levelId: found.levelId, basis: found.basis };
  if (found) return { kind: "reestimate", levelId: found.levelId };
  const levels = Object.entries(project.levels).sort(
    ([, a], [, b]) =>
      Number(a.variant_of != null) - Number(b.variant_of != null),
  );
  for (const [levelId, level] of levels) {
    const seen = Object.values(level.elements).some(
      (e) => e.type === "camera" && e.image_id === imageId,
    );
    if (seen) return { kind: "reestimate", levelId };
  }
  return { kind: "none" };
}

/** « 4,06 → 5,00 m » par dimension, depuis la boîte d'avant la dernière opération et son
 *  facteur (les hauteurs suivent l'échelle) ; null quand rien n'a changé. */
export function beforeAfter(basis: PhotoScaleBasis): string[] | null {
  const { before, factor } = basis;
  if (!before || factor == null || Math.abs(factor - 1) < 1e-3) return null;
  const line = (name: string, value: number) =>
    `${name} ${formatMeters(value).replace(" m", "")} → ${formatMeters(value * factor)}`;
  return [
    line("Longueur", before.length_m),
    line("largeur", before.width_m),
    line("hauteur", before.height_m),
  ];
}
