import { useCallback, useEffect, useState } from "react";
import {
  realisticApi,
  type RealisticRequest,
  type RealisticStatus,
} from "../api/client";
import type { Project } from "../api/types.gen";
import { allCameras } from "../model";

/** KAN-89 : rendus réalistes calculés par le poste d'augus, dans le cadre Rendus (en ligne,
 *  sans Blender sur le serveur). Un compte non autorisé voit la même ligne qu'avant
 *  (« Rendus réalistes : bientôt en ligne ») ; le compte autorisé voit les vues du plan, un
 *  bouton « Rendre » par vue, l'état de chaque demande et l'image quand elle est faite. */

const NOT_YET ="Rendus réalistes : bientôt en ligne";
const OFFLINE = "Poste de rendu hors ligne : le rendu sera fait à son retour";
const POLL_BUSY_MS = 5_000;
const POLL_IDLE_MS = 30_000;

/** Lu une fois par session de page : le droit ne change pas en cours de route. */
let allowedOnce: Promise<RealisticStatus | null> | null = null;
function statusOnce(): Promise<RealisticStatus | null> {
  allowedOnce ??= realisticApi.status().catch(() => {
    allowedOnce = null; // relue au prochain affichage
    return null;
  });
  return allowedOnce;
}

function stateText(
  request: RealisticRequest,
  worker: RealisticStatus["worker"],
): string {
  switch (request.state) {
    case "pending":
      return worker === "offline" ? OFFLINE : "En attente";
    case "taken":
      return "En cours";
    case "expired":
      return "Expiré : non calculé à temps, relancez « Rendre »";
    case "failed":
      return request.error ?? "Le rendu a échoué";
    case "done":
      return request.seconds !== null
        ? `Fait en ${Math.round(request.seconds)} s`
        : "Fait";
  }
}

export function RealisticRenders({
  project,
  levelId,
  readOnly,
}: {
  project: Project;
  levelId: string;
  readOnly: boolean;
}) {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [status, setStatus] = useState<RealisticStatus | null>(null);
  const [requests, setRequests] = useState<RealisticRequest[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const level = project.levels[levelId];
  const cameras = level ? allCameras(level) : [];

  useEffect(() => {
    let alive = true;
    void statusOnce().then((s) => {
      if (alive) setAllowed(Boolean(s?.allowed));
    });
    return () => {
      alive = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const listed = await realisticApi.list(project.id);
      setStatus(listed.status);
      setRequests(listed.requests.filter((r) => r.level_id === levelId));
    } catch {
      /* serveur injoignable : on garde la dernière lecture */
    }
  }, [project.id, levelId]);

  const busy = requests.some(
    (r) => r.state === "pending" || r.state === "taken",
  );
  useEffect(() => {
    if (!allowed) return;
    const first = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(
      () => void refresh(),
      busy ? POLL_BUSY_MS : POLL_IDLE_MS,
    );
    return () => {
      window.clearTimeout(first);
      window.clearInterval(timer);
    };
  }, [allowed, busy, refresh]);

  if (!allowed) {
    return (
      <span className="board-frame-hint board-frame-missing" role="note">
        {NOT_YET}
      </span>
    );
  }

  const ask = async (cameraId: string) => {
    setMessage(null);
    try {
      await realisticApi.ask(project.id, levelId, cameraId);
      await refresh();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    }
  };

  /** La demande la plus récente de chaque vue. */
  const latest = new Map<string, RealisticRequest>();
  for (const r of requests) if (!latest.has(r.camera_id)) latest.set(r.camera_id, r);
  const worker = status?.worker ?? "offline";

  return (
    <div
      className="realistic-renders"
      role="region"
      aria-label="Rendus réalistes"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {cameras.length === 0 && (
        <p className="realistic-empty">
          Aucune vue : posez un point de vue sur le plan.
        </p>
      )}
      {cameras.map((camera) => {
        const r = latest.get(camera.id);
        const running = r?.state === "pending" || r?.state === "taken";
        return (
          <div className="realistic-row" key={camera.id}>
            <div className="realistic-head">
              <span className="realistic-name">{camera.name || "Vue"}</span>
              {!readOnly && (
                <button
                  type="button"
                  className="primary"
                  disabled={running}
                  onClick={() => void ask(camera.id)}
                  title="Calculé par le poste de rendu ; l'image arrive ici quand elle est faite"
                >
                  Rendre
                </button>
              )}
            </div>
            {r && (
              <span
                className={`realistic-state state-${r.state}`}
                role="status"
              >
                {stateText(r, worker)}
              </span>
            )}
            {r?.state === "done" && r.image && (
              <img
                className="realistic-image"
                src={realisticApi.imageUrl(project.id, r.id)}
                alt={`Rendu réaliste : ${camera.name || "vue"}`}
                width={r.width ?? undefined}
                height={r.height ?? undefined}
                draggable={false}
              />
            )}
          </div>
        );
      })}
      {message && (
        <p className="realistic-message" role="alert">
          {message}
        </p>
      )}
    </div>
  );
}
