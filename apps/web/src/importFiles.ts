import type { JobStatus } from "./api/client";

/** Fichiers acceptés à l'import (accueil, écran Importer, dépôt sur le canevas). */
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp"];
export const PLAN_EXTENSIONS = [".dxf", ".pdf", ".svg"];
export const IMPORT_ACCEPT = `${PHOTO_TYPES.join(",")},${PLAN_EXTENSIONS.join(",")}`;

/** Un plan 2D vectoriel : DXF, PDF vectoriel ou SVG (une image de plan passe par le fond). */
export function isPlanFile(file: File): boolean {
  return /\.(dxf|pdf|svg)$/i.test(file.name);
}

export const PLAN_KIND_LABEL: Record<string, string> = {
  dxf: "DXF",
  pdf: "PDF vectoriel",
  svg: "SVG",
  image: "image de plan (traits épais)",
};

export const PAPER_SCALES = [20, 25, 50, 100, 200, 500];

/** Phrase de progression d'une tâche de fond : étape en cours, temps écoulé, durée annoncée. */
export function jobSentence(job: JobStatus): string {
  const stage = job.stage || "Préparation…";
  const expected = job.expected_s
    ? ` · environ ${Math.round(job.expected_s)} s`
    : "";
  return `${stage} ${Math.round(job.elapsed_s)} s${expected}`;
}
