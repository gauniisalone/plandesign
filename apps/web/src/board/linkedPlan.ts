/** KAN-91 (D2) : un rendu sans plan relié est impossible (bouton inactif, « Reliez un plan pour
 *  rendre »).
 *
 *  INTÉGRATION avec le lot L1 (liens de l'espace de travail, Features, d680e6b) : la réponse du
 *  serveur prime. RealisticRenders lit GET …/board/inputs/{frameId} (`can_render`, route de L1,
 *  `realisticApi.planLinked`) et un 409 « Reliez un plan pour rendre » grise le bouton. Tant
 *  que la route ne répond pas, ce repli s'applique : le plan du projet existe (le niveau du
 *  cadre a au moins un mur). Le serveur, lui, vérifie le lien (`render_inputs`, 409). */

import { ApiError } from "../api/client";
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

/** Le refus du serveur (409 « Reliez un plan pour rendre »), quel que soit le lot qui le pose. */
export function isNoPlan(error: unknown): boolean {
  return (
    error instanceof ApiError &&
    error.status === 409 &&
    error.message.includes(NO_PLAN_TEXT)
  );
}
