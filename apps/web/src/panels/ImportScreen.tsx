import { useRef, useState } from "react";
import {
  api,
  type AutoPlanResult,
  type JobStatus,
  type PlanImportPreview,
} from "../api/client";
import type { Level, Project } from "../api/types.gen";
import {
  IMPORT_ACCEPT,
  isPlanFile,
  jobSentence,
  PAPER_SCALES,
  PHOTO_TYPES,
  PLAN_KIND_LABEL,
} from "../importFiles";
import { photosMayLeave, walls } from "../model";
import { useHosting } from "../state/hosting";
import { IconUpload } from "../ui/icons";

interface Props {
  project: Project;
  level: Level;
  busy: boolean;
  /** Construction du plan en cours (tâche de fond) : étape lisible et durée annoncée. */
  autoJob: JobStatus | null;
  /** Dernier plan construit tout seul depuis une image. */
  lastAuto: AutoPlanResult | null;
  /** Dernier plan vectoriel lu (DXF, PDF, SVG), déjà devenu des murs. */
  planPreview: PlanImportPreview | null;
  /** Les murs viennent de ce plan et rien n'a changé depuis : calques et échelle papier
   *  peuvent encore être corrigés (les murs sont refaits). */
  planEditable: boolean;
  /** Calques retenus pour les murs du plan lu. */
  planLayers: string[];
  onImport: (file: File) => void;
  onRescale: (paperScale: number) => void;
  onLayers: (layers: string[]) => void;
  onContinue: () => void;
}

const METHOD_SENTENCE: Record<string, string> = {
  lines: "Plan construit depuis les lignes de la photo.",
  depth: "Plan construit depuis les distances estimées sur la photo.",
  attic: "Combles construits depuis les pentes de toit vues sur la photo.",
  plan_image: "Murs créés depuis l'image du plan.",
  view: "Photo comparée au plan existant.",
  none: "Aucun mur créé depuis cette image.",
};

/** « Ajouter une source » au projet courant (P1 : ce n'est plus une étape) : on dépose une
 *  photo de la pièce, l'image d'un plan ou un plan vectoriel (DXF, PDF, SVG) et le plan se
 *  construit tout seul, sans question ni choix de méthode. Tout est estimé et se corrige dans
 *  l'onglet Plan (« Recalibrer » pour l'échelle). La mention de confidentialité suit l'accord
 *  réel du projet (A1). */
export function ImportScreen({
  project,
  level,
  busy,
  autoJob,
  lastAuto,
  planPreview,
  planEditable,
  planLayers,
  onImport,
  onRescale,
  onLayers,
  onContinue,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const online = Boolean(useHosting()?.online);
  const images = Object.values(project.images);
  const wallCount = walls(level).length;
  const running = autoJob?.status === "running";
  const blocked = busy || running;

  const take = (file: File) => {
    setMessage(null);
    if (isPlanFile(file) || PHOTO_TYPES.includes(file.type)) {
      onImport(file);
      return;
    }
    setMessage(
      "Formats acceptés : JPG, PNG, WEBP (photo ou image de plan), DXF, PDF vectoriel, SVG.",
    );
  };
  const kindLabel = planPreview ? PLAN_KIND_LABEL[planPreview.kind] : "";
  const size = planPreview?.bounds
    ? `${(planPreview.bounds.max.x - planPreview.bounds.min.x).toFixed(2)} × ${(planPreview.bounds.max.y - planPreview.bounds.min.y).toFixed(2)} m`
    : null;
  const chosen = new Set(planLayers);

  return (
    <div className="import-screen">
      <div className="import-card">
        <h1>Ajouter une source</h1>
        <p className="muted">
          Déposez une <strong>photo</strong> de la pièce ou un{" "}
          <strong>plan</strong> (image, DXF, PDF vectoriel, SVG) : le plan se
          construit tout seul, en mètres, et se corrige ensuite dans le plan.{" "}
          {photosMayLeave(project)
            ? `Les photos peuvent partir chez le modèle distant autorisé dans Paramètres avancés › IA ; les plans ${online ? "sont gardés sur le serveur PlanDesign" : "restent sur cette machine"}.`
            : online
              ? "Photos et plans sont gardés sur le serveur PlanDesign ; rien n'est envoyé ailleurs."
              : "Tout reste sur cette machine."}
        </p>
        <div
          className={`dropzone${over ? " over" : ""}${blocked ? " busy" : ""}${planPreview || images.length > 0 ? " compact" : ""}`}
          role="button"
          tabIndex={0}
          aria-label="Déposer une photo ou un plan, ou cliquer pour choisir un fichier"
          onClick={() => !blocked && fileRef.current?.click()}
          onKeyDown={(e) => {
            if ((e.key === "Enter" || e.key === " ") && !blocked)
              fileRef.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !blocked) take(file);
          }}
        >
          <span className="icon-disc">
            <IconUpload />
          </span>
          <strong>
            {busy
              ? "Import en cours…"
              : running
                ? "Construction du plan…"
                : "Glissez-déposez, ou cliquez pour choisir"}
          </strong>
          <span className="muted">
            Photo ou image de plan JPG, PNG, WEBP · plan DXF, PDF, SVG · jusqu'à
            50 Mo · EXIF retiré
          </span>
          <input
            ref={fileRef}
            type="file"
            accept={IMPORT_ACCEPT}
            disabled={blocked}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) take(file);
              e.target.value = "";
            }}
          />
        </div>
        {/* Téléphone seulement (masqué sur ordinateur par la feuille de style). */}
        <div className="row capture-row">
          <button
            type="button"
            disabled={blocked}
            onClick={() => cameraRef.current?.click()}
            title="Ouvre l'appareil photo ; la photo garde sa focale (EXIF) pour l'estimation, le reste des EXIF est retiré à l'import"
          >
            Prendre une photo
          </button>
          <input
            ref={cameraRef}
            type="file"
            accept="image/*"
            capture="environment"
            aria-label="Prendre une photo avec l'appareil"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) take(file);
              e.target.value = "";
            }}
          />
        </div>
        {message && <p className="warn">{message}</p>}

        {autoJob && running && (
          <section className="auto-progress" role="status">
            <p className="job-status">
              <span className="spinner" aria-hidden="true" />{" "}
              <strong>Construction du plan</strong> : {jobSentence(autoJob)}
            </p>
            <p className="muted">
              Vous pouvez déjà passer au plan : le plan apparaîtra dès qu'il est
              prêt.
            </p>
          </section>
        )}
        {autoJob?.status === "failed" && (
          <p className="warn" role="alert">
            Le plan n'a pas pu être construit : {autoJob.error}. La photo reste
            dans le projet ; vous pouvez tracer les murs à la main.
          </p>
        )}
        {lastAuto && !running && (
          <section className="auto-summary">
            <h2>Plan construit</h2>
            <p>
              {METHOD_SENTENCE[lastAuto.run.method] ?? ""}{" "}
              <span className="muted">
                Tout est estimé : vérifiez une longueur connue avec « Recalibrer
                ».
              </span>
            </p>
            {lastAuto.run.notices.map((n, i) => (
              <p key={i} className="muted notice">
                {n}
              </p>
            ))}
          </section>
        )}

        {planPreview && (
          <section className="plan-preview">
            <h2>Plan {kindLabel} lu</h2>
            <p className="muted">
              {planPreview.entity_count} tracé(s) · unités : {planPreview.units}
              {size ? ` · emprise ${size}` : ""}
              {planPreview.merged_pairs > 0
                ? ` · ${planPreview.merged_pairs} mur(s) en double trait fusionné(s) avec leur épaisseur`
                : ""}
            </p>
            {planPreview.warnings.map((w, i) => (
              <p key={i} className="warn">
                {w}
              </p>
            ))}
            {planEditable ? (
              <details className="plan-fix">
                <summary>Corriger la lecture du plan</summary>
                {planPreview.paper_scale != null && (
                  <label className="field">
                    <span>Échelle papier</span>
                    <select
                      value={
                        PAPER_SCALES.includes(planPreview.paper_scale)
                          ? planPreview.paper_scale
                          : ""
                      }
                      onChange={(e) => onRescale(Number(e.target.value))}
                      disabled={blocked}
                      title="Le plan est dessiné à cette échelle sur le papier ; changez-la si l'emprise paraît fausse : les murs sont refaits"
                    >
                      {!PAPER_SCALES.includes(planPreview.paper_scale) && (
                        <option value="">1:{planPreview.paper_scale}</option>
                      )}
                      {PAPER_SCALES.map((s) => (
                        <option key={s} value={s}>
                          1:{s}
                        </option>
                      ))}
                    </select>
                    <em>{size ?? ""}</em>
                  </label>
                )}
                <ul className="list layers">
                  {planPreview.layers.map((layer) => (
                    <li key={layer.name}>
                      <label>
                        <input
                          type="checkbox"
                          checked={chosen.has(layer.name)}
                          disabled={blocked}
                          onChange={(e) => {
                            const next = new Set(chosen);
                            if (e.target.checked) next.add(layer.name);
                            else next.delete(layer.name);
                            if (next.size > 0) onLayers([...next]);
                          }}
                        />{" "}
                        calque <code>{layer.name}</code> · {layer.segments}{" "}
                        segment
                        {layer.segments > 1 ? "s" : ""}
                      </label>
                    </li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="muted">
                Le plan a été modifié depuis : corrigez-le dans le plan.
              </p>
            )}
          </section>
        )}

        {images.length > 0 && (
          <section>
            <h2>Images importées</h2>
            <ul className="import-photos">
              {images.map((image, i) => (
                <li key={image.id}>
                  <img src={api.imageFileUrl(project.id, image.id)} alt="" />
                  <div>
                    <span className="muted">
                      image {i + 1} · {image.width_px} × {image.height_px} px
                      {image.exif_stripped ? " · EXIF retiré" : ""}
                      {level.background_image === image.id
                        ? " · en fond du plan"
                        : ""}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="row end">
          <span className="muted">
            {wallCount > 0
              ? `${wallCount} mur${wallCount > 1 ? "s" : ""} dans le plan`
              : running
                ? "Le plan arrive"
                : "Vous pouvez aussi tracer les murs à la main"}
          </span>
          <button className="cta" onClick={onContinue}>
            Continuer vers le plan
          </button>
        </div>
      </div>
    </div>
  );
}
