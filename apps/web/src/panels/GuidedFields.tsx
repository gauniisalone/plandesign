import type { GuidedAnswers } from "../api/client";
import { FURNITURE, LIGHTS, MOODS, SPACES, STYLES } from "../guided";

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
  const number = (value: string, max: number) => {
    if (value.trim() === "") return null;
    const n = Number(value.replace(",", "."));
    return Number.isFinite(n) ? Math.min(max, Math.max(0, n)) : null;
  };
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
        <label>
          Surface (m²)
          <input
            inputMode="decimal"
            placeholder="ex. 60"
            disabled={disabled}
            value={answers.area_m2 ?? ""}
            onChange={(e) => {
              const n = number(e.target.value, 400);
              onAnswer({ area_m2: n !== null && n >= 8 ? n : null });
            }}
          />
        </label>
        <label>
          Chambres
          <input
            inputMode="numeric"
            placeholder="ex. 2"
            disabled={disabled}
            value={answers.bedrooms ?? ""}
            onChange={(e) => {
              const n = number(e.target.value, 6);
              onAnswer({ bedrooms: n === null ? null : Math.round(n) });
            }}
          />
        </label>
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
