import { useState } from "react";
import type { GuidedAnswers } from "../api/client";
import { typed } from "../format";
import { FURNITURE, LIGHTS, MOODS, SPACES, STYLES } from "../guided";

/** Bornes des réponses guidées (`GuidedAnswers`, openplan/schemas/home.py). */
const AREA_MIN = 8;
const AREA_MAX = 400;
const BEDROOMS_MAX = 6;

/** Ce que le champ montre pour une valeur gardée : la virgule française. */
function shown(value: number | null): string {
  return value === null ? "" : typed(value);
}

/** Lecture d'une saisie : « 35 », « 35,5 », « 35.5 » ; null si ce n'est pas un nombre. */
function parse(text: string, decimal: boolean): number | null {
  const t = text.trim().replace(",", ".");
  const ok = decimal ? /^\d+(\.\d*)?$|^\.\d+$/ : /^\d+$/;
  if (!ok.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** Champ numérique des Questions guidées (retour d'augus, 19 oct.) : le texte tapé est gardé
 *  tel quel (« 3 » en route vers « 35 », « 35, » en route vers « 35,5 ») ; une valeur dans les
 *  bornes part dès la frappe, la validation se fait à la sortie du champ (message clair hors
 *  bornes, la réponse gardée est alors effacée). Avant, le champ était contrôlé par la valeur
 *  numérique bornée : toute saisie partielle sous 8 m² était effacée à chaque touche. */
function NumberField({
  label,
  placeholder,
  decimal = false,
  min,
  max,
  invalid,
  value,
  disabled,
  onValue,
  onError,
}: {
  label: string;
  placeholder: string;
  decimal?: boolean;
  min: number;
  max: number;
  invalid: string;
  value: number | null;
  disabled: boolean;
  onValue: (value: number | null) => void;
  onError: (message: string | null) => void;
}) {
  const [text, setText] = useState(() => shown(value));
  const [bad, setBad] = useState(false);
  // La dernière valeur envoyée : une valeur venue d'ailleurs (recalage, autre onglet, autre
  // membre) remplace le texte ; la nôtre, non (le texte tapé reste celui de l'utilisateur).
  const [sent, setSent] = useState<number | null>(value);
  if (value !== sent) {
    setSent(value);
    setText(shown(value));
    setBad(false);
  }
  const valid = (t: string): number | null | undefined => {
    if (t.trim() === "") return null;
    const n = parse(t, decimal);
    if (n === null || n < min || n > max) return undefined;
    return decimal ? Math.round(n * 10) / 10 : n;
  };
  const send = (n: number | null) => {
    if (n === sent) return;
    setSent(n);
    onValue(n);
  };
  return (
    <label>
      {label}
      <input
        inputMode={decimal ? "decimal" : "numeric"}
        placeholder={placeholder}
        disabled={disabled}
        value={text}
        aria-invalid={bad || undefined}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          const n = valid(t);
          if (n !== undefined) {
            send(n);
            if (bad) {
              setBad(false);
              onError(null);
            }
          }
        }}
        onBlur={() => {
          const n = valid(text);
          if (n === undefined) {
            setBad(true);
            onError(invalid);
            send(null);
          } else {
            setBad(false);
            onError(null);
            // Arrondi au dixième : le champ montre ce qui est enregistré (35,55 → 35,6).
            if (n !== null && parse(text, decimal) !== n) setText(shown(n));
            send(n);
          }
        }}
      />
    </label>
  );
}

/** Questions guidées (L2) : quatre questions, puis « Plus d'options » replié. Chaque réponse
 *  passe en choix de l'utilisateur et prime sur le texte. Partagées par l'accueil et la note
 *  Exigences du tableau (T1). */
export function GuidedFields({
  answers,
  disabled,
  onAnswer,
}: {
  answers: GuidedAnswers;
  disabled: boolean;
  onAnswer: (patch: Partial<GuidedAnswers>) => void;
}) {
  const space =
    SPACES.find(
      (s) =>
        s.space === (answers.space ?? null) &&
        (s.room ?? null) ===
          (answers.space === "piece" ? answers.room_type : null),
    )?.value ?? "";
  const [errors, setErrors] = useState<Record<string, string>>({});
  const fieldErrors = Object.values(errors);
  const reportError = (field: string) => (message: string | null) =>
    setErrors((current) => {
      if ((current[field] ?? null) === message) return current;
      const next = { ...current };
      if (message) next[field] = message;
      else delete next[field];
      return next;
    });
  const toggle = (list: string[] | undefined, value: string, cap: number) => {
    const current = list ?? [];
    if (current.includes(value)) return current.filter((v) => v !== value);
    return [...current, value].slice(-cap);
  };
  return (
    <div className="home-guided">
      <div className="home-grid">
        <label>
          Quel espace ?
          <select
            value={space}
            disabled={disabled}
            onChange={(e) => {
              const s = SPACES.find((x) => x.value === e.target.value);
              onAnswer({ space: s?.space ?? null, room_type: s?.room ?? null });
            }}
          >
            {SPACES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <NumberField
          label="Surface (m²)"
          placeholder="ex. 60"
          decimal
          min={AREA_MIN}
          max={AREA_MAX}
          invalid={`Entre ${AREA_MIN} et ${AREA_MAX} m².`}
          value={answers.area_m2 ?? null}
          disabled={disabled}
          onValue={(n) => onAnswer({ area_m2: n })}
          onError={reportError("area")}
        />
        <NumberField
          label="Chambres"
          placeholder="ex. 2"
          min={0}
          max={BEDROOMS_MAX}
          invalid={`Chambres : un nombre entier de 0 à ${BEDROOMS_MAX}.`}
          value={answers.bedrooms ?? null}
          disabled={disabled}
          onValue={(n) => onAnswer({ bedrooms: n })}
          onError={reportError("bedrooms")}
        />
        <label>
          Style
          <select
            value={answers.style ?? ""}
            disabled={disabled}
            onChange={(e) => onAnswer({ style: e.target.value || null })}
          >
            {STYLES.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* Retour d'augus (19 oct.) : un nombre hors bornes le dit à la sortie du champ, sous
          la grille, en une ligne. */}
      {fieldErrors.length > 0 && (
        <p className="home-field-error" role="alert">
          {fieldErrors.join(" ")}
        </p>
      )}
      <details className="home-options">
        <summary>Plus d'options</summary>
        <div className="home-grid">
          <label>
            Format des images
            <select
              value={answers.aspect ?? ""}
              disabled={disabled}
              onChange={(e) =>
                onAnswer({
                  aspect: (e.target.value || null) as GuidedAnswers["aspect"],
                })
              }
            >
              <option value="">Laisser choisir</option>
              <option value="4:3">Paysage 4:3</option>
              <option value="4:5">Portrait 4:5</option>
            </select>
          </label>
          <label>
            Lumière
            <select
              value={answers.light_time ?? ""}
              disabled={disabled}
              onChange={(e) =>
                onAnswer({
                  light_time: (e.target.value ||
                    null) as GuidedAnswers["light_time"],
                })
              }
            >
              {LIGHTS.map((l) => (
                <option key={l.value} value={l.value}>
                  {l.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ChipGroup
          name="Ambiance (deux au plus)"
          options={MOODS}
          chosen={answers.mood ?? []}
          disabled={disabled}
          onToggle={(v) => onAnswer({ mood: toggle(answers.mood, v, 2) })}
        />
        <ChipGroup
          name="Meubles voulus"
          options={FURNITURE}
          chosen={answers.furniture ?? []}
          disabled={disabled}
          onToggle={(v) =>
            onAnswer({
              furniture: toggle(answers.furniture, v, 8),
              avoid: (answers.avoid ?? []).filter((x) => x !== v),
            })
          }
        />
        <ChipGroup
          name="À éviter"
          options={FURNITURE}
          chosen={answers.avoid ?? []}
          disabled={disabled}
          onToggle={(v) =>
            onAnswer({
              avoid: toggle(answers.avoid, v, 8),
              furniture: (answers.furniture ?? []).filter((x) => x !== v),
            })
          }
        />
      </details>
    </div>
  );
}

function ChipGroup({
  name,
  options,
  chosen,
  disabled,
  onToggle,
}: {
  name: string;
  options: { value: string; label: string }[];
  chosen: string[];
  disabled: boolean;
  onToggle: (value: string) => void;
}) {
  return (
    <div className="home-chips-row" role="group" aria-label={name}>
      <span className="muted">{name}</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={chosen.includes(o.value)}
          disabled={disabled}
          onClick={() => onToggle(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
