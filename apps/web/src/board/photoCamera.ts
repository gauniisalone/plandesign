/** KAN-130 : la caméra posée d'une photo, pour montrer son cône sur le node Plan de son niveau
 *  quand la photo est survolée dans l'espace de travail. */

import type { Level } from "../api/types.gen";

/** Le niveau et la caméra de la photo `imageId` ; null tant qu'elle n'a servi à aucun plan. */
export function photoCamera(
  levels: Record<string, Level>,
  imageId: string,
): { levelId: string; cameraId: string } | null {
  for (const [levelId, level] of Object.entries(levels))
    for (const e of Object.values(level.elements))
      if (e.type === "camera" && "image_id" in e && e.image_id === imageId)
        return { levelId, cameraId: e.id };
  return null;
}
