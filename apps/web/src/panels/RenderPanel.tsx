import { useCapabilities } from "../state/capabilities";
import { useEffect, useRef, useState } from "react";
import {
  renderApi,
  type Aspect,
  type AutopilotRun,
  type AutoViews,
  type JobStatus,
  type FinishStyle,
  type RenderEntry,
  type RenderEstimate,
} from "../api/client";
import type { Level, Project } from "../api/types.gen";
import { allCameras, isFreeCamera, poseOf } from "../model";
import { firstVariant } from "../gallery";
import { PickableImage, type PickAction } from "./PickCard";
import { RenderGallery } from "./RenderGallery";
import type { ViewPose } from "../viewer3d/Viewer3D";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  renders: RenderEntry[];
  renderJob: JobStatus | null;
  /** Change quand le GLB du viewer est chargé : les vignettes sont (re)prises. */
  loadedToken: number;
  snapshot: (pose: ViewPose) => string | null;
  onView: (pose: ViewPose) => void;
  onRender: (
    cameraId: string,
    samples: number,
    gpu: boolean,
    daylight: string | null,
  ) => void;
  /** Nord du plan (degrés depuis +X, 90 = en haut) et son réglage. */
  northDeg: number;
  onNorth: (deg: number) => void;
  onAddViewpoint: () => void;
  /** Vue d'architecte proposée (D3) : frontale, centrée, grand angle. */
  onArchitectView: () => void;
  /** Trois vues automatiques (V1) ; renvoie le compte rendu (vues manquées, gardées). */
  onAutoViews?: (aspect: Aspect | null) => Promise<AutoViews | undefined>;
  /** « Rendre toutes les vues » (V1), enchaînées en une tâche. */
  onRenderAll?: (
    samples: number,
    gpu: boolean,
    daylight: string | null,
  ) => void;
  /** Arrivée du chemin « zéro geste » (V1, point 6) : ses rendus d'abord, et les accès. */
  arrival?: {
    run: AutopilotRun;
    onRefine: () => void;
    onAmbiance: () => void;
    onClose: () => void;
    /** « Version catalogue » d'une vue, à la demande (V1.7b). */
    onCatalogue?: (runId: string) => void;
    busy?: boolean;
    /** Le plan a été recalé sur la surface donnée (L2) : l'annuler en un clic. */
    onUndoAreaGuard?: () => void;
  } | null;
  /** Finition IA d'un rendu (D5) : à la main ; le chemin automatique l'enchaîne (V1). */
  onFinish: (
    runId: string,
    strength: number,
    style: FinishStyle | null,
  ) => void;
  onPlace: (cameraId: string) => void;
  onRefresh: () => void;
  /** Erreur d'une action de la galerie (favori, titre, suppression). */
  onError?: (message: string) => void;
  /** Coupe active dans l'aperçu 3D (E3) : les rendus se font avec ce plan. */
  sectionNote?: string | null;
  /** Coupe verticale active (E3b) : propose la perspective de coupe ; son nom pour savoir
   *  si elle existe déjà. */
  onSectionView?: (() => void) | null;
  sectionViewName?: string | null;
}

const DAYLIGHTS: { id: string; label: string }[] = [
  { id: "matin", label: "matin (soleil bas à l'est)" },
  { id: "midi", label: "midi (soleil haut au sud)" },
  { id: "heure_doree", label: "heure dorée (soleil bas et chaud)" },
  { id: "soir", label: "soir (soleil bas à l'ouest)" },
  { id: "nuit", label: "nuit (lampes intérieures)" },
];

const NORTHS: { deg: number; label: string }[] = [
  { deg: 90, label: "en haut du plan" },
  { deg: 0, label: "à droite du plan" },
  { deg: 270, label: "en bas du plan" },
  { deg: 180, label: "à gauche du plan" },
];

/** Côté du plan (haut, droite, bas, gauche) d'un cap boussole, selon le nord du plan. */
function sideOf(northDeg: number, compassDeg: number): string {
  const angle = (((northDeg - compassDeg) % 360) + 360) % 360;
  if (angle >= 45 && angle < 135) return "le haut";
  if (angle >= 135 && angle < 225) return "la gauche";
  if (angle >= 225 && angle < 315) return "le bas";
  return "la droite";
}

function northSentence(northDeg: number): string {
  const north = NORTHS.find((n) => n.deg === northDeg);
  return `Le nord est ${north ? north.label : `à ${northDeg}°`}. Le soleil du matin vient de ${sideOf(northDeg, 100)} du plan, celui de midi de ${sideOf(northDeg, 180)}, celui du soir de ${sideOf(northDeg, 260)}.`;
}

const QUALITIES: { samples: number; label: string }[] = [
  { samples: 16, label: "rapide" },
  { samples: 64, label: "normale" },
  { samples: 256, label: "fine" },
];

/** Panneau de l'onglet Rendus (décision d'augus, attentes PO) : les points de vue (caméras des photos
 *  et points de vue libres posés dans le plan), une vignette du viewer 3D pour chacun même
 *  sans Blender, le rendu réaliste par point de vue en tâche de fond avec la durée annoncée
 *  et le périphérique, puis la galerie des rendus (point de vue, date, téléchargement). */
export function RenderPanel({
  project,
  level,
  busy,
  renders,
  renderJob,
  loadedToken,
  snapshot,
  onView,
  onRender,
  onAddViewpoint,
  onArchitectView,
  onAutoViews,
  onRenderAll,
  arrival = null,
  onFinish,
  onPlace,
  onRefresh,
  onError,
  sectionNote = null,
  onSectionView = null,
  sectionViewName = null,
  northDeg,
  onNorth,
}: Props) {
  const [samples, setSamples] = useState(64);
  const [gpu, setGpu] = useState(true);
  const [daylight, setDaylight] = useState("");
  const [estimate, setEstimate] = useState<RenderEstimate | null>(null);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const viewpoints = allCameras(level);
  const running = renderJob?.status === "running";
  const [aspect, setAspect] = useState<Aspect>(() => intentAspect(level));
  const [autoReport, setAutoReport] = useState<AutoViews | null>(null);
  const hasAuto = viewpoints.some((c) => autoRole(c) !== null);
  const freeCount = viewpoints.filter((c) => isFreeCamera(c)).length;

  useEffect(() => {
    let cancelled = false;
    renderApi.estimate(project.id, level.id, samples).then(
      (e) => {
        if (!cancelled) setEstimate(e);
      },
      () => {
        if (!cancelled) setEstimate(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [project.id, level.id, samples]);

  useEffect(() => {
    onRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Un rendu vient d'arriver : la galerie est souvent sous la ligne de flottaison du
  // panneau (audit responsive 2.5) ; on la fait remonter à l'écran.
  const galleryRef = useRef<HTMLDivElement>(null);
  const renderCount = renders.length;
  const seenCount = useRef(renderCount);
  useEffect(() => {
    if (renderCount > seenCount.current)
      galleryRef.current?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    seenCount.current = renderCount;
  }, [renderCount]);

  // Vignettes du viewer : une par point de vue, reprises quand la scène ou les poses changent.
  const poseKey = viewpoints
    .map(
      (c) =>
        `${c.id}:${c.position.x.toFixed(2)},${c.position.y.toFixed(2)},${c.position.z.toFixed(2)},${c.yaw.toFixed(3)},${c.pitch.toFixed(3)}`,
    )
    .join("|");
  useEffect(() => {
    if (!loadedToken) return;
    const handle = window.setTimeout(() => {
      const next: Record<string, string> = {};
      for (const cam of viewpoints) {
        const data = snapshot(poseOf(cam));
        if (data) next[cam.id] = data;
      }
      setThumbs(next);
    }, 150);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedToken, poseKey]);

  const blender = estimate?.blender_available ?? false;
  const canRender = blender && !running && !busy;
  const progress =
    renderJob && renderJob.expected_s
      ? Math.min(100, (renderJob.elapsed_s / renderJob.expected_s) * 100)
      : 0;

  const viewsRef = useRef<HTMLElement>(null);
  // Dans l'ordre du chemin (ensemble, zone, détail), les favoris d'abord (US2).
  const arrived = arrival
    ? arrival.run.run_ids
        .flatMap((id) => renders.filter((r) => r.run_id === id))
        .map((r, i) => ({ r, i, fav: (r.favorites?.length ?? 0) > 0 }))
        .sort((x, y) => Number(y.fav) - Number(x.fav) || x.i - y.i)
        .map((x) => x.r)
    : [];

  return (
    <aside className="inspector render-panel">
      {arrival && (
        <section className="arrival" aria-label="Vos rendus">
          <h2>Vos rendus</h2>
          <p className="muted">
            Plan, ambiance, aménagement et vues posés automatiquement en{" "}
            {arrival.run.total_s.toFixed(0)} s. Tout s'annule (Ctrl+Z) ; ce que
            vous changez à la main n'est jamais réécrit.
          </p>
          {
            <section
              className="request-check"
              aria-label="Respect de votre demande"
            >
              <h3>Respect de votre demande</h3>
              {(arrival.run.request?.length ?? 0) === 0 && (
                <p className="muted">
                  Aucune demande particulière : ambiance déduite.
                </p>
              )}
              <ul>
                {arrival.run.request?.map((item, i) => (
                  <li key={i} className={`req-${item.status}`}>
                    <span className="req-mark" aria-hidden="true">
                      {item.status === "ok"
                        ? "✓"
                        : item.status === "partial"
                          ? "~"
                          : "✗"}
                    </span>
                    <span className="sr-only">
                      {item.status === "ok"
                        ? "tenu : "
                        : item.status === "partial"
                          ? "en partie : "
                          : "non tenu : "}
                    </span>
                    <strong>{item.label}</strong> : {item.wanted}
                    {item.detail && (
                      <span className="muted"> — {item.detail}</span>
                    )}
                    {item.label === "Surface" &&
                      item.detail.startsWith("plan recalé") &&
                      arrival.onUndoAreaGuard && (
                        <button
                          className="link"
                          disabled={arrival.busy}
                          onClick={arrival.onUndoAreaGuard}
                        >
                          annuler le recalage
                        </button>
                      )}
                  </li>
                ))}
              </ul>
            </section>
          }
          {/* Les images sont au centre, dans l'onglet Rendus (P1) ; ici, l'échec seulement. */}
          {arrived.length === 0 && (
            <p className="muted">
              Aucun rendu :{" "}
              {arrival.run.steps
                .filter((s) => s.status === "failed")
                .map((s) => `${s.label} (${s.note})`)
                .join(" ; ") || "voir les étapes ci-dessous"}
            </p>
          )}
          <div className="row">
            <button className="primary" onClick={arrival.onRefine}>
              Affiner le plan
            </button>
            <button onClick={arrival.onAmbiance}>Changer l'ambiance</button>
            <button
              onClick={() =>
                viewsRef.current?.scrollIntoView({
                  behavior: "smooth",
                  block: "start",
                })
              }
            >
              Modifier les vues
            </button>
          </div>
          <details>
            <summary>Ce qui a été fait</summary>
            <ul className="muted pilot-steps">
              {arrival.run.steps.map((s) => (
                <li key={s.name} className={`step-${s.status}`}>
                  {s.label} :{" "}
                  {s.status === "done"
                    ? "fait"
                    : s.status === "skipped"
                      ? "sauté"
                      : "échec"}
                  {s.note ? ` (${s.note})` : ""}
                  {s.seconds ? ` · ${s.seconds.toFixed(0)} s` : ""}
                </li>
              ))}
            </ul>
          </details>
          <button className="link" onClick={arrival.onClose}>
            fermer
          </button>
        </section>
      )}
      {onAutoViews && (
        <section className="auto-views" ref={viewsRef}>
          <h2>Vues automatiques</h2>
          <p className="muted">
            Une vue d'ensemble depuis l'arrivée dans la pièce, une vue de la
            zone meublée principale, un détail (lit et chevet…). Une vue que
            vous déplacez à la main n'est jamais remplacée.
          </p>
          <label className="field">
            <span>Format</span>
            <select
              aria-label="Format des vues"
              value={aspect}
              onChange={(e) => setAspect(e.target.value as Aspect)}
            >
              <option value="4:3">4:3 paysage</option>
              <option value="4:5">4:5 portrait (Pinterest)</option>
            </select>
            <em />
          </label>
          <div className="row">
            <button
              disabled={busy}
              onClick={() =>
                void onAutoViews(aspect).then((r) => setAutoReport(r ?? null))
              }
              title="Pose (ou repose) les trois vues automatiques ; annulable d'un geste"
            >
              {hasAuto ? "Reposer les vues" : "Poser les 3 vues"}
            </button>
            {onRenderAll && (
              <button
                className="primary"
                disabled={!canRender || freeCount === 0}
                onClick={() => onRenderAll(samples, gpu, daylight || null)}
                title={
                  !blender
                    ? "Blender n'est pas installé : pas de rendu réaliste"
                    : `Rend les vues automatiques puis vos autres points de vue, l'un après l'autre (${freeCount} vue${freeCount > 1 ? "s" : ""})`
                }
              >
                Rendre toutes les vues
              </button>
            )}
          </div>
          {autoReport &&
            (autoReport.skipped.length > 0 || autoReport.kept.length > 0) && (
              <ul className="muted auto-report" role="status">
                {[...autoReport.kept, ...autoReport.skipped].map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            )}
        </section>
      )}
      <section>
        <h2>Points de vue</h2>
        <p className="muted">
          Les caméras de vos photos et les points de vue que vous posez dans le
          plan. La vignette vient du viewer 3D ; le rendu réaliste vient de
          Blender.
        </p>
        {sectionNote && (
          <p className="section-note" role="status">
            Rendus en coupe ({sectionNote}), comme l'aperçu 3D ; en coupe
            verticale, la moitié où se trouve le point de vue est retirée.
          </p>
        )}
        <div className="row">
          <button
            onClick={onAddViewpoint}
            disabled={busy}
            title="Ajoute un point de vue au centre du plan ; placez-le et visez ensuite dans le plan (glisser le rond, glisser la pointe)"
          >
            + Ajouter un point de vue
          </button>
          <button
            onClick={onArchitectView}
            disabled={
              busy || viewpoints.some((c) => c.name === "Vue d'architecte")
            }
            title="Vue frontale, au centre de la pièce, dans l'axe de sa longueur, grand angle : le cadrage des photos d'intérieur"
          >
            Vue d'architecte
          </button>
          {onSectionView && (
            <button
              onClick={onSectionView}
              disabled={
                busy || viewpoints.some((c) => c.name === sectionViewName)
              }
              title="Perspective de coupe : face à la tranche, perpendiculaire au plan de coupe, reculée pour cadrer toute la coupe ; fond uni, faces coupées en poché"
            >
              Vue de coupe
            </button>
          )}
        </div>
        {viewpoints.length === 0 && (
          <p className="muted">
            Aucun point de vue : ajoutez-en un, ou importez une photo (sa caméra
            en devient un).
          </p>
        )}
        <ul className="viewpoints">
          {viewpoints.map((cam) => {
            const name =
              cam.name ?? (isFreeCamera(cam) ? "Point de vue" : "Photo");
            const thumb = thumbs[cam.id];
            return (
              <li key={cam.id}>
                <button
                  className="thumb-3d"
                  onClick={() => onView(poseOf(cam))}
                  title="Se placer à ce point de vue dans le viewer 3D"
                >
                  {thumb ? (
                    <img src={thumb} alt="" />
                  ) : (
                    <span className="muted">vignette…</span>
                  )}
                </button>
                <div className="viewpoint-meta">
                  <strong>{name}</strong>
                  <span className="muted">
                    {autoRole(cam) !== null
                      ? cam.source === "user_defined"
                        ? "vue automatique retouchée"
                        : "vue automatique"
                      : isFreeCamera(cam)
                        ? "point de vue libre"
                        : "caméra de photo"}{" "}
                    · ({cam.position.x.toFixed(2)} ; {cam.position.y.toFixed(2)}
                    ) · {((cam.yaw * 180) / Math.PI).toFixed(0)}° · h{" "}
                    {cam.position.z.toFixed(2)} m
                  </span>
                  <div className="row">
                    <button
                      className="link"
                      onClick={() => onView(poseOf(cam))}
                    >
                      voir en 3D
                    </button>
                    {isFreeCamera(cam) && (
                      <button className="link" onClick={() => onPlace(cam.id)}>
                        placer dans le plan
                      </button>
                    )}
                    <button
                      className="primary"
                      disabled={!canRender}
                      onClick={() =>
                        onRender(cam.id, samples, gpu, daylight || null)
                      }
                      title={
                        !blender
                          ? "Blender n'est pas installé : pas de rendu réaliste (la vignette 3D reste)"
                          : running
                            ? "Un rendu est déjà en cours"
                            : `Rendu Blender, qualité ${QUALITIES.find((q) => q.samples === samples)?.label ?? ""} : ${estimate?.message ?? ""}`
                      }
                    >
                      Rendre
                    </button>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      <section>
        <h2>Rendu réaliste</h2>
        <label className="field">
          <span>Qualité</span>
          <select
            value={samples}
            onChange={(e) => setSamples(Number(e.target.value))}
          >
            {QUALITIES.map((q) => (
              <option key={q.samples} value={q.samples}>
                {q.label} ({q.samples})
              </option>
            ))}
          </select>
          <em>éch.</em>
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={gpu}
            onChange={(e) => setGpu(e.target.checked)}
          />
          GPU si disponible (sinon processeur, plus lent)
        </label>
        <label className="field">
          <span>Éclairage</span>
          <select
            value={daylight}
            onChange={(e) => setDaylight(e.target.value)}
            title="Heure du jour : le soleil est placé d'après le nord du plan ; neutre = éclairage d'atelier"
          >
            <option value="">
              auto (lumière du jour si la pièce a des fenêtres)
            </option>
            {DAYLIGHTS.map((d) => (
              <option key={d.id} value={d.id}>
                {d.label}
              </option>
            ))}
          </select>
          <em />
        </label>
        <label className="field">
          <span>Nord du plan</span>
          <select
            value={String(northDeg)}
            onChange={(e) => onNorth(Number(e.target.value))}
            disabled={busy}
            title="Où est le nord sur votre plan ? Il oriente le soleil des rendus (boussole en bas à droite du plan)"
          >
            {NORTHS.map((n) => (
              <option key={n.deg} value={String(n.deg)}>
                {n.label}
              </option>
            ))}
            {!NORTHS.some((n) => n.deg === northDeg) && (
              <option value={String(northDeg)}>{northDeg}°</option>
            )}
          </select>
          <em />
        </label>
        <p className="muted north-note">{northSentence(northDeg)}</p>
        {estimate ? (
          <p className={blender ? "muted" : "warn"}>
            {blender
              ? `Périphérique : ${estimate.device_label}. ${estimate.message}`
              : estimate.message}
          </p>
        ) : (
          <p className="muted">…</p>
        )}
        {renderJob && (
          <div className="job-status">
            {running && <span className="spinner" />}
            <div className="job-text">
              <span>
                {running
                  ? `${renderJob.stage ? `${renderJob.stage} · ` : ""}Rendu en cours (${renderJob.device}) : ${renderJob.elapsed_s.toFixed(0)} s`
                  : renderJob.status === "done"
                    ? `Rendu terminé en ${renderJob.elapsed_s.toFixed(0)} s.`
                    : `Rendu échoué : ${renderJob.error ?? "erreur"}`}
                {running && renderJob.expected_s
                  ? ` sur environ ${renderJob.expected_s.toFixed(0)} s`
                  : ""}
              </span>
              {running && (
                <span className="progress">
                  <span style={{ width: `${progress}%` }} />
                </span>
              )}
            </div>
          </div>
        )}
      </section>

      <div ref={galleryRef}>
        <RenderGallery
          projectId={project.id}
          renders={renders}
          busy={busy}
          onChanged={onRefresh}
          onError={onError}
          extra={(r) =>
            r.finished || r.variant != null ? null : (
              <FinishControls
                busy={busy}
                onFinish={(strength, style) =>
                  onFinish(r.run_id, strength, style)
                }
              />
            )
          }
        />
      </div>
    </aside>
  );
}

/** Une vue à l'arrivée (V1, sortie B) : l'image d'inspiration d'abord (allure catalogue,
 *  « cotes non garanties »), et la bascule vers A (rendu Blender + finition, la référence). */
export function ArrivalView({
  anchorId,
  projectId,
  entry,
  stale = false,
  onRefine,
  onPickAction,
  onCatalogue,
  busy,
}: {
  /** Ancre de la vue (bande de rendus, P2). */
  anchorId?: string;
  projectId: string;
  entry: RenderEntry;
  /** Le plan a changé depuis ce rendu (P1) : marqué « périmée ». */
  stale?: boolean;
  /** « Affiner cette vue » (P3) : l'onglet 3D à la caméra du rendu, la pièce choisie. */
  onRefine?: () => void;
  /** Désigner un élément dans l'image (P4b) : un clic ouvre la carte de désignation. */
  onPickAction?: (action: PickAction) => void;
  /** « Version catalogue » à la demande (V1.7b), jamais automatique. */
  onCatalogue?: (runId: string) => void;
  busy: boolean;
}) {
  const { images: canImages } = useCapabilities();
  // Une image gardée en favori (US2) est montrée d'abord.
  const [side, setSide] = useState<"A" | "B" | "C">(() => {
    if (entry.favorites?.length) {
      const v = firstVariant(entry);
      return v === "inspiration" ? "B" : v === "catalogue" ? "C" : "A";
    }
    return entry.inspiration ? "B" : "A";
  });
  const favorite = (entry.favorites?.length ?? 0) > 0;
  const showC = side === "C" && entry.catalogue;
  const showB = !showC && side === "B" && entry.inspiration;
  const aUrl = entry.finished
    ? renderApi.finishedUrl(projectId, entry)
    : renderApi.imageUrl(projectId, entry);
  const url = showC
    ? renderApi.catalogueUrl(projectId, entry)
    : showB
      ? renderApi.inspirationUrl(projectId, entry)
      : aUrl;
  const alt = `${showC ? "Version catalogue" : showB ? "Inspiration" : "Rendu"} ${entry.camera_name}`;
  return (
    <li id={anchorId} className={stale ? "stale" : undefined}>
      {onPickAction ? (
        <PickableImage
          projectId={projectId}
          runId={entry.run_id}
          url={url}
          alt={alt}
          busy={busy}
          onAction={onPickAction}
        />
      ) : (
        <a href={url} target="_blank" rel="noreferrer">
          <img src={url} alt={alt} />
        </a>
      )}
      <span>
        {stale && (
          <span
            className="badge stale"
            title="Le plan, l'ambiance ou la vue ont changé depuis ce rendu : « Re-rendre » le met à jour"
          >
            périmée
          </span>
        )}{" "}
        {favorite && (
          <span className="arrival-fav" title="Gardé en favori">
            ★{" "}
          </span>
        )}
        {entry.title || entry.camera_name} ·{" "}
        {showC ? (
          <span className="badge generated">
            {entry.catalogue?.label ?? "Inspiration · matières libres"}
          </span>
        ) : showB ? (
          <span className="badge generated">
            {entry.inspiration?.label ?? "Inspiration · cotes non garanties"}
          </span>
        ) : (
          <span className="badge">
            {entry.finished
              ? "référence : Blender + finition"
              : "référence : Blender"}
          </span>
        )}{" "}
        ·{" "}
        <a
          href={renderApi.imageUrl(projectId, entry)}
          target="_blank"
          rel="noreferrer"
        >
          image Blender
        </a>
      </span>
      {entry.inspiration && (
        <div
          className="ab-toggle"
          role="group"
          aria-label={`A ou B : ${entry.camera_name}`}
        >
          <button
            type="button"
            aria-pressed={side === "B"}
            onClick={() => setSide("B")}
            title="Image d'inspiration refaite par un modèle d'images : allure catalogue, cotes et matières non garanties"
          >
            B inspiration
          </button>
          <button
            type="button"
            aria-pressed={side === "A"}
            onClick={() => setSide("A")}
            title="Rendu Blender des cotes, avec sa finition légère : la référence"
          >
            A référence
          </button>
          {entry.catalogue && (
            <button
              type="button"
              aria-pressed={side === "C"}
              onClick={() => setSide("C")}
              title="Version catalogue : image refaite librement, matières et cotes non garanties"
            >
              Catalogue
            </button>
          )}
        </div>
      )}
      {onCatalogue && !entry.catalogue && canImages && (
        <button
          type="button"
          className="link"
          disabled={busy}
          onClick={() => {
            setSide("C");
            onCatalogue(entry.run_id);
          }}
          title="Image refaite librement par le modèle d'images (allure la plus « catalogue », environ 45 s) : matières et cotes non garanties ; à la demande seulement"
        >
          Version catalogue
        </button>
      )}
      {onRefine && (
        <button
          type="button"
          className="refine-view"
          onClick={onRefine}
          title="Ouvre la 3D depuis la caméra de ce rendu, la pièce choisie : déplacez, remplacez, puis « Re-rendre »"
        >
          Affiner cette vue
        </button>
      )}
    </li>
  );
}

type Viewpoint = ReturnType<typeof allCameras>[number];

/** Rôle d'une vue automatique (V1) : ensemble, zone, détail ; null pour les autres. */
function autoRole(cam: Viewpoint): string | null {
  const role = cam.metadata?.auto_view;
  return typeof role === "string" ? role : null;
}

/** Format des vues d'après l'intention de la pièce (4:3 sinon). */
function intentAspect(level: Level): Aspect {
  for (const e of Object.values(level.elements)) {
    if (e.type === "room" && e.intent?.views?.aspect === "4:5") return "4:5";
  }
  return "4:3";
}

/** Finition IA (D5) : force bornée (0,35 par défaut, 0,55 au plus) et style ; un clic. */
function FinishControls({
  busy,
  onFinish,
}: {
  busy: boolean;
  onFinish: (strength: number, style: FinishStyle | null) => void;
}) {
  const { images: canImages } = useCapabilities();
  const [strength, setStrength] = useState(0.35);
  const [style, setStyle] = useState<FinishStyle | "">("");
  return (
    <div className="finish-controls row">
      <select
        aria-label="Force de la finition"
        value={strength}
        onChange={(e) => setStrength(Number(e.target.value))}
      >
        <option value={0.35}>légère (0,35)</option>
        <option value={0.45}>moyenne (0,45)</option>
        <option value={0.55}>marquée (0,55)</option>
      </select>
      <select
        aria-label="Style de la finition"
        value={style}
        onChange={(e) => setStyle(e.target.value as FinishStyle | "")}
        title="Par défaut, la finition suit l'ambiance de la pièce (style, humeur, matières, couleurs, lumière)"
      >
        <option value="">selon l'ambiance</option>
        <option value="naturel">naturel</option>
        <option value="chalet">chalet</option>
        <option value="scandinave">scandinave</option>
        <option value="industriel">industriel</option>
        <option value="contemporain">contemporain</option>
      </select>
      {canImages && (
        <button
          disabled={busy}
          onClick={() => onFinish(strength, style || null)}
          title="Retouche photo par un modèle d'images local, guidée par la profondeur et les arêtes du rendu : la géométrie ne bouge pas, l'image Blender reste. Image marquée « générée »."
        >
          Finition IA
        </button>
      )}
    </div>
  );
}
