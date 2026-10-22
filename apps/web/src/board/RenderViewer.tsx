import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  realisticApi,
  REACTIONS,
  type ReactionKey,
  type RealisticReaction,
  type RealisticRequest,
} from "../api/client";
import type { Camera, Project } from "../api/types.gen";
import { allCameras, NO_SELECTION } from "../model";
import { etaText, photoNumber } from "./renderGallery";

/** KAN-91 (lot L6, maquette validée par augus le 22 oct.) : la visionneuse plein écran d'un
 *  rendu réaliste. Curseur avant/après contre la vue 3D du plan ou la photo réelle,
 *  téléchargement, couverture du projet, réactions (KAN-78), variantes (même vue, autre
 *  tirage), « Rendre depuis la photo » (D5) et ce dont vient le rendu. ← → passe d'un rendu à
 *  l'autre, Échap ferme. */

const Viewer3D = lazy(() =>
  import("../viewer3d/Viewer3D").then((m) => ({ default: m.Viewer3D })),
);

type Compare = "3d" | "photo";

export interface RenderViewerProps {
  project: Project;
  levelId: string;
  title: string;
  /** Les rendus faits, du plus récent au plus ancien. */
  renders: RealisticRequest[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
  coverId: string | null;
  reactions: RealisticReaction[];
  typicalSeconds: number | undefined;
  /** D2 : un plan est relié (sinon, ni variante ni nouveau rendu). */
  linked: boolean;
  readOnly: boolean;
  onCover: (requestId: string, on: boolean) => Promise<void>;
  onReact: (requestId: string, key: ReactionKey, on: boolean) => Promise<void>;
  onVariant: (requestId: string) => Promise<void>;
  onFromPhoto: (imageId: string) => Promise<void>;
}

function when(iso: string): string {
  return new Date(iso).toLocaleString("fr-FR", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function duration(seconds: number | null): string | null {
  if (seconds === null) return null;
  const s = Math.round(seconds);
  return s < 60
    ? `${s} s`
    : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")}`;
}

/** Les photos du niveau qui ont une caméra estimée, dans l'ordre de la bande de photos. */
function levelPhotos(project: Project, levelId: string): string[] {
  const level = project.levels[levelId];
  if (!level) return [];
  const withCamera = new Set(
    allCameras(level)
      .map((c) => c.image_id)
      .filter((id): id is string => Boolean(id && project.images[id])),
  );
  return Object.keys(project.images).filter((id) => withCamera.has(id));
}

export function RenderViewer(props: RenderViewerProps) {
  const {
    project,
    levelId,
    renders,
    index,
    onIndex,
    onClose,
    coverId,
    reactions,
    typicalSeconds,
  } = props;
  const render = renders[Math.min(index, renders.length - 1)];
  const level = project.levels[levelId];
  const camera = (render && level?.elements[render.camera_id]) as
    Camera | undefined;
  const photos = useMemo(
    () => levelPhotos(project, levelId),
    [project, levelId],
  );
  const comparePhoto = render?.photo_id ?? photos[0] ?? null;
  const faithful = Boolean(
    render?.photo_id && render.photo_id === comparePhoto,
  );
  const [compare, setCompare] = useState<Compare>(
    render?.photo_id ? "photo" : "3d",
  );
  const [cut, setCut] = useState(50);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stage = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  // Un autre rendu : la comparaison par défaut suit (photo pour un rendu depuis la photo).
  const renderId = render?.id;
  const fromPhoto = Boolean(render?.photo_id);
  useEffect(() => {
    setCompare(fromPhoto ? "photo" : "3d");
    setError(null);
  }, [renderId, fromPhoto]);

  useEffect(() => {
    stage.current?.style.setProperty("--rview-cut", `${cut}%`);
  }, [cut]);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  // Comme PhotoMeasure : le clavier n'atteint pas l'espace de travail (Suppr, Échap, etc.).
  const count = renders.length;
  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) {
        // Le curseur avant/après garde ses flèches ; Échap ferme quand même.
        if (e.key === "Escape") {
          e.stopPropagation();
          onClose();
        }
        return;
      }
      e.stopPropagation();
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowLeft" && index > 0) {
        e.preventDefault();
        onIndex(index - 1);
      } else if (e.key === "ArrowRight" && index < count - 1) {
        e.preventDefault();
        onIndex(index + 1);
      }
    };
    window.addEventListener("keydown", down, true);
    return () => window.removeEventListener("keydown", down, true);
  }, [index, count, onClose, onIndex]);

  if (!render) return null;
  const number = comparePhoto ? photoNumber(project, comparePhoto) : null;
  const photoName = number ? `photo ${number}` : "photo";
  const viewName = render.photo_id
    ? `point de vue exact de la ${photoName}`
    : `vue « ${camera?.name || "Vue"} »`;
  const isCover = coverId === render.id;
  const variants = renders.filter((r) => r.camera_id === render.camera_id);
  const eta = etaText(typicalSeconds);
  const canAct = !props.readOnly;

  const act = async (job: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await job();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const before =
    compare === "photo" && comparePhoto ? (
      <img
        className="rview-layer"
        src={api.imageFileUrl(project.id, comparePhoto)}
        alt={`Photo réelle (${photoName})`}
        draggable={false}
      />
    ) : camera ? (
      <div className="rview-layer rview-3d" aria-label="Vue 3D du plan">
        <Suspense fallback={<span className="rview-wait">Vue 3D…</span>}>
          <Viewer3D
            url={api.exportGlbUrl(project.id, levelId, project.history_cursor)}
            selection={NO_SELECTION}
            onSelect={() => undefined}
            viewFrom={{
              pose: {
                position: camera.position,
                yaw: camera.yaw,
                pitch: camera.pitch,
              },
              id: 1,
            }}
          />
        </Suspense>
      </div>
    ) : (
      <span className="rview-layer rview-wait">Vue introuvable</span>
    );

  return createPortal(
    <div
      className="rview"
      role="dialog"
      aria-modal="true"
      aria-label="Visionneuse des rendus"
      onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <header className="rview-head">
        <button
          ref={closeRef}
          type="button"
          className="icon rview-close"
          aria-label="Fermer la visionneuse"
          onClick={onClose}
        >
          ×
        </button>
        <strong className="rview-title">{props.title}</strong>
        <span className="rview-meta">
          {index + 1} sur {renders.length} · {when(render.created_at)} ·{" "}
          {viewName}
        </span>
        <span className="rview-keys">← → pour passer d'un rendu à l'autre</span>
      </header>
      <div className="rview-body">
        <button
          type="button"
          className="icon rview-nav rview-prev"
          aria-label="Rendu précédent"
          disabled={index === 0}
          onClick={() => onIndex(index - 1)}
        >
          ‹
        </button>
        <div className="rview-stage" ref={stage}>
          <div className="rview-before">{before}</div>
          <div className="rview-after">
            <img
              className="rview-layer"
              src={realisticApi.imageUrl(project.id, render.id)}
              alt={`Rendu réaliste : ${viewName}`}
              width={render.width ?? undefined}
              height={render.height ?? undefined}
              draggable={false}
            />
          </div>
          <span className="rview-tag rview-tag-before">
            {compare === "photo" && comparePhoto
              ? `Photo réelle (${photoName})`
              : "Vue 3D"}
          </span>
          <span className="rview-tag rview-tag-after">
            {render.photo_id ? `Rendu depuis la ${photoName}` : "Rendu"}
          </span>
          <span className="rview-handle" aria-hidden="true">
            ‹ ›
          </span>
          <input
            className="rview-range"
            type="range"
            min={0}
            max={100}
            value={cut}
            aria-label="Avant / après"
            onChange={(e) => setCut(Number(e.target.value))}
          />
          {compare === "photo" &&
            comparePhoto &&
            (faithful ? (
              <p className="rview-note rview-note-ok" role="status">
                ✓ Même point de vue que la {photoName} : l'avant/après est
                fidèle.
              </p>
            ) : (
              <p className="rview-note rview-note-warn" role="status">
                <span>
                  Ce rendu n'a pas le point de vue de la {photoName} : la
                  comparaison est approximative.
                </span>
                {canAct && (
                  <button
                    type="button"
                    className="primary"
                    disabled={busy || !props.linked}
                    onClick={() =>
                      void act(() => props.onFromPhoto(comparePhoto))
                    }
                  >
                    Rendre depuis la {photoName} · {eta}
                  </button>
                )}
              </p>
            ))}
        </div>
        <button
          type="button"
          className="icon rview-nav rview-next"
          aria-label="Rendu suivant"
          disabled={index >= renders.length - 1}
          onClick={() => onIndex(index + 1)}
        >
          ›
        </button>
        <aside className="rview-side" aria-label="Actions du rendu">
          <h3 className="rview-h">Comparer avec</h3>
          <div className="rview-seg" role="group" aria-label="Comparer avec">
            <button
              type="button"
              aria-pressed={compare === "3d"}
              onClick={() => setCompare("3d")}
            >
              Vue 3D
            </button>
            <button
              type="button"
              aria-pressed={compare === "photo"}
              disabled={!comparePhoto}
              title={
                comparePhoto ? undefined : "Aucune photo analysée sur ce plan"
              }
              onClick={() => setCompare("photo")}
            >
              Photo réelle
            </button>
          </div>
          <a
            className="rview-download"
            href={realisticApi.imageUrl(project.id, render.id)}
            download={`rendu-${render.id.slice(0, 8)}.png`}
          >
            Télécharger
            {render.width && render.height
              ? ` (PNG, ${render.width} × ${render.height})`
              : ""}
          </a>
          {canAct && (
            <button
              type="button"
              aria-pressed={isCover}
              disabled={busy}
              onClick={() => void act(() => props.onCover(render.id, !isCover))}
              title="La vignette du projet dans « Mes projets »"
            >
              {isCover
                ? "Couverture du projet ✓"
                : "Définir comme couverture du projet"}
            </button>
          )}
          {canAct && comparePhoto && !faithful && (
            <button
              type="button"
              disabled={busy || !props.linked}
              onClick={() => void act(() => props.onFromPhoto(comparePhoto))}
            >
              Rendre depuis la {photoName} · {eta}
            </button>
          )}
          <h3 className="rview-h">Réagir</h3>
          <div className="rview-reactions" role="group" aria-label="Réagir">
            {REACTIONS.map((r) => {
              const pill = reactions.find(
                (p) => p.item_id === render.id && p.key === r.key,
              );
              return (
                <button
                  key={r.key}
                  type="button"
                  className="rview-reaction"
                  aria-pressed={Boolean(pill?.mine)}
                  aria-label={`${r.label}${pill ? ` (${pill.count})` : ""}`}
                  title={
                    pill ? `${r.label} : ${pill.names.join(", ")}` : r.label
                  }
                  disabled={busy}
                  onClick={() =>
                    void act(() => props.onReact(render.id, r.key, !pill?.mine))
                  }
                >
                  {r.emoji}
                  {pill ? ` ${pill.count}` : ""}
                </button>
              );
            })}
          </div>
          <h3 className="rview-h">Variantes</h3>
          <div className="rview-variants">
            {variants.map((v) => (
              <button
                key={v.id}
                type="button"
                className="rview-variant"
                aria-pressed={v.id === render.id}
                aria-label={`Variante du ${when(v.created_at)}`}
                onClick={() => onIndex(renders.indexOf(v))}
              >
                <img
                  src={realisticApi.imageUrl(project.id, v.id)}
                  alt=""
                  draggable={false}
                />
              </button>
            ))}
          </div>
          {canAct && (
            <button
              type="button"
              disabled={busy || !props.linked}
              onClick={() => void act(() => props.onVariant(render.id))}
            >
              Nouvelle variante · {eta}
            </button>
          )}
          <h3 className="rview-h">D'où vient ce rendu</h3>
          <ul className="rview-origin">
            <li>
              Plan · {level?.name || "niveau"},{" "}
              {render.photo_id
                ? `point de vue de la ${photoName} (caméra estimée)`
                : `point de vue « ${camera?.name || "Vue"} »`}
            </li>
            <li>
              Calculé par le poste de rendu
              {duration(render.seconds)
                ? ` en ${duration(render.seconds)}`
                : ""}
            </li>
          </ul>
          {error && (
            <p className="rview-error" role="alert">
              {error}
            </p>
          )}
        </aside>
      </div>
    </div>,
    document.body,
  );
}
