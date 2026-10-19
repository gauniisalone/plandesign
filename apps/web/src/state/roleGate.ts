import type { Role } from "../api/client";

/** Rôle qui permet d'éditer le projet (B1d). `null` (rôle pas encore lu) : non. */
export function canEdit(role: Role | null): boolean {
  return role === "editor" || role === "owner";
}

/** Écran d'un lien direct vers « Ajouter une source » (`#/projects/{id}/import`).
 *  KAN-47 : avec les comptes, le rôle arrive après l'atelier ; tant qu'il n'est pas lu,
 *  l'atelier attend (rien d'éditable, pas de saut vers l'espace de travail), puis montre
 *  l'import à qui peut éditer et l'espace de travail, en lecture seule, aux autres. */
export type ImportGate = "waiting" | "import" | "board";

export function importGate(role: Role | null): ImportGate {
  if (role === null) return "waiting";
  return canEdit(role) ? "import" : "board";
}
