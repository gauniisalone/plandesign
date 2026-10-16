import { useEffect, useRef, useState } from "react";
import {
  api,
  type DeviceInfo,
  type ImageRef,
  inspirationApi,
  type InspirationRun,
  type JobStatus,
} from "../api/client";
import type { Op } from "../api/ops";
import type { Level, Project, ValidationReport } from "../api/types.gen";
import {
  cameras,
  readTransform,
  viewsOfImage,
  violationFor,
  type BackgroundTransform,
  type Selection,
} from "../model";
import { ViewActions, ViewSummary } from "./ViewActions";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  report: ValidationReport | null;
  selection: Selection;
  onSelect: (s: Selection) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
  imageWarnings: Record<string, string[]>;
  lastInspiration: InspirationRun | null;
  inspirationJob: JobStatus | null;
  onInspire: (
    imageId: string,
    prompt: string,
    seed: number,
    steps: number,
  ) => void;
  onUpload: (file: File) => void;
  onRemove: (imageId: string) => void;
  onOp: (op: Op) => void;
}

/** Photos du projet (locales, EXIF retiré) : fond du plan, vues, suppression. L'analyse est
 *  faite toute seule à l'import ; ici, aucun bouton d'analyse. */
export function PhotoPanel({
  project,
  level,
  busy,
  report,
  selection,
  onSelect,
  onAdoptView,
  onRotateView,
  imageWarnings,
  lastInspiration,
  inspirationJob,
  onInspire,
  onUpload,
  onRemove,
  onOp,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const images = Object.values(project.images);
  // Navigation par photos : les vues (caméras de photos) dans l'ordre d'ajout ; la vue
  // sélectionnée est mise en évidence ici, dans le plan (champ de vision) et l'inspecteur.
  const views = cameras(level);
  const selectedId = selection.kind === "element" ? selection.id : null;
  const viewIndex = views.findIndex((v) => v.id === selectedId);
  const stepView = (delta: number) => {
    if (views.length === 0) return;
    const next =
      viewIndex < 0
        ? delta > 0
          ? 0
          : views.length - 1
        : (viewIndex + delta + views.length) % views.length;
    onSelect({ kind: "element", id: views[next]!.id });
  };
  return (
    <aside className="inspector">
      <section>
        <h2>Photos</h2>
        {views.length > 0 && (
          <div className="row view-nav">
            <button onClick={() => stepView(-1)} title="Vue précédente">
              ◀ Vue précédente
            </button>
            <span className="muted">
              {viewIndex >= 0
                ? `vue ${viewIndex + 1} / ${views.length}`
                : `${views.length} vue${views.length > 1 ? "s" : ""} dans le plan`}
            </span>
            <button onClick={() => stepView(1)} title="Vue suivante">
              Vue suivante ▶
            </button>
          </div>
        )}
        <p className="muted">
          Les photos restent dans le dossier du projet. Métadonnées EXIF{" "}
          {project.privacy.strip_exif ? "retirées" : "conservées"} ; focale{" "}
          {project.privacy.keep_exif_focal ? "conservée" : "ignorée"}. Chaque
          photo ajoutée est analysée toute seule.
        </p>
        <button
          disabled={busy}
          onClick={() => fileRef.current?.click()}
          title="La photo est analysée toute seule et complète le plan"
        >
          Ajouter une photo
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          style={{ display: "none" }}
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) onUpload(file);
            e.target.value = "";
          }}
        />
        {images.length === 0 && <p className="muted">Aucune photo importée.</p>}
        <ul className="photos">
          {images.map((image) => (
            <PhotoItem
              key={image.id}
              project={project}
              level={level}
              image={image}
              warnings={imageWarnings[image.id]}
              report={report}
              busy={busy}
              selectedId={selectedId}
              onSelect={onSelect}
              onAdoptView={onAdoptView}
              onRotateView={onRotateView}
              onRemove={onRemove}
              onOp={onOp}
            />
          ))}
        </ul>
      </section>
      {images.length > 0 && (
        <InspirationSection
          projectId={project.id}
          images={images}
          busy={busy}
          last={lastInspiration}
          job={inspirationJob}
          onInspire={onInspire}
        />
      )}
    </aside>
  );
}

/** Image d'inspiration générée en local : opt-in, marquée « générée », jamais une mesure. */
function InspirationSection({
  projectId,
  images,
  busy,
  last,
  job,
  onInspire,
}: {
  projectId: string;
  images: ImageRef[];
  busy: boolean;
  last: InspirationRun | null;
  job: JobStatus | null;
  onInspire: (
    imageId: string,
    prompt: string,
    seed: number,
    steps: number,
  ) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [seed, setSeed] = useState(0);
  const [steps, setSteps] = useState(20);
  const [imageId, setImageId] = useState(images[0]?.id ?? "");
  // Périphérique de calcul annoncé avant de lancer : GPU (secondes) ou processeur (minutes).
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  useEffect(() => {
    let cancelled = false;
    inspirationApi.device().then(
      (d) => {
        if (!cancelled) setDevice(d);
      },
      () => {
        if (!cancelled) setDevice(null);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);
  const running = job?.status === "running";
  const gpu = device?.device === "cuda";
  const chosen = images.some((i) => i.id === imageId)
    ? imageId
    : (images[0]?.id ?? "");
  const image = last?.result.artifacts.find(
    (a) => a.name === "inspiration.png",
  );
  const control = last?.result.artifacts.find(
    (a) => a.name === "control_depth.png",
  );
  return (
    <section>
      <h2>Image d'inspiration (locale)</h2>
      <p className="muted">
        Générée par PlanDesign, sans service extérieur, par un modèle de
        diffusion guidé par la profondeur de la photo. C'est une inspiration{" "}
        <strong>générée</strong>, pas une photo retouchée ni un rendu des cotes.
        Poids à télécharger une fois (licence OpenRAIL-M à accepter) :{" "}
        <code>
          openplan models pull stable_diffusion_1_5@1.5 --accept-license
        </code>
        .
      </p>
      {images.length > 1 && (
        <label className="field">
          <span>Photo</span>
          <select value={chosen} onChange={(e) => setImageId(e.target.value)}>
            {images.map((i, n) => (
              <option key={i.id} value={i.id}>
                Photo {n + 1} ({i.width_px} × {i.height_px})
              </option>
            ))}
          </select>
          <em />
        </label>
      )}
      <textarea
        className="prompt"
        rows={3}
        value={prompt}
        placeholder="Style, matières, usage de la pièce (ex. chambre sous les toits, parquet clair, poutres apparentes)"
        onChange={(e) => setPrompt(e.target.value)}
      />
      <div className="row">
        <label className="field">
          <span>Graine</span>
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(e) => setSeed(Number(e.target.value) || 0)}
          />
          <em />
        </label>
        <label className="field">
          <span>Étapes</span>
          <select
            value={steps}
            onChange={(e) => setSteps(Number(e.target.value))}
          >
            <option value={12}>12 (rapide)</option>
            <option value={20}>20</option>
            <option value={30}>30 (fin)</option>
          </select>
          <em />
        </label>
      </div>
      {device && (
        <p className="muted">
          Calcul sur{" "}
          {gpu ? (
            <strong>GPU</strong>
          ) : (
            <strong className="warn">processeur</strong>
          )}{" "}
          ({device.label}) :{" "}
          {gpu
            ? "une dizaine de secondes par image."
            : "plusieurs minutes par image ; la page reste utilisable pendant ce temps."}
        </p>
      )}
      <button
        onClick={() => onInspire(chosen, prompt, seed, steps)}
        disabled={busy || running || !chosen}
      >
        {running ? "Génération en cours…" : "Générer une image d'inspiration"}
      </button>
      {job && job.status === "running" && (
        <p className="job-status" role="status">
          <span className="spinner" aria-hidden="true" /> {job.message}{" "}
          {Math.round(job.elapsed_s)} s écoulées
          {job.expected_s ? ` sur environ ${Math.round(job.expected_s)} s` : ""}
          .
        </p>
      )}
      {job && job.status === "failed" && (
        <p className="warn" role="alert">
          La génération a échoué : {job.error}
        </p>
      )}
      {last && (
        <div>
          <p className="muted">
            <span className="badge source-generated">générée</span>
            {last.result.model_ids.slice(1).join(" + ")} · graine {last.seed} ·{" "}
            {last.generation_s.toFixed(1)} s
            {last.peak_vram_mb
              ? ` · ${(last.peak_vram_mb / 1024).toFixed(1)} Go VRAM`
              : ""}
          </p>
          {image && (
            <a
              href={inspirationApi.artifactUrl(
                projectId,
                last.run_id,
                image.name,
              )}
              target="_blank"
              rel="noreferrer"
            >
              <img
                className="preview"
                alt="Image d'inspiration générée"
                src={inspirationApi.artifactUrl(
                  projectId,
                  last.run_id,
                  image.name,
                )}
              />
            </a>
          )}
          {control && (
            <img
              className="preview small"
              alt="Profondeur suivie"
              src={inspirationApi.artifactUrl(
                projectId,
                last.run_id,
                control.name,
              )}
            />
          )}
          {[
            ...last.result.assumptions.map((a) => a.text),
            ...last.result.warnings,
          ].map((t, i) => (
            <p key={i} className="warn">
              {t}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}

function PhotoItem({
  project,
  level,
  image,
  warnings,
  report,
  busy,
  selectedId,
  onSelect,
  onAdoptView,
  onRotateView,
  onRemove,
  onOp,
}: {
  project: Project;
  level: Level;
  image: ImageRef;
  warnings: string[] | undefined;
  report: ValidationReport | null;
  busy: boolean;
  selectedId: string | null;
  onSelect: (s: Selection) => void;
  onAdoptView: (cameraId: string) => void;
  onRotateView: (cameraId: string) => void;
  onRemove: (id: string) => void;
  onOp: (op: Op) => void;
}) {
  const [fetched, setFetched] = useState<string[] | null>(null);
  useEffect(() => {
    if (warnings !== undefined) return;
    let cancelled = false;
    api.imageQuality(project.id, image.id).then(
      (q) => {
        if (!cancelled) setFetched(q.warnings);
      },
      () => {
        if (!cancelled) setFetched([]);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [project.id, image.id, warnings]);
  const shown = warnings ?? fetched ?? [];
  const views = viewsOfImage(level, image.id);
  const viewing = views.some((v) => v.id === selectedId);
  const selectedRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (viewing) selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [viewing, selectedId]);
  const isBackground = level.background_image === image.id;
  const transform = readTransform(level.background_transform, image.width_px);
  const setTransform = (patch: Partial<BackgroundTransform>) =>
    onOp({
      op: "update_level",
      fields: {
        background_image: image.id,
        background_transform: { ...transform, ...patch },
      },
    });
  return (
    <li
      className={`photo${isBackground ? " active" : ""}${viewing ? " viewing" : ""}`}
    >
      <img
        src={api.imageFileUrl(project.id, image.id)}
        alt=""
        className="thumb"
      />
      <div className="photo-meta">
        <span className="muted">
          {image.width_px} × {image.height_px} px
          {image.focal_35mm
            ? ` · ${image.focal_35mm} mm éq.`
            : " · focale inconnue"}
          {image.exif_stripped ? " · EXIF retiré" : ""}
        </span>
        {shown.map((w, i) => (
          <span key={i} className="warn">
            {w}
          </span>
        ))}
        <div className="row">
          {isBackground ? (
            <button
              onClick={() =>
                onOp({
                  op: "update_level",
                  fields: { background_image: null, background_transform: {} },
                })
              }
            >
              Retirer du fond
            </button>
          ) : (
            <button onClick={() => setTransform({})}>
              Mettre en fond du plan
            </button>
          )}
          <button
            className="link danger"
            onClick={() => onRemove(image.id)}
            title="Supprimer le fichier du projet"
          >
            supprimer
          </button>
        </div>
        {views.map((camera) => (
          <div
            key={camera.id}
            className={`view${camera.id === selectedId ? " selected" : ""}`}
            ref={camera.id === selectedId ? selectedRef : undefined}
          >
            <ViewSummary camera={camera} />
            <div className="row">
              <button
                className="link"
                onClick={() => onSelect({ kind: "element", id: camera.id })}
                title="Sélectionne la caméra de cette photo dans le plan et montre son champ de vision"
              >
                Voir dans le plan
              </button>
            </div>
            <ViewActions
              camera={camera}
              violation={violationFor(report?.violations, camera.id)}
              busy={busy}
              onRotateView={onRotateView}
              onOp={onOp}
              onAdoptView={onAdoptView}
            />
          </div>
        ))}
        {isBackground && (
          <div className="background-controls">
            <Range
              label="Opacité"
              value={transform.opacity}
              min={0}
              max={1}
              step={0.05}
              onCommit={(v) => setTransform({ opacity: v })}
            />
            <Num
              label="Largeur"
              value={round(transform.scale * image.width_px, 2)}
              suffix="m"
              step={0.1}
              onCommit={(v) =>
                v > 0 && setTransform({ scale: v / image.width_px })
              }
            />
            <Num
              label="X"
              value={round(transform.tx, 2)}
              suffix="m"
              step={0.1}
              onCommit={(v) => setTransform({ tx: v })}
            />
            <Num
              label="Y"
              value={round(transform.ty, 2)}
              suffix="m"
              step={0.1}
              onCommit={(v) => setTransform({ ty: v })}
            />
            <Num
              label="Rotation"
              value={round((transform.rotation * 180) / Math.PI, 1)}
              suffix="°"
              step={1}
              onCommit={(v) => setTransform({ rotation: (v * Math.PI) / 180 })}
            />
          </div>
        )}
      </div>
    </li>
  );
}

function round(v: number, digits: number): number {
  const k = 10 ** digits;
  return Math.round(v * k) / k;
}

/** Champ numérique validé au blur ou sur Entrée (motif « valeur précédente »). */
function Num({
  label,
  value,
  onCommit,
  step,
  suffix,
}: {
  label: string;
  value: number;
  onCommit: (v: number) => void;
  step: number;
  suffix: string;
}) {
  const [text, setText] = useState(String(value));
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    setText(String(value));
  }
  const commit = () => {
    const v = Number(text.replace(",", "."));
    if (Number.isFinite(v) && v !== value) onCommit(v);
    else setText(String(value));
  };
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && commit()}
      />
      <em>{suffix}</em>
    </label>
  );
}

/** Curseur : aperçu local pendant le glisser, une seule commande à la fin. */
function Range({
  label,
  value,
  min,
  max,
  step,
  onCommit,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = useState(value);
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    setPrev(value);
    setDraft(value);
  }
  const commit = () => draft !== value && onCommit(draft);
  return (
    <label className="field">
      <span>{label}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={draft}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <em>{Math.round(draft * 100)} %</em>
    </label>
  );
}
