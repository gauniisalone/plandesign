import { useEffect, useState } from "react";
import type {
  DimensionsBody,
  DimensionsSummary,
  RecalibrateBody,
} from "../api/client";
import type { Point2 } from "../api/types.gen";
import { decimal, metres, typed } from "../format";

interface Props {
  /** Dans le panneau de droite (hors du plan) ; sinon flottante, en bas à droite. */
  docked: boolean;
  /** Les deux points cliqués sur le plan, quand ils le sont déjà. */
  measure: { a: Point2; b: Point2 } | null;
  /** Hauteur sous plafond courante du niveau, pour la troisième entrée. */
  ceilingHeight: number;
  busy: boolean;
  onApply: (body: RecalibrateBody) => void;
  /** R3 : aperçu des dimensions (résumé, ou message d'erreur) et application. */
  onPreviewDimensions: (
    body: DimensionsBody,
  ) => Promise<DimensionsSummary | string>;
  onApplyDimensions: (body: DimensionsBody) => void;
  onCancel: () => void;
}

const MIN_REAL_M = 0.3;
const MAX_REAL_M = 30;
const MIN_CEILING_M = 1.8;
const MAX_CEILING_M = 6;

function parse(text: string): number | null {
  const v = Number(text.replace(",", "."));
  return Number.isFinite(v) && v > 0 ? v : null;
}

/** « Recalibrer », seul outil de correction d'échelle : deux clics sur une longueur connue du
 *  plan (murs, fond de plan, meubles, caméras suivent ensemble), ou la hauteur sous plafond
 *  quand c'est ce que l'on connaît. Un mur se recalibre depuis sa fiche. L'effet est annoncé
 *  avant d'appliquer, une valeur aberrante est signalée. */
export function RecalibrateCard({
  docked,
  measure,
  ceilingHeight,
  busy,
  onApply,
  onPreviewDimensions,
  onApplyDimensions,
  onCancel,
}: Props) {
  const [text, setText] = useState("");
  const [ceilingText, setCeilingText] = useState("");
  const [lengthText, setLengthText] = useState("");
  const [widthText, setWidthText] = useState("");
  const [current, setCurrent] = useState<DimensionsSummary | null>(null);
  const [preview, setPreview] = useState<DimensionsSummary | string | null>(
    null,
  );
  const measured = measure
    ? Math.hypot(measure.b.x - measure.a.x, measure.b.y - measure.a.y)
    : 0;
  const real = parse(text);
  const ceiling = parse(ceilingText);
  const factor = real && measured > 1e-6 ? real / measured : null;
  const warnings: string[] = [];
  if (real && (real < MIN_REAL_M || real > MAX_REAL_M))
    warnings.push(
      `${metres(real)} est une longueur inhabituelle pour une cote de pièce (entre ${typed(MIN_REAL_M)} et ${typed(MAX_REAL_M)} m d'ordinaire) : vérifiez l'unité.`,
    );
  if (factor && (factor < 0.2 || factor > 5))
    warnings.push(
      `Le plan serait multiplié par ${decimal(factor)} : c'est beaucoup, vérifiez les deux points cliqués.`,
    );
  if (ceiling && (ceiling < MIN_CEILING_M || ceiling > MAX_CEILING_M))
    warnings.push(
      `${metres(ceiling)} est une hauteur sous plafond inhabituelle (entre ${typed(MIN_CEILING_M)} et ${typed(MAX_CEILING_M)} m d'ordinaire).`,
    );
  const applyDistance = () => {
    if (measure && real) onApply({ real_m: real, a: measure.a, b: measure.b });
  };
  const length = parse(lengthText);
  const width = parse(widthText);
  const dims: DimensionsBody = {
    length_m: length,
    width_m: width,
    height_m: ceiling,
  };
  const anyDimension = length !== null || width !== null || ceiling !== null;
  // Dimensions actuelles à l'ouverture (repères des champs), puis aperçu à chaque saisie.
  useEffect(() => {
    void onPreviewDimensions({}).then((r) => {
      if (typeof r !== "string") setCurrent(r);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!anyDimension) {
      setPreview(null);
      return;
    }
    const timer = window.setTimeout(() => {
      void onPreviewDimensions(dims).then(setPreview);
    }, 200);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lengthText, widthText, ceilingText]);
  const previewOk = preview !== null && typeof preview !== "string";
  const applyDimensions = () => {
    if (anyDimension && previewOk) onApplyDimensions(dims);
  };
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") applyDimensions();
    if (e.key === "Escape") onCancel();
  };
  return (
    <div
      className={docked ? "card scale-card docked" : "float card scale-card"}
      role="dialog"
      aria-label="Recalibrer"
    >
      <h3>Recalibrer le plan</h3>
      {measure ? (
        <>
          <p className="muted">
            Sur le plan, cette longueur mesure aujourd'hui{" "}
            <strong>{metres(measured)}</strong>. Quelle est sa longueur réelle ?
          </p>
          <div className="row">
            <input
              autoFocus
              type="text"
              inputMode="decimal"
              placeholder="distance réelle en m (ex. 3,20)"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyDistance();
                if (e.key === "Escape") onCancel();
              }}
            />
          </div>
          {factor && (
            <p className="muted preview-scale">
              Tout le plan (murs, fond, meubles, vues) sera multiplié par{" "}
              {decimal(factor, 3)}.
            </p>
          )}
        </>
      ) : (
        <p className="muted">
          Cliquez les deux bouts d'une longueur que vous connaissez sur le plan
          (un mur, une porte, un meuble sur le fond), ou donnez une ou plusieurs
          dimensions de la pièce.
        </p>
      )}
      <div className="dims" role="group" aria-label="Dimensions de la pièce">
        <label>
          <span>Longueur (m)</span>
          <input
            type="text"
            inputMode="decimal"
            aria-label="Longueur de la pièce"
            placeholder={current ? decimal(current.before.length_m) : ""}
            value={lengthText}
            onChange={(e) => setLengthText(e.target.value)}
            onKeyDown={keys}
          />
        </label>
        <label>
          <span>Largeur (m)</span>
          <input
            type="text"
            inputMode="decimal"
            aria-label="Largeur de la pièce"
            placeholder={current ? decimal(current.before.width_m) : ""}
            value={widthText}
            onChange={(e) => setWidthText(e.target.value)}
            onKeyDown={keys}
          />
        </label>
        <label>
          <span>Hauteur (m)</span>
          <input
            type="text"
            inputMode="decimal"
            aria-label="Hauteur sous plafond"
            placeholder={decimal(ceilingHeight)}
            title={`Aujourd'hui ${metres(ceilingHeight)}`}
            value={ceilingText}
            onChange={(e) => setCeilingText(e.target.value)}
            onKeyDown={keys}
          />
        </label>
      </div>
      {previewOk && (
        <p className="muted preview-scale" role="status">
          <strong>{preview.text}</strong>
          {preview.uniform
            ? ` · tout le plan × ${decimal(preview.sx, 3)}`
            : " · murs, ouvertures, meubles, vues et fond suivent"}
        </p>
      )}
      {typeof preview === "string" && <p className="warn">{preview}</p>}
      {warnings.map((w, i) => (
        <p key={i} className="warn">
          {w}
        </p>
      ))}
      <div className="row">
        {measure && (
          <button
            className="primary"
            disabled={busy || !real}
            onClick={applyDistance}
          >
            Appliquer
          </button>
        )}
        <button
          className={measure ? "" : "primary"}
          disabled={busy || !anyDimension || !previewOk}
          onClick={applyDimensions}
        >
          Appliquer les dimensions
        </button>
        <button className="link" onClick={onCancel}>
          annuler
        </button>
      </div>
    </div>
  );
}
