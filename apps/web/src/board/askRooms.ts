import { ApiError, realisticApi } from "../api/client";
import { refreshRenderStatus } from "../state/renderStatus";
import { cleanSettings, RENDERS_EVENT, type RoomsSent } from "./renderSettings";

/** KAN-134 : « Rendre » des vues de pièce, `draws` tirages chacune. Chaque tirage porte son
 *  rang (`draw`) : le serveur garde au plus draw+1 demandes en attente par vue, donc un double
 *  clic reste fusionné et « 2 rendus par pièce » en donne bien 2, chacune prise sur le quota du
 *  jour. Le poste donne une graine de variante à chaque tirage qui suit un rendu fait de la même
 *  vue (render_queue.py). Partagé par le node Rendus et l'Assistant ; le node relit sa galerie
 *  à l'événement `RENDERS_EVENT`. Le quota atteint en route arrête l'envoi et le dit (ce qui
 *  est parti reste) ; toute autre erreur, ou le quota dès la 1re demande, remonte. */
export async function askRooms(
  projectId: string,
  levelId: string,
  cameraIds: string[],
  draws: number,
): Promise<RoomsSent> {
  const n = cleanSettings({ draws }).draws;
  const ids = new Set<string>();
  const asked = cameraIds.length * n;
  try {
    for (const id of cameraIds)
      for (let k = 0; k < n; k += 1) {
        try {
          const done = await realisticApi.ask(projectId, levelId, id, k);
          ids.add(done.id);
        } catch (e) {
          if (ids.size > 0 && e instanceof ApiError && e.reason === "daily_quota")
            return { sent: ids.size, asked, full: true };
          throw e;
        }
      }
  } finally {
    if (ids.size > 0) {
      void refreshRenderStatus();
      window.dispatchEvent(new Event(RENDERS_EVENT));
    }
  }
  return { sent: ids.size, asked, full: false };
}
