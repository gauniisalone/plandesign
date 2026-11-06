import { STYLE_LABELS } from "../intent";
import {
  DRAWS_MAX,
  DRAWS_MIN,
  focalChoices,
  type RenderSettings,
} from "./renderSettings";

/** KAN-134 (maquette 45d0d6d, réponse 3 d'augus) : « Réglages des rendus », ouverts par
 *  l'icône curseurs du node Rendus en ligne. Ambiance (le style des pièces, dans le plan,
 *  annulable), focale des vues de pièce et nombre de tirages par pièce (dans le navigateur).
 *  En local, l'icône ouvre toujours le volet du rendu Blender. */
export function RenderSettingsPanel({
  settings,
  style,
  canEdit,
  hasRoomViews,
  busy,
  onChange,
  onStyle,
  onClose,
}: {
  settings: RenderSettings;
  /** Le style commun des pièces ; null : elles diffèrent. */
  style: string | null;
  canEdit: boolean;
  hasRoomViews: boolean;
  busy: boolean;
  onChange: (next: RenderSettings) => void;
  /** Absent : aucune pièce fermée (l'ambiance se règle par pièce). */
  onStyle?: (style: string) => void;
  onClose?: () => void;
}) {
  const draws = Array.from(
    { length: DRAWS_MAX - DRAWS_MIN + 1 },
    (_, k) => DRAWS_MIN + k,
  );
  return (
    <section className="rgal-settings" aria-label="Réglages des rendus">
      <header className="rgal-settings-head">
        <b>Réglages des rendus</b>
        {onClose && (
          <button
            type="button"
            className="icon rgal-push"
            aria-label="Fermer les réglages"
            data-tip="Fermer les réglages"
            onClick={onClose}
          >
            ×
          </button>
        )}
      </header>
      {!canEdit ? (
        <p className="rgal-hint" role="note">
          Les réglages des rendus se changent avec le droit de modifier le projet.
        </p>
      ) : (
        <>
          <label className="rgal-settings-row">
            <span>Ambiance</span>
            <select
              aria-label="Ambiance des pièces"
              value={style ?? ""}
              disabled={!onStyle || busy}
              onChange={(e) => onStyle?.(e.target.value)}
            >
              {style === null && (
                <option value="" disabled>
                  {onStyle ? "Plusieurs ambiances" : "Aucune pièce fermée"}
                </option>
              )}
              {Object.entries(STYLE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <p className="rgal-hint">
            Toutes les pièces du plan prennent ce style (annulable). Les rendus
            existants ne changent pas.
          </p>
          <label className="rgal-settings-row">
            <span>Focale</span>
            <select
              aria-label="Focale des vues de pièce"
              value={settings.focal === null ? "auto" : String(settings.focal)}
              disabled={busy}
              onChange={(e) =>
                onChange({
                  ...settings,
                  focal: e.target.value === "auto" ? null : Number(e.target.value),
                })
              }
            >
              {focalChoices().map((c) => (
                <option
                  key={c.label}
                  value={c.value === null ? "auto" : String(c.value)}
                >
                  {c.label}
                </option>
              ))}
            </select>
          </label>
          <p className="rgal-hint">
            {hasRoomViews
              ? "Les vues calculées sont recalculées à ce réglage ; une vue modifiée à la main garde la sienne."
              : "Pris au calcul des vues par pièce."}
          </p>
          <label className="rgal-settings-row">
            <span>Nombre de rendus</span>
            <select
              aria-label="Nombre de rendus par pièce"
              value={settings.draws}
              onChange={(e) =>
                onChange({ ...settings, draws: Number(e.target.value) })
              }
            >
              {draws.map((n) => (
                <option key={n} value={n}>
                  {n} par pièce
                </option>
              ))}
            </select>
          </label>
          <p className="rgal-hint">
            Chaque rendu est pris sur le quota du jour. Ces deux réglages restent
            dans ce navigateur.
          </p>
        </>
      )}
    </section>
  );
}
