import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  realisticApi,
  type ReactionKey,
  type RealisticListing,
  type RealisticRequest,
  type RealisticStatus,
} from "../api/client";
import type { Project } from "../api/types.gen";
import { allCameras, isFreeCamera } from "../model";
import { hasLinkedPlan, isNoPlan, NO_PLAN_TEXT } from "./linkedPlan";
import { RenderViewer } from "./RenderViewer";
import { useRetryImage } from "./retryImage";
import {
  etaText,
  gallery,
  isRunning,
  photoNumber,
  progress,
} from "./renderGallery";

/** KAN-89 : rendus réalistes calculés par le poste d'augus, dans le cadre Rendus (en ligne,
 *  sans Blender sur le serveur). Un compte non autorisé voit la même ligne qu'avant
 *  (« Rendus réalistes : bientôt en ligne »).
 *
 *  KAN-91 (lot L6, maquette validée par augus le 22 oct.) : le compte autorisé voit une
 *  galerie de taille fixe (le cadre ne grandit jamais tout seul) : au plus quatre vignettes
 *  4:3, la plus récente d'abord, « +N plus anciens · Tout voir » au-delà ; un rendu en cours
 *  prend la première case avec la durée typique (médiane du journal du poste, sinon ~2 min).
 *  Le menu « Rendre ▾ » rend une vue du plan, toutes les vues, le point de vue exact d'une
 *  photo (D5) ou une nouvelle variante ; il est inactif sans plan relié (D2, `hasLinkedPlan`).
 *  Un clic sur une vignette ouvre la visionneuse plein écran (RenderViewer). */

const NOT_YET = "Rendus réalistes : bientôt en ligne";
const OFFLINE = "Poste de rendu hors ligne : le rendu sera fait à son retour";
const POLL_BUSY_MS = 5_000;
const POLL_IDLE_MS = 30_000;
const TICK_MS = 1_000;

/** Lu une fois par session de page : le droit ne change pas en cours de route. */
let allowedOnce: Promise<RealisticStatus | null> | null = null;
/** Une case de la galerie (connexion lente, 25 oct.) : l'image se recharge seule quand elle
 *  échoue, puis laisse une case grise « Image non chargée · Réessayer » au lieu du texte
 *  de remplacement brut ; un clic sur la case réessaie alors au lieu d'ouvrir le rendu. */
function RetryTile({
  src,
  alt,
  label,
  onOpen,
  children,
}: {
  src: string;
  alt: string;
  label: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  const image = useRetryImage(src);
  return (
    <button
      type="button"
      className="rgal-open"
      onClick={image.failed ? image.retry : onOpen}
      aria-label={
        image.failed ? `Image non chargée : réessayer (${alt})` : label
      }
    >
      {image.failed ? (
        <span className="rgal-img img-failed">
          Image non chargée ·{" "}
          <span className="img-failed-retry">Réessayer</span>
        </span>
      ) : (
        <img
          className="rgal-img"
          src={image.src}
          alt={alt}
          draggable={false}
          decoding="async"
          onError={image.onError}
          onLoad={image.onLoad}
        />
      )}
      {children}
    </button>
  );
}

function statusOnce(): Promise<RealisticStatus | null> {
  allowedOnce ??= realisticApi.status().catch(() => {
    allowedOnce = null; // relue au prochain affichage
    return null;
  });
  return allowedOnce;
}

function problemText(request: RealisticRequest): string {
  return request.state === "expired"
    ? "Expiré : non calculé à temps, relancez « Rendre »"
    : (request.error ?? "Le rendu a échoué");
}

function hour(iso: string): string {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
}

/** KAN-91 (D5) : « Rendre depuis cette photo », dans la photo en grand. Visible pour le compte
 *  autorisé seulement, sur une photo qui a une caméra estimée ; inactif sans plan relié. */
export function RenderFromPhotoButton({
  project,
  imageId,
  readOnly,
}: {
  project: Project;
  imageId: string;
  readOnly: boolean;
}) {
  const [allowed, setAllowed] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    void statusOnce().then((s) => {
      if (alive) setAllowed(Boolean(s?.allowed));
    });
    return () => {
      alive = false;
    };
  }, []);
  const levelId = Object.entries(project.levels).find(([, level]) =>
    allCameras(level).some((c) => c.image_id === imageId),
  )?.[0];
  if (!allowed || readOnly || !levelId) return null;
  const linked = hasLinkedPlan(project, levelId) && said !== NO_PLAN_TEXT;
  const ask = async () => {
    try {
      await realisticApi.fromPhoto(project.id, levelId, imageId);
      setSaid("Demandé : le rendu arrivera dans la galerie du cadre Rendus.");
    } catch (e) {
      // 409 du serveur (D2, lot L1) : bouton grisé, « Reliez un plan pour rendre ».
      setSaid(
        isNoPlan(e) ? NO_PLAN_TEXT : e instanceof Error ? e.message : String(e),
      );
    }
  };
  return (
    <span className="rgal-from-photo">
      <button
        type="button"
        disabled={!linked}
        title={linked ? "Le point de vue exact de cette photo" : NO_PLAN_TEXT}
        onClick={() => void ask()}
      >
        Rendre depuis cette photo
      </button>
      {said && <span role="status">{said}</span>}
    </span>
  );
}

const EMPTY: RealisticListing = {
  status: { allowed: true, worker: "offline", quota: 0, used_today: 0 },
  requests: [],
};

export function RealisticRenders({
  project,
  levelId,
  readOnly,
  frameId,
  title = "Rendus",
}: {
  project: Project;
  levelId: string;
  readOnly: boolean;
  /** KAN-91 : le cadre Rendus (pour le contrôle « plan relié » du lot L1). */
  frameId?: string;
  title?: string;
}) {
  const [allowed, setAllowed] = useState<boolean | null>(null);
  const [listing, setListing] = useState<RealisticListing>(EMPTY);
  const [message, setMessage] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const menuRef = useRef<HTMLDivElement>(null);
  const level = project.levels[levelId];
  const cameras = level ? allCameras(level) : [];
  const views = cameras.filter(isFreeCamera);
  const photos = Object.keys(project.images).filter((id) =>
    cameras.some((c) => c.image_id === id),
  );
  /** D2 : la réponse du serveur prime (GET …/board/inputs/{frameId} du lot L1, ou un 409
   *  « Reliez un plan pour rendre ») ; null : pas de réponse, repli `hasLinkedPlan`. */
  const [serverLinked, setServerLinked] = useState<boolean | null>(null);
  const linked = serverLinked ?? hasLinkedPlan(project, levelId, frameId);

  useEffect(() => {
    let alive = true;
    void statusOnce().then((s) => {
      if (alive) setAllowed(Boolean(s?.allowed));
    });
    return () => {
      alive = false;
    };
  }, []);

  // Relu à chaque modification du projet (un lien posé ou coupé change la réponse).
  const version = project.updated_at;
  useEffect(() => {
    if (!allowed || !frameId) return;
    let alive = true;
    void realisticApi.planLinked(project.id, frameId).then((can) => {
      if (alive) setServerLinked(can);
    });
    return () => {
      alive = false;
    };
  }, [allowed, project.id, frameId, version]);

  const refresh = useCallback(async () => {
    try {
      const listed = await realisticApi.list(project.id);
      setListing({
        ...listed,
        requests: listed.requests.filter((r) => r.level_id === levelId),
      });
    } catch {
      /* serveur injoignable : on garde la dernière lecture */
    }
  }, [project.id, levelId]);

  const requests = listing.requests;
  const busy = requests.some(isRunning);
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

  // La barre d'avancement avance entre deux lectures.
  useEffect(() => {
    if (!busy) return;
    const timer = window.setInterval(() => setNow(Date.now()), TICK_MS);
    return () => window.clearInterval(timer);
  }, [busy]);

  // Un clic ailleurs ou Échap ferme le menu.
  useEffect(() => {
    if (!menu) return;
    const away = (e: PointerEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) setMenu(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenu(false);
    };
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", key);
    };
  }, [menu]);

  if (!allowed) {
    return (
      <span className="board-frame-hint board-frame-missing" role="note">
        {NOT_YET}
      </span>
    );
  }

  /** Dans la visionneuse : un 409 « Reliez un plan » grise aussi les boutons, puis l'erreur
   *  y est dite. */
  const noPlan = (e: unknown): never => {
    if (isNoPlan(e)) setServerLinked(false);
    throw e;
  };
  const run = async (job: () => Promise<unknown>) => {
    setMessage(null);
    setMenu(false);
    try {
      await job();
    } catch (e) {
      // 409 « Reliez un plan pour rendre » : le bouton se grise avec la phrase (D2).
      if (isNoPlan(e)) setServerLinked(false);
      else setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      await refresh();
    }
  };
  const askView = (cameraId: string) =>
    run(() => realisticApi.ask(project.id, levelId, cameraId));
  const askPhoto = (imageId: string) =>
    run(() => realisticApi.fromPhoto(project.id, levelId, imageId));
  const askVariant = (requestId: string) =>
    run(() => realisticApi.variant(project.id, requestId));
  const askAll = () =>
    run(async () => {
      for (const v of views) await realisticApi.ask(project.id, levelId, v.id);
    });

  const g = gallery(requests);
  const worker = listing.status.worker;
  const eta = etaText(listing.typical_seconds);
  const latest = g.done[0];
  const canRender =
    !readOnly && linked && (views.length > 0 || photos.length > 0);
  const viewName = (r: RealisticRequest) => {
    if (r.photo_id) {
      const n = photoNumber(project, r.photo_id);
      return n ? `photo ${n}` : "photo";
    }
    return level?.elements[r.camera_id]?.name || "Vue";
  };
  const reactionsOf = (id: string) =>
    (listing.reactions ?? []).filter((p) => p.item_id === id);
  const viewingIndex = viewing ? g.done.findIndex((r) => r.id === viewing) : -1;

  return (
    <div
      className={`realistic-renders rgal${linked ? "" : " rgal-unlinked"}`}
      role="region"
      aria-label="Rendus réalistes"
      onPointerDown={(e) => e.stopPropagation()}
    >
      {!readOnly && (
        <div className="rgal-bar" ref={menuRef}>
          <button
            type="button"
            className="primary rgal-render"
            aria-haspopup="menu"
            aria-expanded={menu}
            disabled={!canRender}
            title={
              linked
                ? "Calculé par le poste de rendu ; l'image arrive ici quand elle est faite"
                : NO_PLAN_TEXT
            }
            onClick={() => setMenu((m) => !m)}
          >
            Rendre ▾
          </button>
          {menu && (
            <div className="rgal-menu" role="menu" aria-label="Rendre">
              {views.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  role="menuitem"
                  onClick={() => void askView(v.id)}
                >
                  <span>Rendre la vue « {v.name || "Vue"} »</span>
                  <small>{eta} · arrive en tête de la galerie</small>
                </button>
              ))}
              {views.length > 1 && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => void askAll()}
                >
                  <span>Rendre les {views.length} vues du plan</span>
                  <small>une demande par vue</small>
                </button>
              )}
              {photos.map((id) => (
                <button
                  key={id}
                  type="button"
                  role="menuitem"
                  onClick={() => void askPhoto(id)}
                >
                  <span>Depuis la photo {photoNumber(project, id)}</span>
                  <small>
                    point de vue exact de la photo : avant/après fidèle
                  </small>
                </button>
              ))}
              {latest && (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => void askVariant(latest.id)}
                >
                  <span>Nouvelle variante du dernier rendu</span>
                  <small>même vue, autre tirage</small>
                </button>
              )}
            </div>
          )}
        </div>
      )}
      {!linked && (
        <p className="rgal-hint" role="note">
          {NO_PLAN_TEXT}
        </p>
      )}
      {linked && cameras.length === 0 && (
        <p className="realistic-empty">
          Aucune vue : posez un point de vue sur le plan.
        </p>
      )}
      {g.tiles.length > 0 && (
        <ul className="rgal-grid" aria-label="Galerie des rendus">
          {g.tiles.map((tile) => {
            const r = tile.request;
            if (tile.kind === "running") {
              const share = progress(r, listing.typical_seconds, now);
              const text =
                r.state === "taken"
                  ? "Rendu en cours"
                  : worker === "offline"
                    ? OFFLINE
                    : "En attente";
              return (
                <li key={r.id} className="rgal-cell rgal-running">
                  <span role="status" aria-live="polite" className="rgal-state">
                    <strong>{text}</strong>
                    <span>
                      {viewName(r)} · {eta}
                    </span>
                  </span>
                  {share !== null && (
                    <span
                      className="rgal-bar-track"
                      role="progressbar"
                      aria-label="Avancement estimé"
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-valuenow={Math.round(share * 100)}
                      ref={(el) =>
                        el?.style.setProperty("--rgal-share", String(share))
                      }
                    />
                  )}
                </li>
              );
            }
            const pills = reactionsOf(r.id);
            const total = pills.reduce((n, p) => n + p.count, 0);
            return (
              <li key={r.id} className="rgal-cell">
                <RetryTile
                  src={realisticApi.imageUrl(project.id, r.id)}
                  alt={`Rendu réaliste : ${viewName(r)}`}
                  onOpen={() => setViewing(r.id)}
                  label={
                    tile.kind === "more"
                      ? `${tile.count} plus anciens : tout voir`
                      : `Ouvrir le rendu : ${viewName(r)}, ${hour(r.created_at)}`
                  }
                >
                  {tile.kind === "more" ? (
                    <span className="rgal-more">
                      +{tile.count} plus anciens · Tout voir
                    </span>
                  ) : (
                    <>
                      <span className="rgal-chip rgal-time">
                        {hour(r.created_at)}
                      </span>
                      {listing.cover_id === r.id && (
                        <span className="rgal-chip rgal-cover">couverture</span>
                      )}
                      {total > 0 && (
                        <span className="rgal-chip rgal-react">
                          {pills[0]!.emoji} {total}
                        </span>
                      )}
                    </>
                  )}
                </RetryTile>
              </li>
            );
          })}
        </ul>
      )}
      {g.problem && (
        <p className="realistic-message" role="status">
          {problemText(g.problem)}
        </p>
      )}
      {message && (
        <p className="realistic-message" role="alert">
          {message}
        </p>
      )}
      {viewingIndex >= 0 && (
        <RenderViewer
          project={project}
          levelId={levelId}
          title={title}
          renders={g.done}
          index={viewingIndex}
          onIndex={(i) => setViewing(g.done[i]?.id ?? null)}
          onClose={() => setViewing(null)}
          coverId={listing.cover_id ?? null}
          reactions={listing.reactions ?? []}
          typicalSeconds={listing.typical_seconds}
          linked={linked}
          readOnly={readOnly}
          onCover={async (id, on) => {
            await (on
              ? realisticApi.setCover(project.id, id)
              : realisticApi.clearCover(project.id));
            await refresh();
          }}
          onReact={async (id, key: ReactionKey, on) => {
            await realisticApi.react(project.id, id, key, on);
            await refresh();
          }}
          onVariant={async (id) => {
            await realisticApi.variant(project.id, id).catch(noPlan);
            await refresh();
            setViewing(null);
          }}
          onFromPhoto={async (imageId) => {
            await realisticApi
              .fromPhoto(project.id, levelId, imageId)
              .catch(noPlan);
            await refresh();
            setViewing(null);
          }}
        />
      )}
    </div>
  );
}
