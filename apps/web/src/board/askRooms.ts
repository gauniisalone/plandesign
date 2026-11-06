import { realisticApi } from "../api/client";
import { refreshRenderStatus } from "../state/renderStatus";
import { cleanSettings, RENDERS_EVENT } from "./renderSettings";

/** KAN-134 : « Rendre » des vues de pièce, `draws` tirages chacune (une demande par tirage,
 *  chacune prise sur le quota du jour). Le serveur donne une graine de variante à chaque
 *  tirage qui suit un rendu fait de la même vue (render_queue.py). Partagé par le node
 *  Rendus et l'Assistant ; le node relit sa galerie à l'événement `RENDERS_EVENT`. La
 *  première erreur arrête l'envoi et remonte (quota atteint, plan non relié…). */
export async function askRooms(
  projectId: string,
  levelId: string,
  cameraIds: string[],
  draws: number,
): Promise<number> {
  const n = cleanSettings({ draws }).draws;
  let sent = 0;
  try {
    for (const id of cameraIds)
      for (let k = 0; k < n; k += 1) {
        await realisticApi.ask(projectId, levelId, id);
        sent += 1;
      }
  } finally {
    if (sent > 0) {
      void refreshRenderStatus();
      window.dispatchEvent(new Event(RENDERS_EVENT));
    }
  }
  return sent;
}
