/** KAN-91 (D2) : un rendu sans plan relié est impossible (bouton inactif, « Reliez un plan pour
 *  rendre »).
 *
 *  INTÉGRATION avec le lot L1 (liens de l'espace de travail, Features) : la notion de lien
 *  plan → rendus est codée en parallèle. Ce contrôle passe par cette seule fonction ; L1 la
 *  remplace par « la carte Rendus `frameId` a exactement un lien `renders_view` actif ». Le
 *  repli d'ici est l'état d'avant L1 : le plan du projet existe (le niveau du cadre a au moins
 *  un mur). Le serveur applique le même repli (`linked_plan` de render_routes.py, 409). */

import type { Project } from "../api/types.gen";

export const NO_PLAN_TEXT = "Reliez un plan pour rendre";

export type LinkedPlanCheck = (
  project: Project,
  levelId: string,
  frameId?: string,
) => boolean;

export const hasLinkedPlan: LinkedPlanCheck = (project, levelId) => {
  const level = project.levels[levelId];
  return (
    level !== undefined &&
    Object.values(level.elements).some((e) => e.type === "wall")
  );
};
