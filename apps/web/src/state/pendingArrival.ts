import type { AutopilotRun } from "../api/client";

/** Chemin automatique fini depuis l'accueil (L1a) : l'atelier du projet s'ouvre sur
 *  « Vos rendus » avec ce compte rendu. Lu une seule fois. */
let pending: { projectId: string; run: AutopilotRun } | null = null;

export function setPendingArrival(projectId: string, run: AutopilotRun): void {
  pending = { projectId, run };
}

export function takePendingArrival(projectId: string): AutopilotRun | null {
  if (!pending || pending.projectId !== projectId) return null;
  const { run } = pending;
  pending = null;
  return run;
}

/** Arrivée par /nouveau avec des exigences (retour d'augus, 19 oct.) : le plan se génère
 *  depuis elles dès l'ouverture de l'espace, sans geste. Lu une seule fois. */
let planFor: string | null = null;

export function setPendingPlan(projectId: string): void {
  planFor = projectId;
}

export function takePendingPlan(projectId: string): boolean {
  if (planFor !== projectId) return false;
  planFor = null;
  return true;
}

/** Ce que l'accueil a à dire en ouvrant l'atelier sans rendus (A2) : un plan ou des rendus
 *  en échec, un fichier ignoré. Lu une seule fois. */
let notices: { projectId: string; items: string[] } | null = null;

export function setPendingNotices(projectId: string, items: string[]): void {
  notices = items.length ? { projectId, items } : null;
}

export function takePendingNotices(projectId: string): string[] {
  if (!notices || notices.projectId !== projectId) return [];
  const { items } = notices;
  notices = null;
  return items;
}
